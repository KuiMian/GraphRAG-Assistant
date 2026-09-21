from collections import defaultdict
import json
from pathlib import Path


class InheritanceDAG:
    """管理引擎类与工程自定义类的单向继承图"""

    def __init__(self):
        self.parent_map: dict[str, str] = {}
        self.class_members_map: dict[str, set[str]] = defaultdict(set)
        self._flattened_cache: dict[str, set[str]] = {}

    def add_class(
        self,
        class_name: str,
        parent_class: str = "",
        members: set[str] | None = None,
    ):
        if parent_class:
            self.parent_map[class_name] = parent_class
        if members:
            self.class_members_map[class_name].update(members)
        self._flattened_cache.clear()

    def get_all_members_flattened(self, class_name: str) -> set[str]:
        """沿 DAG 向上提取该类自身及其全部祖先节点的所有成员 (带缓存)"""
        if class_name in self._flattened_cache:
            return self._flattened_cache[class_name]

        all_symbols = set()
        curr = class_name
        visited = set()

        while curr and curr not in visited:
            visited.add(curr)
            if curr in self.class_members_map:
                all_symbols.update(self.class_members_map[curr])
            curr = self.parent_map.get(curr, "")

        self._flattened_cache[class_name] = all_symbols
        return all_symbols


class GodotKnowledgeBase:
    """载入 enriched kb 并初始化引擎层继承 DAG"""

    def __init__(self, kb_json_path: str | Path | None = None):
        if kb_json_path is None:
            current_dir = Path(__file__).resolve().parent
            kb_json_path = (
                current_dir.parent / "assets" / "godot_enriched_kb.json"
            )

        self.kb_path = Path(kb_json_path)
        if not self.kb_path.exists():
            raise FileNotFoundError(f"找不到知识底座文件: {self.kb_path}")

        print(f"[*] 正在载入增强知识库: {self.kb_path.name} ...")
        with open(self.kb_path, "r", encoding="utf-8") as f:
            self.data = json.load(f)

        self.singletons = self.data.get("singletons", {})
        self.classes = self.data.get("classes", {})
        self.builtin_classes = set(self.data.get("builtin_classes", []))
        self.global_functions = self.data.get("global_functions", {})
        self.annotations = self.data.get("annotations", {})

        # 初始化引擎内置类的 DAG
        self.dag = InheritanceDAG()
        self._build_engine_dag()

        print(f"[✓] 知识库就绪:")
        print(f"    - 单例: {len(self.singletons)} 个")
        print(f"    - 引擎类: {len(self.classes)} 个 (已织入 DAG)")
        print(f"    - 基础数学/内置类型: {len(self.builtin_classes)} 个")
        print(f"    - 全局工具函数: {len(self.global_functions)} 个")

    def _build_engine_dag(self):
        """将引擎所有类的继承边与成员属性/方法/信号推入 DAG"""
        for c_name, meta in self.classes.items():
            inherits = meta.get("inherits", "")
            # 加上 signals
            members = (
                set(meta.get("methods", {}).keys())
                | set(meta.get("properties", {}).keys())
                | set(meta.get("signals", {}).keys())
            )
            self.dag.add_class(c_name, parent_class=inherits, members=members)

    def query_symbol(self, name: str) -> dict:
        if name in self.singletons:
            meta = self.singletons[name]
            return {
                "status": "RESOLVED",
                "type": "SINGLETON",
                "target_class": meta.get("type", name),
            }
        if name in self.classes:
            return {
                "status": "RESOLVED",
                "type": "CLASS",
                "inherits": self.classes[name].get("inherits", ""),
            }
        if name in self.builtin_classes:
            return {"status": "RESOLVED", "type": "BUILTIN_CLASS"}
        if name in self.global_functions:
            return {"status": "RESOLVED", "type": "UTILITY_FUNCTION"}
        return {"status": "UNKNOWN", "type": None}