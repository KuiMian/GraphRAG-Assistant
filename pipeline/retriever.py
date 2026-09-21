from pathlib import Path
import re
from pipeline.lineage_resolver import LineageResolver


class SymbolicGraphRetriever:

    # 为常用注解补充语义关键词，确保自然语言提问能顺利匹配
    ANNOTATION_KEYWORDS = {
        "@export": ["export", "inspector", "variable", "parameter", "property"],
        "@export_category": ["category", "inspector", "header", "organization", "organize"],
        "@export_group": ["group", "section", "folding", "inspector", "organize"],
        "@export_subgroup": ["subgroup", "subsection", "inspector"],
        "@export_range": ["range", "slider", "min", "max", "step", "limit", "clamp", "speed"],
        "@export_enum": ["enum", "dropdown", "options", "select", "choice"],
        "@export_file": ["file", "path", "asset", "resource", "picker"],
        "@export_dir": ["directory", "folder", "path"],
        "@export_multiline": ["multiline", "text", "string", "long"],
        "@onready": ["onready", "ready", "node", "child", "get_node", "dependency"],
        "@tool": ["tool", "editor", "plugin", "runtime"],
        "@icon": ["icon", "node_icon", "custom"],
    }

    def __init__(
        self,
        graph_path: str = "assets/godot4_api_graph.json",
        depth_decay: float = 0.85,
        confidence_threshold: float = 0.15,
    ):
        self.resolver = LineageResolver(graph_path)
        self.depth_decay = depth_decay
        self.confidence_threshold = confidence_threshold

    def _tokenize_query(self, query: str) -> set[str]:
        """提取用户查询词并转小写"""
        tokens = re.findall(r"[a-zA-Z0-9_]+", query.lower())
        return {t for t in tokens if len(t) >= 2}

    def _calculate_score(
        self, query_tokens: set[str], name: str, keywords: list[str], depth: int
    ) -> float:
        """
        计算加权得分: Jaccard 交集 + 名称直击加成, 再乘上深度衰减系数 (gamma ^ depth)
        """
        kw_set = set(keywords)
        if not kw_set:
            return 0.0

        matches = query_tokens & kw_set
        if not matches:
            return 0.0

        # Jaccard 词袋相似度
        jaccard = len(matches) / len(query_tokens | kw_set)

        # 名字直接命中奖励 (Exact Name Match Bonus)
        name_bonus = 0.0
        name_lower = name.lower()
        for q in query_tokens:
            if q in name_lower:
                name_bonus += 0.4

        raw_score = jaccard + name_bonus
        # 施加层级深度衰减
        decayed_score = raw_score * (self.depth_decay**depth)
        return round(decayed_score, 4)

    def retrieve(self, class_name: str, query: str, top_k: int = 5) -> dict:
        """执行剪枝检索与置信度门控"""
        aggregated = self.resolver.aggregate(class_name)
        if not aggregated:
            return {"error": f"Class '{class_name}' does not exist in graph."}

        gdscript_aggregated = self.resolver.aggregate("@GDScript")

        query_tokens = self._tokenize_query(query)

        # 1. 检索方法 (Methods)
        scored_methods = []
        for m_name, m_info in aggregated["methods"].items():
            s = self._calculate_score(
                query_tokens, m_name, m_info.get("keywords", []), m_info["depth"]
            )
            if s >= self.confidence_threshold:
                scored_methods.append((s, m_name, m_info))
        scored_methods.sort(key=lambda x: x[0], reverse=True)

        # 2. 检索属性 (Properties)
        scored_props = []
        for p_name, p_info in aggregated["properties"].items():
            s = self._calculate_score(
                query_tokens, p_name, p_info.get("keywords", []), p_info["depth"]
            )
            if s >= self.confidence_threshold:
                scored_props.append((s, p_name, p_info))
        scored_props.sort(key=lambda x: x[0], reverse=True)

        # 3. 检索信号 (Signals)
        scored_signals = []
        for s_name, s_info in aggregated["signals"].items():
            s = self._calculate_score(
                query_tokens, s_name, s_info.get("keywords", []), s_info["depth"]
            )
            if s >= self.confidence_threshold:
                scored_signals.append((s, s_name, s_info))
        scored_signals.sort(key=lambda x: x[0], reverse=True)

        # 4. 检索语言级注解 (Annotations，来自 @GDScript，深度设为 0)
        scored_annotations = []
        annotations_dict = gdscript_aggregated.get("annotations", {})
        for a_name, a_info in annotations_dict.items():
            # 融合预定义的关键词与注解描述分词
            desc = a_info.get("desc", "") if isinstance(a_info, dict) else str(a_info)
            kw_list = self.ANNOTATION_KEYWORDS.get(a_name, []) + list(self._tokenize_query(desc))
            
            # 注解为语言原生语法，depth=0 不衰减
            s = self._calculate_score(query_tokens, a_name, kw_list, depth=0)
            if s >= self.confidence_threshold:
                scored_annotations.append((s, a_name, desc))
        scored_annotations.sort(key=lambda x: x[0], reverse=True)

        return {
            "target_class": class_name,
            "chain": aggregated["chain"],
            "query_tokens": list(query_tokens),
            "top_methods": scored_methods[:top_k],
            "top_properties": scored_props[:top_k],
            "top_signals": scored_signals[:top_k],
            "top_annotations": scored_annotations[:top_k],
        }

    def assemble_prompt_context(self, retrieved: dict) -> str:
        """将检索符号装配为严格的上下文约束"""
        if "error" in retrieved:
            return f"[Error] {retrieved['error']}"

        lines = [
            "### Godot 4 Ground-Truth Context",
            f"Target Node: {retrieved['target_class']}",
            f"Inheritance Chain: {' -> '.join(retrieved['chain'])}",
        ]

        has_candidates = any([
            retrieved["top_methods"],
            retrieved["top_properties"],
            retrieved["top_signals"],
            retrieved.get("top_annotations", []),
        ])

        # 门控未通过：安全回退，发出负向约束
        if not has_candidates:
            lines.append("\n[Notice]")
            lines.append(
                "No specialized engine APIs or annotations exceeded the confidence threshold for this query."
            )
            lines.append(
                "Rely STRICTLY on standard GDScript control flow. Do NOT hallucinate engine methods."
            )
            return "\n".join(lines)

        lines.append("\nVerified Godot 4 APIs & Language Annotations for Implementation:")

        # 优先展示注解规范（指导检查器和属性声明）
        if retrieved.get("top_annotations"):
            lines.append("\n[Language Annotations]")
            for score, name, desc in retrieved["top_annotations"]:
                lines.append(f"- {name} (score: {score}) - Description: {desc}")

        if retrieved["top_methods"]:
            lines.append("\n[Methods]")
            for score, name, info in retrieved["top_methods"]:
                params = [f"{p['name']}: {p['type']}" for p in info.get("params", [])]
                sig = f"{name}({', '.join(params)}) -> {info.get('return', 'void')}"
                virt = " [Virtual Callback]" if info.get("is_virtual") else ""
                lines.append(
                    f"- {sig}{virt}  (from {info['declared_in']}, score: {score})"
                )
                if info.get("desc"):
                    lines.append(f"  Description: {info['desc']}")

        if retrieved["top_properties"]:
            lines.append("\n[Properties]")
            for score, name, info in retrieved["top_properties"]:
                lines.append(
                    f"- {name}: {info['type']}  (from {info['declared_in']}, score: {score})"
                )

        if retrieved["top_signals"]:
            lines.append("\n[Signals]")
            for score, name, info in retrieved["top_signals"]:
                lines.append(
                    f"- signal {name}  (from {info['declared_in']}, score: {score})"
                )

        lines.append("\n[Constraint Directives]")
        lines.append(
            "1. Strictly adhere to Godot 4.x syntax (e.g., use @export, @export_range instead of Godot 3 export)."
        )
        lines.append(
            "2. Prioritize the verified APIs and annotations above to prevent version drift and hallucination."
        )

        return "\n".join(lines)