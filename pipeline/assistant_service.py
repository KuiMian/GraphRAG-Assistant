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
from pipeline.retriever import SymbolicGraphRetriever


class AssistantStatus:
    """Assistant lifecycle state constants."""
    IDLE = "IDLE"
    RETRIEVING = "RETRIEVING"
    STREAMING_RESPONSE = "STREAMING_RESPONSE"
    VALIDATING = "VALIDATING"
    SELF_CORRECTING = "SELF_CORRECTING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class GodotCodeAssistant:
    Status = AssistantStatus

    # Status localization dictionary for both English and Chinese
    STATUS_I18N = {
        "en": {
            AssistantStatus.IDLE: "Idle",
            AssistantStatus.RETRIEVING: "Querying inheritance graph and annotations for {target}...",
            AssistantStatus.STREAMING_RESPONSE: "Generating code... ({chars} chars / {lines} lines)",
            AssistantStatus.VALIDATING: "Running static symbol analysis and line tracing...",
            AssistantStatus.SELF_CORRECTING: "Refining unverified APIs: [{items}] (Attempt {turn})",
            AssistantStatus.COMPLETED: "Code generated and statically verified.",
            AssistantStatus.FAILED: "Failed: {error}",
        },
        "zh": {
            AssistantStatus.IDLE: "空闲",
            AssistantStatus.RETRIEVING: "正在检索 {target} 的继承图谱与语言注解...",
            AssistantStatus.STREAMING_RESPONSE: "正在生成代码... (已生成 {chars} 字符 / {lines} 行)",
            AssistantStatus.VALIDATING: "正在进行静态符号自省与行号溯源...",
            AssistantStatus.SELF_CORRECTING: "检测到未定义符号/格式问题，打回重写: [{items}] (第 {turn} 次)",
            AssistantStatus.COMPLETED: "代码生成完成并通过静态校验。",
            AssistantStatus.FAILED: "执行失败: {error}",
        },
    }

    # GDScript built-in core global functions whitelist
    GDSCRIPT_BUILTINS = {
        "move_toward", "lerp", "clamp", "min", "max", "abs", "sign",
        "print", "printerr", "print_debug", "str", "int", "float", "bool",
        "randf", "randi", "randf_range", "randi_range", "sin", "cos", "tan",
        "deg_to_rad", "rad_to_deg", "is_equal_approx", "is_zero_approx",
    }

    # Godot core singletons
    GODOT_SINGLETONS = {
        "Input", "Time", "OS", "Engine", "AudioServer", "RenderingServer",
        "PhysicsServer2D", "PhysicsServer3D", "DisplayServer", "ResourceLoader"
    }

    # Godot 4 official built-in annotations
    GODOT_ANNOTATIONS = {
        "@export", "@export_category", "@export_group", "@export_subgroup",
        "@export_range", "@export_enum", "@export_file", "@export_dir",
        "@export_global_file", "@export_global_dir", "@export_multiline",
        "@export_placeholder", "@export_flags", "@export_flags_2d_physics",
        "@export_flags_2d_render", "@export_flags_3d_physics",
        "@export_flags_3d_render", "@export_exp_easing", "@export_color_no_alpha",
        "@export_node_path", "@export_custom", "@onready", "@tool", "@icon",
        "@rpc", "@warning_ignore",
    }

    def __init__(
        self,
        api_key: str | None = None,
        config_path: str = "config.json",
        graph_path: str = "assets/godot4_api_graph.json",
        model_name: str = "gemini-2.5-flash",
        language: str = "en",
        thinking_budget: Optional[int] = 512,
    ):
        self.retriever = SymbolicGraphRetriever(graph_path)
        self.model_name = model_name
        self.language = language.lower() if language.lower() in ("en", "zh") else "en"
        self.thinking_budget = thinking_budget
        self.current_status: str = AssistantStatus.IDLE

        resolved_key = api_key
        if not resolved_key and Path(config_path).exists():
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                    resolved_key = cfg.get("api_key")
                    self.model_name = cfg.get("model_name", self.model_name)
                    self.language = cfg.get("language", self.language)
                    # 从配置中解析 thinking_budget
                    self.thinking_budget = cfg.get("thinking_budget", self.thinking_budget)
            except Exception as e:
                print(f"[Warning] Failed to read {config_path}: {e}")

        if not resolved_key:
            resolved_key = os.environ.get("GEMINI_API_KEY")

        if not resolved_key:
            raise ValueError("No Gemini API key provided. Check config.json or environment variables.")

        self.client = genai.Client(api_key=resolved_key)

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
        print(f">> State Changed: {status_msg}")
        if callback:
            try:
                callback(new_status, detail)
            except Exception as e:
                print(f"[Warning] Status callback error: {e}")

    def _stream_chat_with_retry(
        self, chat_session, message_content: str, status_hook=None, max_retries: int = 4
    ) -> str:
        """带连接耗时统计(TTFB)与时间节流的流式通信层"""
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
                    self._update_status(AssistantStatus.STREAMING_RESPONSE, connecting_msg, status_hook)

                response_stream = chat_session.send_message_stream(message_content)

                has_received_first_chunk = False

                for chunk in response_stream:
                    text_piece = chunk.text or ""
                    if text_piece:
                        if not has_received_first_chunk:
                            has_received_first_chunk = True
                            first_token_cost = round(time.time() - start_connect_time, 2)
                            ttfb_info = (
                                f">> 收到首字响应！首字节耗时 (TTFB): {first_token_cost}s"
                                if self.language == "zh"
                                else f">> First token received! TTFB: {first_token_cost}s"
                            )
                            print(ttfb_info)

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
                                status_hook
                            )
                            last_update_time = now

                line_count = full_text.count("\n") + 1
                if status_hook:
                    final_detail = (
                        f"生成完毕: 共计 {len(full_text)} 字符 / {line_count} 行"
                        if self.language == "zh"
                        else f"Generated total {len(full_text)} chars ({line_count} lines)"
                    )
                    self._update_status(AssistantStatus.STREAMING_RESPONSE, final_detail, status_hook)

                return full_text

            except Exception as e:
                err_str = str(e)
                if ("503" in err_str or "429" in err_str) and attempt < (max_retries - 1):
                    wait_time = backoff_delays[attempt]
                    delay_match = re.search(r"retry in (\d+)", err_str)
                    if delay_match:
                        wait_time = max(wait_time, int(delay_match.group(1)) + 1)
                    notice_msg = (
                        f"[Notice] 触发限流或服务端繁忙 ({self.model_name})，等待 {wait_time} 秒后重试... (尝试 {attempt + 1}/{max_retries})"
                        if self.language == "zh"
                        else f"[Notice] Rate limit / server busy ({self.model_name}), waiting {wait_time}s... (Attempt {attempt + 1}/{max_retries})"
                    )
                    print(notice_msg)
                    time.sleep(wait_time)
                else:
                    raise e

    def extract_gdscript(self, response_text: str) -> str:
        pattern = r"```(?:gdscript)?\s*(.*?)```"
        matches = re.findall(pattern, response_text, re.DOTALL)
        if matches:
            return matches[0].strip()
        return ""

    def validate_code_apis(self, class_name: str, code: str) -> dict:
        """静态符号自省校验"""
        if not code:
            return {"valid": False, "error": "No GDScript code block found."}

        aggregated = self.retriever.resolver.aggregate(class_name)
        legal_methods = set(aggregated.get("methods", {}).keys())

        unverified_details = []
        unverified_signatures = []

        lines = code.split("\n")

        # 1. 废弃语法检测
        for idx, line in enumerate(lines, start=1):
            if re.search(r"\bexport\s*\(", line):
                unverified_details.append(
                    f"Line {idx}: Deprecated Godot 3 syntax 'export(...)'. Use Godot 4 '@export'."
                )

        # 2. 注解白名单与参数完整性
        found_annotations = re.findall(r"(@[a-zA-Z_][a-zA-Z0-9_]*)", code)
        for anno in set(found_annotations):
            if anno not in self.GODOT_ANNOTATIONS:
                unverified_details.append(f"Invalid annotation: {anno}")

        for bad_category in re.findall(r"@export_(?:category|group)\s*\(\s*\)", code):
            unverified_details.append(f"Malformed annotation: '{bad_category}' requires a string argument.")

        for bad_range in re.findall(r"@export_range\s*\(\s*\)", code):
            unverified_details.append(f"Malformed annotation: '{bad_range}' requires range arguments.")

        # 3. 提取局部符号（消歧）
        declared_local_funcs = set(re.findall(r"func\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\(", code))
        clean_code_no_anno = re.sub(r"@[a-zA-Z0-9_]+(?:\(.*?\))?", "", code)
        declared_vars = set(re.findall(r"(?:var|const)\s+([a-zA-Z_][a-zA-Z0-9_]*)", clean_code_no_anno))
        loop_vars = set(re.findall(r"for\s+([a-zA-Z_][a-zA-Z0-9_]*)\s+in", code))

        param_names = set()
        for p_str in re.findall(r"func\s+[a-zA-Z0-9_]+\s*\((.*?)\)", code):
            for piece in p_str.split(","):
                piece = piece.strip()
                if piece:
                    p_name = piece.split(":")[0].split("=")[0].strip()
                    if p_name:
                        param_names.add(p_name)

        local_symbols = declared_local_funcs | declared_vars | loop_vars | param_names

        # 4. 按行提取函数调用并核验
        for idx, line in enumerate(lines, start=1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue

            matches = re.finditer(
                r"(?:([a-zA-Z_][a-zA-Z0-9_]*)\.)?(?<!@)\b([a-zA-Z_][a-zA-Z0-9_]*)\s*\(",
                line
            )

            for m in matches:
                caller = m.group(1) or ""
                fn = m.group(2)
                full_call = f"{caller}.{fn}()" if caller else f"{fn}()"

                if fn in local_symbols:
                    continue
                if fn in self.GDSCRIPT_BUILTINS:
                    continue
                if caller in self.GODOT_SINGLETONS:
                    continue
                if fn in {"Vector2", "Vector3", "Vector2i", "Vector3i", "Color", "Rect2", "Transform2D", "Array", "Dictionary"}:
                    continue

                if caller == "" or caller == "self":
                    if fn in legal_methods:
                        continue
                    unverified_details.append(f"Line {idx}: Unknown method '{full_call}' on {class_name} -> `{stripped}`")
                    unverified_signatures.append(full_call)
                else:
                    unverified_details.append(f"Line {idx}: Unverified call '{full_call}' -> `{stripped}`")
                    unverified_signatures.append(full_call)

        return {
            "valid": len(unverified_details) == 0,
            "unverified_items": unverified_details,
            "unverified_signatures": list(set(unverified_signatures)),
        }

    def _build_compact_system_instruction(self) -> str:
        """
        方案二：精炼的紧凑技术契约（Engineering Contract）。
        大幅砍掉多余套话与重复说教，减小模型在 CoT 阶段的符号冲突空间。
        """
        if self.language == "zh":
            return (
                "Role: Godot 4 GDScript Expert.\n"
                "Rules:\n"
                "1. Code: Strictly Godot 4.x syntax in ```gdscript ... ``` block. Use standard English identifiers.\n"
                "2. Context: Prioritize verified APIs/annotations from Context. Do NOT hallucinate node methods.\n"
                "3. Annotations: Use Godot 4 annotations (@export, @export_range, @export_group, @onready). Never use Godot 3 export(...).\n"
                "4. Language: GDScript code must use English names. All surrounding explanations and comments must be in Simplified Chinese (简体中文)."
            )
        else:
            return (
                "Role: Godot 4 GDScript Expert.\n"
                "Rules:\n"
                "1. Code: Strictly Godot 4.x syntax in ```gdscript ... ``` block.\n"
                "2. Context: Prioritize verified APIs/annotations from Context. Do NOT hallucinate node methods.\n"
                "3. Annotations: Use Godot 4 annotations (@export, @export_range, @export_group, @onready). Never use Godot 3 export(...).\n"
                "4. Language: All explanations and code comments strictly in English."
            )

    def generate_code_with_self_correction(
        self,
        class_name: str,
        query: str,
        max_correction_turns: int = 2,
        status_hook: Optional[Callable[[str, str], None]] = None,
        language: Optional[str] = None,
        thinking_budget: Optional[int] = None,
    ) -> dict:
        """带思考预算调节、精简技术契约与自愈重试的代码生成流水线"""
        if language and language.lower() in ("en", "zh"):
            self.language = language.lower()

        # 优先使用单次传入的预算，否则回退到全局实例配置
        effective_budget = self.thinking_budget if thinking_budget is None else thinking_budget

        try:
            # 1. 检索阶段
            retrieving_detail = self._get_localized_status(AssistantStatus.RETRIEVING, target=class_name)
            self._update_status(AssistantStatus.RETRIEVING, retrieving_detail, status_hook)
            retrieval_res = self.retriever.retrieve(class_name, query, top_k=5)
            ground_truth_context = self.retriever.assemble_prompt_context(retrieval_res)

            system_instruction = self._build_compact_system_instruction()

            user_content = (
                f"{ground_truth_context}\n\n"
                f"--- User Request ---\n"
                f"Target Node: {class_name}\n"
                f"Requirements: {query}"
            )

            # 配置生成参数，动态注入思考预算 (Thinking Config)
            config_kwargs = {
                "system_instruction": system_instruction,
                "temperature": 0.2,
            }
            if effective_budget is not None and effective_budget >= 0:
                config_kwargs["thinking_config"] = types.ThinkingConfig(thinking_budget=effective_budget)

            chat = self.client.chats.create(
                model=self.model_name,
                config=types.GenerateContentConfig(**config_kwargs),
            )

            # 2. 流式生成
            raw_text = self._stream_chat_with_retry(chat, user_content, status_hook=status_hook)
            code = self.extract_gdscript(raw_text)

            # 3. 静态符号自检
            validating_detail = self._get_localized_status(AssistantStatus.VALIDATING)
            self._update_status(AssistantStatus.VALIDATING, validating_detail, status_hook)
            val_report = self.validate_code_apis(class_name, code)

            correction_attempts = 0

            # 4. 自愈修正循环
            while not val_report["valid"] and correction_attempts < max_correction_turns:
                correction_attempts += 1
                error_list_str = "\n".join(val_report["unverified_items"])
                short_summary = ", ".join(val_report["unverified_signatures"])

                correcting_detail = self._get_localized_status(
                    AssistantStatus.SELF_CORRECTING,
                    items=short_summary,
                    turn=correction_attempts,
                )
                self._update_status(AssistantStatus.SELF_CORRECTING, correcting_detail, status_hook)

                if self.language == "zh":
                    feedback_prompt = (
                        f"静态代码分析警告：代码中调用了未定义方法：\n{error_list_str}\n\n"
                        f"请修复此脚本。使用合法的 Godot 4 内置函数或属性替代上述调用。"
                        f"确保返回完整的 ```gdscript ... ``` 代码块，并简要中文说明。"
                    )
                else:
                    feedback_prompt = (
                        f"Static Analysis Warning: Code called unverified methods:\n{error_list_str}\n\n"
                        f"Please fix the script using only valid Godot 4 built-ins or properties.\n"
                        f"Return the complete corrected ```gdscript ... ``` block with brief English explanations."
                    )

                raw_text = self._stream_chat_with_retry(chat, feedback_prompt, status_hook=status_hook)
                code = self.extract_gdscript(raw_text)

                self._update_status(AssistantStatus.VALIDATING, validating_detail, status_hook)
                val_report = self.validate_code_apis(class_name, code)

            # 5. 完成
            completed_detail = self._get_localized_status(AssistantStatus.COMPLETED)
            self._update_status(AssistantStatus.COMPLETED, completed_detail, status_hook)

            return {
                "retrieval": retrieval_res,
                "response_text": raw_text,
                "extracted_code": code,
                "validation": val_report,
                "correction_attempts": correction_attempts,
                "language": self.language,
                "thinking_budget": effective_budget,
            }

        except Exception as e:
            failed_detail = self._get_localized_status(AssistantStatus.FAILED, error=str(e))
            self._update_status(AssistantStatus.FAILED, failed_detail, status_hook)
            raise e


if __name__ == "__main__":
    # 测试：将 thinking_budget 设为 512 时的响应速度
    test_lang = "zh"
    
    assistant = GodotCodeAssistant(language=test_lang)

    test_class = "CharacterBody2D"
    test_query = (
        "实现2D平台跳跃与左右移动，支持重力与跳跃，使用 @export_group 整理参数并为移速添加滑动条范围。"
        if test_lang == "zh"
        else "Implement 2D platformer movement: left/right run with acceleration, jump, and handle gravity. Export speed with slider range."
    )

    def on_ui_status_change(status: str, detail: str):
        print(f"   [Dock Status UI ({assistant.language})] >> {detail}")

    print(f"Running pipeline with Thinking Budget: {assistant.thinking_budget}...")
    result = assistant.generate_code_with_self_correction(
        test_class, test_query, status_hook=on_ui_status_change
    )

    # 1. 打印完整的模型原始自然语言说明
    print("\n================== Full Model Response ==================")
    print(result["response_text"])
    print("=========================================================")

    # 2. 打印抽取的纯 GDScript
    print("\n================== Extracted GDScript ===================")
    print(result["extracted_code"])
    print("=========================================================")

    # 3. 打印符号自检报告
    print("\n================== Validation Report ====================")
    val = result["validation"]
    print(f"Is Verified: {val['valid']}")
    if not val['valid']:
        print("Detailed Issues Found:")
        for err in val["unverified_items"]:
            print(f"  - {err}")
    print(f"Correction Turns Used: {result['correction_attempts']}")
    print("=========================================================")