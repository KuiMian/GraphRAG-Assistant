import json
from pathlib import Path
import re
import sys
import xml.etree.ElementTree as ET


class GodotClassParser:
    # 领域停用词与文档噪音词表
    STOPWORDS = {
        "the", "a", "an", "and", "or", "in", "on", "at", "to", "for", "of", "with",
        "by", "from", "is", "are", "was", "were", "be", "this", "that", "it", "as",
        "if", "when", "returns", "return", "returned", "method", "function",
        "call", "called", "sets", "gets", "value", "whether", "allows", "manually"
    }

    def __init__(self, max_desc_chars: int = 1600):
        self.max_desc_chars = max_desc_chars

    def clean_bbcode(self, text: str) -> str:
        """剥离 BBCode 标签并标准化空白字符"""
        if not text:
            return ""
        cleaned = re.sub(r"\[/?[a-zA-Z0-9_]+(=[^\]]*)?\]", "", text)
        return " ".join(cleaned.split()).strip()

    def truncate_text(self, text: str) -> str:
        """按最大字符限制安全截断文本（保持单词完整）"""
        if len(text) <= self.max_desc_chars:
            return text
        truncated = text[: self.max_desc_chars].rsplit(" ", 1)[0]
        return truncated + "..."

    def extract_keywords(self, name: str, text: str = "") -> list[str]:
        """AOT 预提取：将 API 名称与文档描述转化为低维高纯度倒排词袋"""
        combined = f"{name} {text}"
        tokens = re.findall(r"[a-zA-Z0-9_]+", combined.lower())
        valid = {
            t for t in tokens
            if len(t) >= 3 and not t.isdigit() and t not in self.STOPWORDS
        }
        return sorted(list(valid))

    def parse_xml_file(self, file_path: Path) -> dict:
        try:
            tree = ET.parse(file_path)
            root = tree.getroot()
        except Exception:
            return None

        if root.tag != "class":
            return None

        class_name = root.attrib.get("name", "").strip()
        inherits = root.attrib.get("inherits", "").strip()
        if not class_name:
            return None

        # 提取类级别简短描述
        class_desc = ""
        brief_node = root.find("brief_description")
        if brief_node is not None and brief_node.text:
            cleaned_brief = self.clean_bbcode(brief_node.text)
            class_desc = self.truncate_text(cleaned_brief)

        # 提取属性 (Properties) 并预索引关键词
        properties = {}
        members_node = root.find("members")
        if members_node is not None:
            for m in members_node.findall("member"):
                p_name = m.attrib.get("name", "")
                p_type = m.attrib.get("type", "Variant")
                p_default = m.attrib.get("default", "")
                if p_name:
                    prop_info = {
                        "type": p_type,
                        "keywords": self.extract_keywords(p_name),
                    }
                    if p_default:
                        prop_info["default"] = p_default
                    properties[p_name] = prop_info

        # 提取方法 (Methods) 并预索引关键词
        methods = {}
        methods_node = root.find("methods")
        if methods_node is not None:
            for m in methods_node.findall("method"):
                m_name = m.attrib.get("name", "")
                if not m_name:
                    continue

                ret_node = m.find("return")
                ret_type = (
                    ret_node.attrib.get("type", "void")
                    if ret_node is not None
                    else "void"
                )

                params = []
                for p in m.findall("param"):
                    params.append(
                        {
                            "name": p.attrib.get("name", ""),
                            "type": p.attrib.get("type", "Variant"),
                        }
                    )

                qualifiers = m.attrib.get("qualifiers", "")
                is_virtual = m_name.startswith("_") or "virtual" in qualifiers

                desc_node = m.find("description")
                raw_desc = (
                    self.clean_bbcode(desc_node.text)
                    if desc_node is not None and desc_node.text
                    else ""
                )

                # 将方法名与描述合并生成离线关键词集合
                keywords = self.extract_keywords(m_name, raw_desc)

                method_data = {
                    "return": ret_type,
                    "params": params,
                    "is_virtual": is_virtual,
                    "keywords": keywords,
                }
                if raw_desc:
                    method_data["desc"] = self.truncate_text(raw_desc)

                methods[m_name] = method_data

        # 提取信号 (Signals) 并预索引关键词
        signals = {}
        signals_node = root.find("signals")
        if signals_node is not None:
            for s in signals_node.findall("signal"):
                s_name = s.attrib.get("name", "")
                if not s_name:
                    continue
                s_params = []
                for p in s.findall("param"):
                    s_params.append(
                        {
                            "name": p.attrib.get("name", ""),
                            "type": p.attrib.get("type", "Variant"),
                        }
                    )
                signals[s_name] = {
                    "params": s_params,
                    "keywords": self.extract_keywords(s_name),
                }

        return {
            "name": class_name,
            "inherits": inherits,
            "desc": class_desc,
            "properties": properties,
            "methods": methods,
            "signals": signals,
        }


def build_and_export(xml_dir: str, output_file: str):
    xml_folder = Path(xml_dir)
    if not xml_folder.exists():
        print(f"[Error] Directory not found: {xml_dir}")
        sys.exit(1)

    files = list(xml_folder.glob("*.xml"))
    print(f"Discovered {len(files)} XML definitions. Processing AOT indexing...")

    parser = GodotClassParser(max_desc_chars=200)
    api_graph = {}

    for f in files:
        res = parser.parse_xml_file(f)
        if res:
            c_name = res.pop("name")
            api_graph[c_name] = res

    out_path = Path(output_file)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(api_graph, f, ensure_ascii=False, indent=2)

    file_size_mb = out_path.stat().st_size / (1024 * 1024)
    print("--------------------------------------------------")
    print(f"ETL Complete. Total Classes: {len(api_graph)}")
    print(f"Target Graph: {out_path.resolve()} ({file_size_mb:.2f} MB)")
    print("--------------------------------------------------")


if __name__ == "__main__":
    XML_DIR = "pipeline/data/classes"
    OUTPUT_FILE = "assets/godot4_api_graph.json"
    build_and_export(XML_DIR, OUTPUT_FILE)