import argparse
import json
import os
from pathlib import Path
import re
import sys
import time
from typing import Callable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from google import genai
from google.genai import types
from retriever import SymbolicGraphRetriever
from symbol_resolver import (
    GDScriptLightweightAnalyzer,
    GodotSymbolResolver,
)


class AssistantStatus:
    IDLE = "IDLE"
    GENERATING = "GENERATING"
    STREAMING_RESPONSE = "STREAMING_RESPONSE"
    VALIDATING = "VALIDATING"
    SELF_CORRECTING = "SELF_CORRECTING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class GodotCodeAssistant:
    Status = AssistantStatus

    STATUS_I18N = {
        "en": {
            AssistantStatus.IDLE: "Idle",
            AssistantStatus.GENERATING: "Generating initial GDScript draft...",
            AssistantStatus.STREAMING_RESPONSE: "Streaming response... ({chars} chars / {lines} lines)",
            AssistantStatus.VALIDATING: "Validating against Godot 4 symbols (Extends: {target})...",
            AssistantStatus.SELF_CORRECTING: "Refining with graph lineage [{target}]: unverified [{items}] (Turn {turn})",
            AssistantStatus.COMPLETED: "Code generated and statically verified.",
            AssistantStatus.FAILED: "Failed: {error}",
        },
        "zh": {
            AssistantStatus.IDLE: "空闲",
            AssistantStatus.GENERATING: "正在生成初版 GDScript 代码...",
            AssistantStatus.STREAMING_RESPONSE: "正在输出代码... (已生成 {chars} 字符 / {lines} 行)",
            AssistantStatus.VALIDATING: "正在结合 Godot 4 继承树与符号库进行静态校验 (基类: {target})...",
            AssistantStatus.SELF_CORRECTING: "打回修正: 基于 {target} 继承链修复未定义符号 [{items}] (第 {turn} 次)",
            AssistantStatus.COMPLETED: "代码生成完成并通过静态校验。",
            AssistantStatus.FAILED: "执行失败: {error}",
        },
    }

    GODOT_ANNOTATIONS = {
        "@export",
        "@export_category",
        "@export_group",
        "@export_subgroup",
        "@export_range",
        "@export_enum",
        "@export_file",
        "@export_dir",
        "@export_global_file",
        "@export_global_dir",
        "@export_multiline",
        "@export_placeholder",
        "@export_flags",
        "@export_flags_2d_physics",
        "@export_flags_2d_render",
        "@export_flags_3d_physics",
        "@export_flags_3d_render",
        "@export_exp_easing",
        "@export_color_no_alpha",
        "@export_node_path",
        "@export_custom",
        "@onready",
        "@tool",
        "@icon",
        "@rpc",
        "@warning_ignore",
    }

    def __init__(
        self,
        api_key: str | None = None,
        config_path: str = "config.json",
        graph_path: str = "assets/godot4_api_graph.json",
        kb_path: str = "assets/godot_enriched_kb.json",
        project_root: str | Path | None = None,
        model_name: str = "gemini-2.5-flash",
        language: str = "zh",
        thinking_budget: Optional[int] = 512,
    ):
        self.retriever = SymbolicGraphRetriever(graph_path)
        self.model_name = model_name
        self.language = (
            language.lower() if language.lower() in ("en", "zh") else "zh"
        )
        self.thinking_budget = thinking_budget
        self.current_status: str = AssistantStatus.IDLE

        resolved_key = api_key
        cfg_file = Path(config_path)
        if not resolved_key and cfg_file.exists():
            try:
                with open(cfg_file, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                    resolved_key = cfg.get("api_key")
                    self.model_name = cfg.get("model_name", self.model_name)
                    self.language = cfg.get("language", self.language)
                    self.thinking_budget = cfg.get(
                        "thinking_budget", self.thinking_budget
                    )
                    if not project_root:
                        project_root = cfg.get("project_root")
            except Exception as e:
                print(f"[Warning] Failed to read {config_path}: {e}")

        if not resolved_key:
            resolved_key = os.environ.get("GEMINI_API_KEY")

        if not resolved_key:
            raise ValueError(
                "No Gemini API key provided. Check config.json or environment variables."
            )

        self.client = genai.Client(api_key=resolved_key)

        root_dir = Path(__file__).resolve().parent.parent
        resolved_kb_path = (
            Path(kb_path)
            if Path(kb_path).is_absolute()
            else root_dir / kb_path
        )
        self.resolver = GodotSymbolResolver(
            kb_path=resolved_kb_path, project_root=project_root
        )
        self.analyzer = GDScriptLightweightAnalyzer(self.resolver)

    def set_project_root(self, project_root: str | Path):
        self.resolver.set_project_root(project_root)

    def _get_localized_status(self, status: str, **kwargs) -> str:
        lang_dict = self.STATUS_I18N.get(self.language, self.STATUS_I18N["en"])
        template = lang_dict.get(status, status)
        try:
            return template.format(**kwargs)
        except Exception:
            return template

    def _update_status(
        self,
        new_status: str,
        detail: str = "",
        callback: Optional[Callable[[str, str], None]] = None,
    ):
        self.current_status = new_status
        status_msg = f"[{new_status}]" + (f" {detail}" if detail else "")
        print(f">> State Changed: {status_msg}", flush=True)
        if callback:
            try:
                callback(new_status, detail)
            except Exception as e:
                print(f"[Warning] Status callback error: {e}")

    def _stream_chat_with_retry(
        self,
        chat_session,
        message_content: str,
        status_hook=None,
        max_retries: int = 4,
    ) -> str:
        backoff_delays = [3, 8, 15, 25]
        for attempt in range(max_retries):
            try:
                full_text = ""
                start_connect_time = time.time()
                last_update_time = time.time()

                if status_hook:
                    connecting_msg = (
                        "正在连接服务并等待首字响应（模型推理中）..."
                        if self.language == "zh"
                        else "Handshaking and waiting for first token (reasoning)..."
                    )
                    self._update_status(
                        AssistantStatus.STREAMING_RESPONSE,
                        connecting_msg,
                        status_hook,
                    )

                response_stream = chat_session.send_message_stream(
                    message_content
                )
                has_received_first_chunk = False

                for chunk in response_stream:
                    text_piece = chunk.text or ""
                    if text_piece:
                        if not has_received_first_chunk:
                            has_received_first_chunk = True
                            first_token_cost = round(
                                time.time() - start_connect_time, 2
                            )
                            ttfb_info = (
                                f">> 收到首字响应！首字节耗时 (TTFB): {first_token_cost}s"
                                if self.language == "zh"
                                else f">> First token received! TTFB: {first_token_cost}s"
                            )
                            print(ttfb_info, flush=True)

                        full_text += text_piece
                        now = time.time()
                        if status_hook and (now - last_update_time >= 0.25):
                            line_count = full_text.count("\n") + 1
                            progress_detail = self._get_localized_status(
                                AssistantStatus.STREAMING_RESPONSE,
                                chars=len(full_text),
                                lines=line_count,
                            )
                            self._update_status(
                                AssistantStatus.STREAMING_RESPONSE,
                                progress_detail,
                                status_hook,
                            )
                            last_update_time = now

                line_count = full_text.count("\n") + 1
                if status_hook:
                    final_detail = (
                        f"生成完毕: 共计 {len(full_text)} 字符 / {line_count} 行"
                        if self.language == "zh"
                        else f"Generated total {len(full_text)} chars ({line_count} lines)"
                    )
                    self._update_status(
                        AssistantStatus.STREAMING_RESPONSE,
                        final_detail,
                        status_hook,
                    )

                return full_text

            except Exception as e:
                err_str = str(e)
                if (
                    "503" in err_str or "429" in err_str
                ) and attempt < (max_retries - 1):
                    wait_time = backoff_delays[attempt]
                    delay_match = re.search(r"retry in (\d+)", err_str)
                    if delay_match:
                        wait_time = max(
                            wait_time, int(delay_match.group(1)) + 1
                        )
                    notice_msg = (
                        f"[Notice] 触发限流或服务端繁忙 ({self.model_name})，等待 {wait_time} 秒后重试... (尝试 {attempt + 1}/{max_retries})"
                        if self.language == "zh"
                        else f"[Notice] Rate limit / server busy ({self.model_name}), waiting {wait_time}s... (Attempt {attempt + 1}/{max_retries})"
                    )
                    print(notice_msg, flush=True)
                    time.sleep(wait_time)
                else:
                    raise e

    def parse_structured_response(self, text: str) -> dict:
        """
        通过明确定义的语义标签协议提取：
        <approach>: 实现思路
        <gdscript>: 纯净 GDScript 源码
        <key_points>: 核心要点及注意事项
        """
        approach = ""
        code = ""
        key_points = ""

        # 提取思路 <approach>
        m_app = re.search(r"<approach>([\s\S]*?)</approach>", text, re.IGNORECASE)
        if m_app:
            approach = m_app.group(1).strip()

        # 提取代码 <gdscript>
        m_code = re.search(r"<gdscript>([\s\S]*?)</gdscript>", text, re.IGNORECASE)
        if m_code:
            code = m_code.group(1).strip()
        else:
            # 容错兜底：若模型漏打标签但输出了标准 markdown 语法块
            m_alt = re.search(r"```(?:gdscript)?\s*([\s\S]*?)```", text)
            if m_alt:
                code = m_alt.group(1).strip()

        # 如果代码中意外包裹了 Markdown 标识，再次剔除保证纯净
        if code.startswith("```"):
            code = re.sub(r"^```[a-zA-Z0-9_]*\n?", "", code)
            code = re.sub(r"\n?```$", "", code).strip()

        # 提取核心要点 <key_points>
        m_points = re.search(r"<key_points>([\s\S]*?)</key_points>", text, re.IGNORECASE)
        if m_points:
            key_points = m_points.group(1).strip()

        return {
            "approach": approach,
            "extracted_code": code,
            "key_points": key_points,
        }

    def extract_base_class(self, code: str) -> str:
        """从生成的 GDScript 源码中提取 extends 的目标基类"""
        m = re.search(r"^\s*extends\s+([A-Za-z0-9_]+)", code, re.MULTILINE)
        return m.group(1).strip() if m else "Node"

    def validate_code_apis(self, code: str) -> dict:
        if not code:
            return {"valid": False, "error": "No GDScript code block found."}

        unverified_details = []
        unverified_signatures = []
        lines = code.splitlines()

        # 1. 废弃语法拦截 (Godot 3 -> 4)
        for idx, line in enumerate(lines, start=1):
            if re.search(r"\bexport\s*\(", line):
                unverified_details.append(
                    f"Line {idx}: Deprecated Godot 3 syntax 'export(...)'. Use Godot 4 '@export'."
                )

        # 2. 仅保留注解存在性校验
        found_annotations = re.findall(r"(@[a-zA-Z_][a-zA-Z0-9_]*)", code)
        for anno in set(found_annotations):
            if anno not in self.GODOT_ANNOTATIONS:
                unverified_details.append(f"Invalid annotation: {anno}")

        # 3. 静态符号分析 (包含继承树、自定义类、单例及单例成员调用)
        analysis = self.analyzer.analyze_code(code)
        for diag in analysis.get("diagnostics", []):
            line_no = diag["line"]
            symbol = diag["symbol"]
            msg = diag.get("message", f"Unknown symbol '{symbol}'")
            unverified_details.append(f"Line {line_no}: {msg}")
            unverified_signatures.append(symbol)

        return {
            "valid": len(unverified_details) == 0,
            "unverified_items": unverified_details,
            "unverified_signatures": list(set(unverified_signatures)),
        }

    def _build_system_instruction(self) -> str:
        if self.language == "zh":
            return (
                "Role: Godot 4 GDScript Expert.\n"
                "Output Protocol Rules:\n"
                "你必须且仅允许使用以下 3 个 XML 风格标签组织输出，严禁在标签外书写任何文字或引言：\n\n"
                "<approach>\n"
                "1 到 3 句话说明核心实现方案与思路。严禁出现任何内部自省、检查报告或“以下是代码”等套话。\n"
                "</approach>\n\n"
                "<gdscript>\n"
                "# 完整的纯净 Godot 4 GDScript 源码，首行必须写明明确的 extends <BaseClass>，严格遵循 Godot 4 规范与 @export 注解。\n"
                "</gdscript>\n\n"
                "<key_points>\n"
                "列出 1 到 3 条使用该脚本的关键注意事项（如必须挂载的子节点类型、输入映射 Action 名称或分组名）。若无特殊要求可留空。\n"
                "</key_points>\n\n"
                "Language Rules:\n"
                "- GDScript 代码标识符与变量名全英文。\n"
                "- <approach> 和 <key_points> 内容使用简体中文。"
            )
        else:
            return (
                "Role: Godot 4 GDScript Expert.\n"
                "Output Protocol Rules:\n"
                "You MUST structure your response strictly inside these 3 XML-style tags, with NO text outside tags:\n\n"
                "<approach>\n"
                "1-3 concise sentences explaining the technical architecture and approach. No meta-chatter or 'here is the code'.\n"
                "</approach>\n\n"
                "<gdscript>\n"
                "# Complete, clean Godot 4 GDScript code. Must start with explicit 'extends <BaseClass>'. Strictly Godot 4 syntax and annotations.\n"
                "</gdscript>\n\n"
                "<key_points>\n"
                "1-3 bullet points on critical setup details (e.g. required child node types, expected Input Map actions, group names).\n"
                "</key_points>\n\n"
                "Language Rules:\n"
                "- All explanations and comments strictly in English."
            )

    def generate_code_with_self_correction(
        self,
        query: str,
        max_correction_turns: int = 2,
        status_hook: Optional[Callable[[str, str], None]] = None,
        language: Optional[str] = None,
        thinking_budget: Optional[int] = None,
    ) -> dict:
        if language and language.lower() in ("en", "zh"):
            self.language = language.lower()

        effective_budget = (
            self.thinking_budget if thinking_budget is None else thinking_budget
        )

        try:
            # 步骤 1：生成初版
            gen_detail = self._get_localized_status(AssistantStatus.GENERATING)
            self._update_status(AssistantStatus.GENERATING, gen_detail, status_hook)

            system_instruction = self._build_system_instruction()
            user_content = f"--- User Requirement ---\n{query}"

            config_kwargs = {
                "system_instruction": system_instruction,
                "temperature": 0.2,
            }
            if effective_budget is not None and effective_budget >= 0:
                config_kwargs["thinking_config"] = types.ThinkingConfig(
                    thinking_budget=effective_budget
                )

            chat = self.client.chats.create(
                model=self.model_name,
                config=types.GenerateContentConfig(**config_kwargs),
            )

            raw_text = self._stream_chat_with_retry(
                chat, user_content, status_hook=status_hook
            )
            parsed = self.parse_structured_response(raw_text)
            code = parsed["extracted_code"]

            # 步骤 2：自动提炼基类
            base_class = self.extract_base_class(code)

            # 步骤 3：静态校验
            val_detail = self._get_localized_status(
                AssistantStatus.VALIDATING, target=base_class
            )
            self._update_status(AssistantStatus.VALIDATING, val_detail, status_hook)
            val_report = self.validate_code_apis(code)

            correction_attempts = 0

            # 步骤 4：自愈重试
            while (
                not val_report["valid"]
                and correction_attempts < max_correction_turns
            ):
                correction_attempts += 1
                error_list_str = "\n".join(val_report["unverified_items"])
                short_summary = ", ".join(val_report["unverified_signatures"])

                correcting_detail = self._get_localized_status(
                    AssistantStatus.SELF_CORRECTING,
                    target=base_class,
                    items=short_summary,
                    turn=correction_attempts,
                )
                self._update_status(
                    AssistantStatus.SELF_CORRECTING,
                    correcting_detail,
                    status_hook,
                )

                # 以当前基类精确拉取图谱 Ground Truth 上下文
                retrieval_res = self.retriever.retrieve(base_class, query, top_k=5)
                ground_truth_context = self.retriever.assemble_prompt_context(
                    retrieval_res
                )

                if self.language == "zh":
                    feedback_prompt = (
                        f"静态代码分析发现以下未定义符号或 API 错误：\n{error_list_str}\n\n"
                        f"{ground_truth_context}\n\n"
                        f"请修复代码。严格依据上述真实存在的 Godot 4 API、内置单例及基类修正。\n"
                        f"必须依然严格按照 <approach>、<gdscript>、<key_points> 这 3 个标签输出，严禁在标签外写任何文字或辩解。"
                    )
                else:
                    feedback_prompt = (
                        f"Static code analysis identified unverified symbols or API issues:\n{error_list_str}\n\n"
                        f"{ground_truth_context}\n\n"
                        f"Please fix the script using valid Godot 4 APIs listed above.\n"
                        f"You MUST format the entire output strictly with <approach>, <gdscript>, and <key_points> tags. No text outside tags."
                    )

                raw_text = self._stream_chat_with_retry(
                    chat, feedback_prompt, status_hook=status_hook
                )
                parsed = self.parse_structured_response(raw_text)
                code = parsed["extracted_code"]
                base_class = self.extract_base_class(code)

                self._update_status(
                    AssistantStatus.VALIDATING, val_detail, status_hook
                )
                val_report = self.validate_code_apis(code)

            # 步骤 5：完成
            completed_detail = self._get_localized_status(
                AssistantStatus.COMPLETED
            )
            self._update_status(
                AssistantStatus.COMPLETED, completed_detail, status_hook
            )

            # 构造用于在 Godot 中合并展示的纯净说明文本（排除了代码与自愈杂音）
            display_response_parts = []
            if parsed["approach"]:
                display_response_parts.append(f"**思路方案**：\n{parsed['approach']}")
            if parsed["key_points"]:
                display_response_parts.append(f"**核心要点**：\n{parsed['key_points']}")
            clean_display_text = "\n\n".join(display_response_parts)

            return {
                "target_class": base_class,
                "response_text": clean_display_text,
                "approach": parsed["approach"],
                "key_points": parsed["key_points"],
                "extracted_code": code,
                "validation": val_report,
                "correction_attempts": correction_attempts,
                "language": self.language,
                "thinking_budget": effective_budget,
            }

        except Exception as e:
            failed_detail = self._get_localized_status(
                AssistantStatus.FAILED, error=str(e)
            )
            self._update_status(
                AssistantStatus.FAILED, failed_detail, status_hook
            )
            raise e


def find_godot_project_root(start_path: Path) -> Path | None:
    current = start_path.resolve()
    for parent in [current] + list(current.parents):
        if (parent / "project.godot").exists():
            return parent
    return None


def main():
    parser = argparse.ArgumentParser(
        description="Godot Code Assistant CLI Bridge for EditorPlugin"
    )
    parser.add_argument(
        "--query",
        type=str,
        required=True,
        help="用户自然语言需求描述",
    )
    parser.add_argument(
        "--lang",
        type=str,
        default="zh",
        choices=["zh", "en"],
        help="语言设置 (zh 或 en)",
    )
    parser.add_argument(
        "--budget",
        type=int,
        default=512,
        help="思考预算 Thinking Budget",
    )
    parser.add_argument(
        "--json-output",
        action="store_true",
        help="以 JSON 格式输出供 Godot 编辑器插件解析",
    )

    args = parser.parse_args()
    auto_project_root = find_godot_project_root(Path(__file__))

    assistant = GodotCodeAssistant(
        language=args.lang,
        project_root=auto_project_root,
        thinking_budget=args.budget,
    )

    def on_status_change(status: str, detail: str):
        if not args.json_output:
            print(f"[{status}] >> {detail}", flush=True)

    try:
        result = assistant.generate_code_with_self_correction(
            query=args.query,
            status_hook=on_status_change,
        )

        if args.json_output:
            payload = {
                "success": True,
                "target_class": result["target_class"],
                "response_text": result["response_text"],
                "approach": result["approach"],
                "key_points": result["key_points"],
                "extracted_code": result["extracted_code"],
                "is_verified": result["validation"]["valid"],
                "correction_attempts": result["correction_attempts"],
                "unverified_items": result["validation"]["unverified_items"],
            }
            print("__GODOT_PLUGIN_PAYLOAD_START__", flush=True)
            print(json.dumps(payload, ensure_ascii=False), flush=True)
            print("__GODOT_PLUGIN_PAYLOAD_END__", flush=True)
        else:
            print("\n================== Extracted GDScript ===================")
            print(result["extracted_code"])
            print("=========================================================")
            print(f"思路方案:\n{result['approach']}")
            print(f"\n核心要点:\n{result['key_points']}")
            print(f"\n推导基类: {result['target_class']}")
            print(f"静态校验通过: {result['validation']['valid']}")
            print(f"自愈修正轮数: {result['correction_attempts']}")

    except Exception as e:
        if args.json_output:
            payload = {
                "success": False,
                "error": str(e),
                "extracted_code": "",
            }
            print("__GODOT_PLUGIN_PAYLOAD_START__", flush=True)
            print(json.dumps(payload, ensure_ascii=False), flush=True)
            print("__GODOT_PLUGIN_PAYLOAD_END__", flush=True)
        else:
            print(f"\n[!] 执行失败: {e}", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()