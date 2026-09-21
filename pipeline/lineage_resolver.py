import json
from pathlib import Path


class LineageResolver:

    # 官方 Godot 4 内置核心语言特性与注解（虚拟根定义）
    GDSCRIPT_BUILTIN_DEF = {
        "inherits": "",
        "brief_description": (
            "Built-in GDScript language functions and annotations."
        ),
        "methods": {
            "move_toward": {
                "return": "float",
                "args": "from: float, to: float, delta: float",
                "desc": "Moves from toward to by delta.",
            },
            "lerp": {
                "return": "Variant",
                "args": "from: Variant, to: Variant, weight: float",
                "desc": "Linearly interpolates between two values.",
            },
            "clamp": {
                "return": "Variant",
                "args": "value: Variant, min: Variant, max: Variant",
                "desc": "Clamps value between min and max.",
            },
            "min": {
                "return": "Variant",
                "args": "a: Variant, b: Variant",
                "desc": "Returns minimum of two values.",
            },
            "max": {
                "return": "Variant",
                "args": "a: Variant, b: Variant",
                "desc": "Returns maximum of two values.",
            },
            "abs": {
                "return": "Variant",
                "args": "s: Variant",
                "desc": "Returns absolute value.",
            },
            "sign": {
                "return": "Variant",
                "args": "s: Variant",
                "desc": "Returns -1, 0, or 1.",
            },
            "print": {
                "return": "void",
                "args": "...",
                "desc": "Prints to the console.",
            },
            "printerr": {
                "return": "void",
                "args": "...",
                "desc": "Prints error to console.",
            },
            "randf": {
                "return": "float",
                "args": "",
                "desc": "Returns random float between 0.0 and 1.0.",
            },
            "randi": {
                "return": "int",
                "args": "",
                "desc": "Returns random unsigned 32-bit integer.",
            },
            "randf_range": {
                "return": "float",
                "args": "from: float, to: float",
                "desc": "Returns random float in range.",
            },
            "randi_range": {
                "return": "int",
                "args": "from: int, to: int",
                "desc": "Returns random int in range.",
            },
        },
        "annotations": {
            "@export": "Export variable to inspector.",
            "@export_category": "Create a top-level category in inspector.",
            "@export_group": "Group properties in inspector.",
            "@export_subgroup": "Subgroup properties in inspector.",
            "@export_range": "Export float/int as a slider range.",
            "@export_enum": "Export variable with dropdown options.",
            "@export_file": "Export string as a file path selector.",
            "@export_dir": "Export string as a directory path selector.",
            "@export_multiline": "Export string as a multiline text edit.",
            "@onready": "Mark variable to initialize when node is ready.",
            "@tool": "Run script in editor.",
            "@icon": "Set custom node icon.",
        },
        "properties": {},
        "signals": {},
        "keywords": [
            "annotation",
            "export",
            "category",
            "group",
            "range",
            "onready",
            "tool",
            "inspector",
        ],
    }

    def __init__(self, graph_path: str):
        with open(graph_path, "r", encoding="utf-8") as f:
            self.graph = json.load(f)

        # 内存动态打补丁：若知识图谱中尚未收录 @GDScript，则自动注入
        if "@GDScript" not in self.graph:
            self.graph["@GDScript"] = self.GDSCRIPT_BUILTIN_DEF

    def get_inheritance_chain(self, class_name: str) -> list[str]:
        """追溯有向无环图 (DAG)，得到从叶子类到根类的链路: [Child(d=0), Parent(d=1), ..., Object]"""
        chain = []
        curr = class_name
        while curr and curr in self.graph:
            chain.append(curr)
            curr = self.graph[curr].get("inherits", "").strip()
        return chain

    def aggregate(self, class_name: str) -> dict:
        """自底向上逆向聚合 (Child-First Reverse Merging):

        先占为主，保护子类的特化覆盖，并为每个符号附带声明来源与深度标定。
        """
        chain = self.get_inheritance_chain(class_name)
        if not chain:
            return {}

        methods = {}
        properties = {}
        signals = {}
        annotations = {}

        for depth, c in enumerate(chain):
            c_data = self.graph[c]

            # 聚合属性 (子类优先覆盖)
            for p_name, p_info in c_data.get("properties", {}).items():
                if p_name not in properties:
                    properties[p_name] = {
                        **p_info,
                        "declared_in": c,
                        "depth": depth,
                    }

            # 聚合方法 (子类优先覆盖)
            for m_name, m_info in c_data.get("methods", {}).items():
                if m_name not in methods:
                    methods[m_name] = {
                        **m_info,
                        "declared_in": c,
                        "depth": depth,
                    }

            # 聚合信号
            for s_name, s_params in c_data.get("signals", {}).items():
                if s_name not in signals:
                    signals[s_name] = {
                        **s_params,
                        "declared_in": c,
                        "depth": depth,
                    }

            # 聚合注解 (主要来自 @GDScript 节点)
            for a_name, a_desc in c_data.get("annotations", {}).items():
                if a_name not in annotations:
                    annotations[a_name] = {
                        "desc": a_desc,
                        "declared_in": c,
                        "depth": depth,
                    }

        return {
            "target_class": class_name,
            "chain": chain,
            "properties": properties,
            "methods": methods,
            "signals": signals,
            "annotations": annotations,
        }


if __name__ == "__main__":
    resolver = LineageResolver("assets/godot4_api_graph.json")

    # 1. 验证常规节点聚合
    res = resolver.aggregate("CharacterBody2D")
    print(f"CharacterBody2D Chain: {res['chain']}")
    print(f"Methods count: {len(res['methods'])}")

    # 2. 验证 @GDScript 虚拟符号解析
    gdscript_res = resolver.aggregate("@GDScript")
    print(f"@GDScript Annotations count: {len(gdscript_res['annotations'])}")
    print(
        f"Sample Annotation (@export_range):"
        f" {gdscript_res['annotations'].get('@export_range')}"
    )