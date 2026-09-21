import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET


def clean_bbcode(text: str | None) -> str:
    """清理 Godot 特有的 BBCode 标签（例如 [b], [code], [param x], [method x]）"""
    if not text:
        return ""
    # 保留参数/方法名，去掉外层包裹：[param x] -> x
    text = re.sub(r"\[(param|method|member)\s+([^\]]+)\]", r"\2", text)
    # 去掉所有其他闭合与自闭合标签：[code], [/code], [b], [/b] 等
    text = re.sub(r"\[/?\w+.*?\]", "", text)
    return text.strip()


def parse_all_xml_docs(xml_dir: Path) -> dict:
    """提取 XML 里的全部人类可读文本"""
    print(f"[*] 正在读取 XML 文档目录: {xml_dir.resolve()} ...")
    docs = {
        "classes": {},  # 类/单例 -> {brief, description, methods, properties}
        "annotations": {},  # 注解 -> {description} (来自 @GDScript.xml)
        "global_functions": {},  # 全局函数 -> {description} (来自 @GlobalScope.xml)
    }

    xml_files = list(xml_dir.glob("*.xml"))
    if not xml_files:
        print(f"[!] 警告: 没在 {xml_dir} 下找到任何 XML 文件！")
        return docs

    for f in xml_files:
        try:
            tree = ET.parse(f)
            root = tree.getroot()
        except Exception:
            continue

        c_name = root.get("name", "")

        # 1. 提取注解 (@GDScript.xml)
        if c_name == "@GDScript":
            for ann in root.findall(".//annotation"):
                a_name = ann.get("name")
                desc = ann.find("description")
                docs["annotations"][a_name] = {
                    "description": clean_bbcode(
                        desc.text if desc is not None else ""
                    )
                }
            continue

        # 2. 提取全局工具函数 (@GlobalScope.xml)
        if c_name == "@GlobalScope":
            for m in root.findall(".//methods/method"):
                m_name = m.get("name")
                desc = m.find("description")
                docs["global_functions"][m_name] = {
                    "description": clean_bbcode(
                        desc.text if desc is not None else ""
                    )
                }
            continue

        # 3. 提取普通类与单例 (Node, ProjectSettings, Input 等)
        brief = root.find("brief_description")
        desc = root.find("description")

        methods_map = {}
        for m in root.findall(".//methods/method"):
            m_name = m.get("name")
            m_desc = m.find("description")
            methods_map[m_name] = clean_bbcode(
                m_desc.text if m_desc is not None else ""
            )

        props_map = {}
        for p in root.findall(".//members/member"):
            p_name = p.get("name")
            props_map[p_name] = clean_bbcode(p.text if p.text else "")

        docs["classes"][c_name] = {
            "brief_description": clean_bbcode(
                brief.text if brief is not None else ""
            ),
            "description": clean_bbcode(desc.text if desc is not None else ""),
            "methods": methods_map,
            "properties": props_map,
        }

    print(
        f"[✓] XML 解析完成！捕获 {len(docs['classes'])} 个类文档, {len(docs['annotations'])} 个注解, {len(docs['global_functions'])} 个全局函数说明"
    )
    return docs


def build_enriched_kb(api_json_path: Path, xml_dir: Path, output_path: Path):
    """融合两者，产出最终单一数据底座"""
    print(f"[*] 正在读取 API JSON: {api_json_path.resolve()} ...")
    with open(api_json_path, "r", encoding="utf-8") as f:
        api_data = json.load(f)

    doc_data = parse_all_xml_docs(xml_dir)

    result_kb = {
        "singletons": {},
        "classes": {},
        "builtin_classes": [
            b["name"] for b in api_data.get("builtin_classes", [])
        ],
        "annotations": doc_data["annotations"],
        "global_functions": {},
    }

    # 1. 注入全局工具函数 (print, lerp 等)
    for u in api_data.get("utility_functions", []):
        name = u["name"]
        xml_desc = doc_data["global_functions"].get(name, {}).get(
            "description", ""
        )
        result_kb["global_functions"][name] = {
            "return_type": u.get("return_type", "void"),
            "arguments": u.get("arguments", []),
            "description": xml_desc,
        }

    # 2. 注入类元数据与方法 (包含 is_virtual, is_static, 以及 XML description)
    for c in api_data.get("classes", []):
        c_name = c["name"]
        xml_c = doc_data["classes"].get(c_name, {})

        methods_info = {}
        for m in c.get("methods", []):
            m_name = m["name"]
            methods_info[m_name] = {
                "is_virtual": m.get("is_virtual", False),
                "is_static": m.get("is_static", False),
                "return_value": m.get("return_value", {}),
                "arguments": m.get("arguments", []),
                "description": xml_c.get("methods", {}).get(m_name, ""),
            }

        properties_info = {}
        for p in c.get("properties", []):
            p_name = p["name"]
            properties_info[p_name] = {
                "type": p.get("type", "Variant"),
                "description": xml_c.get("properties", {}).get(p_name, ""),
            }

        result_kb["classes"][c_name] = {
            "inherits": c.get("inherits", ""),
            "brief_description": xml_c.get("brief_description", ""),
            "description": xml_c.get("description", ""),
            "methods": methods_info,
            "properties": properties_info,
        }

    # 3. 注入单例 (ProjectSettings, Input 等)
    for s in api_data.get("singletons", []):
        s_name = s["name"]
        s_type = s.get("type", s_name)
        xml_s = doc_data["classes"].get(s_name, {})
        result_kb["singletons"][s_name] = {
            "type": s_type,
            "brief_description": xml_s.get("brief_description", ""),
            "description": xml_s.get("description", ""),
        }

    # 写入单一文件
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result_kb, f, ensure_ascii=False, indent=2)

    print(f"[✓] 成功生成完全体知识库: {output_path.resolve()}")


if __name__ == "__main__":
    current_dir = Path(__file__).resolve().parent

    # 按照你实际存放的路径调整 assets 相对定位
    # 假设你的目录结构是:
    #   assets/extension_api.json
    #   assets/doc_classes/ (放所有的 xml)
    api_json = current_dir.parent / "assets" / "extension_api.json"
    doc_classes_dir = current_dir / "data" / "classes"
    output_kb = current_dir.parent / "assets" / "godot_enriched_kb.json"

    build_enriched_kb(api_json, doc_classes_dir, output_kb)