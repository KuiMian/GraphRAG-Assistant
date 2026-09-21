import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET


def clean_bbcode(text: str) -> str:
    """Remove BBCode tags and normalize whitespace."""
    if not text:
        return ""
    cleaned = re.sub(r"\[/?[a-zA-Z0-9_]+(=[^\]]*)?\]", "", text)
    return " ".join(cleaned.split()).strip()


def inspect_xml(xml_file_path: str):
    file_path = Path(xml_file_path)
    if not file_path.exists():
        print(f"[Error] File not found: {file_path}")
        return

    tree = ET.parse(file_path)
    root = tree.getroot()

    # Extract class metadata
    class_name = root.attrib.get("name", "")
    inherits = root.attrib.get("inherits", "")

    brief_elem = root.find("brief_description")
    brief_desc = (
        clean_bbcode(brief_elem.text)
        if brief_elem is not None and brief_elem.text
        else ""
    )

    # Extract properties
    members_elem = root.find("members")
    properties = []
    if members_elem is not None:
        for m in members_elem.findall("member"):
            properties.append(
                {
                    "name": m.attrib.get("name"),
                    "type": m.attrib.get("type"),
                    "default": m.attrib.get("default", ""),
                }
            )

    # Extract methods
    methods_elem = root.find("methods")
    methods = []
    if methods_elem is not None:
        for m in methods_elem.findall("method"):
            m_name = m.attrib.get("name")

            ret_elem = m.find("return")
            ret_type = ret_elem.attrib.get("type") if ret_elem is not None else "void"

            params = []
            for p in m.findall("param"):
                params.append(
                    {"name": p.attrib.get("name"), "type": p.attrib.get("type")}
                )

            # Determine virtual status (Variant B)
            qualifiers = m.attrib.get("qualifiers", "")
            is_virtual = m_name.startswith("_") or "virtual" in qualifiers

            # Extract brief description snippet (Variant A)
            desc_elem = m.find("description")
            desc_text = (
                clean_bbcode(desc_elem.text)
                if desc_elem is not None and desc_elem.text
                else ""
            )
            short_desc = (
                desc_text[:90] + "..." if len(desc_text) > 90 else desc_text
            )

            methods.append(
                {
                    "name": m_name,
                    "return": ret_type,
                    "params": params,
                    "is_virtual": is_virtual,
                    "desc": short_desc,
                }
            )

    # Extract signals
    signals_elem = root.find("signals")
    signals = []
    if signals_elem is not None:
        for s in signals_elem.findall("signal"):
            s_name = s.attrib.get("name")
            s_params = [
                p.attrib.get("name") + ": " + p.attrib.get("type", "Variant")
                for p in s.findall("param")
            ]
            signals.append(f"{s_name}({', '.join(s_params)})")

    # Display inspection report
    print("----------------------------------------")
    print("Class Probing Report")
    print("----------------------------------------")
    print(f"Class Name: {class_name}")
    print(f"Inherits:   {inherits}")
    print(f"Brief Desc: {brief_desc}\n")

    print(f"Properties Count: {len(properties)}")
    if properties:
        print(f"Properties Sample (Top 3):\n{json.dumps(properties[:3], indent=2, ensure_ascii=False)}\n")

    virtuals = [m for m in methods if m["is_virtual"]]
    concretes = [m for m in methods if not m["is_virtual"]]
    print(f"Methods Count:    {len(methods)}")
    print(f"- Virtual Methods (Variant B):  {len(virtuals)}")
    if virtuals:
        print(f"  Sample: {json.dumps(virtuals[0], indent=2, ensure_ascii=False)}")
    print(f"- Concrete Methods:             {len(concretes)}")
    if concretes:
        print(f"  Sample: {json.dumps(concretes[0], indent=2, ensure_ascii=False)}\n")

    print(f"Signals Count:    {len(signals)}")
    if signals:
        print(f"Signals List:     {signals}")
    print("----------------------------------------")


if __name__ == "__main__":
    inspect_xml("pipeline/data/classes/CharacterBody2D.xml")