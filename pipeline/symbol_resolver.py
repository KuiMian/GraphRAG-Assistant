import re
from pathlib import Path
from typing import Any

from kb_loader import GodotKnowledgeBase
from project_scanner import GodotProjectScanner


class GodotSymbolResolver:

    def __init__(
        self,
        kb_path: str | Path | None = None,
        project_root: str | Path | None = None,
    ):
        self.kb = GodotKnowledgeBase(kb_path)
        self.project_scanner = GodotProjectScanner(project_root)
        self._fuse_project_into_dag()

    def set_project_root(self, project_root: str | Path):
        self.project_scanner.set_project_root(project_root)
        self._fuse_project_into_dag()

    def _fuse_project_into_dag(self):
        """将用户项目的自定义类与继承关系缝合进全局继承 DAG"""
        for c_name, meta in self.project_scanner.custom_classes_meta.items():
            self.kb.dag.add_class(
                class_name=c_name,
                parent_class=meta["extends"],
                members=meta["members"],
            )

    def resolve_singleton_member(self, singleton_name: str, member_name: str) -> bool:
        """验证单例对象上调用的成员方法/属性/信号是否存在于其类定义及继承链中"""
        # 1. 检查是否为内置单例 (Input, Time, DisplayServer 等)
        if singleton_name in self.kb.singletons:
            target_class = self.kb.singletons[singleton_name].get("type", singleton_name)
            inherited_symbols = self.kb.dag.get_all_members_flattened(target_class)
            return member_name in inherited_symbols

        # 2. 检查是否为用户工程的 Autoload 单例
        if singleton_name in self.project_scanner.autoloads:
            if singleton_name in self.project_scanner.custom_classes_meta:
                meta = self.project_scanner.custom_classes_meta[singleton_name]
                inherited = self.kb.dag.get_all_members_flattened(singleton_name)
                return (member_name in meta["members"]) or (member_name in inherited)
            return True

        return False

    def resolve(
        self,
        name: str,
        local_scope: set[str] | None = None,
        class_members: set[str] | None = None,
        base_class: str = "",
    ) -> dict[str, Any]:
        local_scope = local_scope or set()
        class_members = class_members or set()

        # 1. 局部变量 / 函数入参
        if name in local_scope:
            return {"status": "RESOLVED", "scope": "LOCAL", "type": "variable"}

        # 2. 当前脚本内部定义的属性、方法、信号
        if name in class_members:
            return {
                "status": "RESOLVED",
                "scope": "CLASS_MEMBER",
                "type": "member",
            }

        # 3. 沿 DAG 继承树查询祖先属性/方法
        if base_class:
            inherited_symbols = self.kb.dag.get_all_members_flattened(base_class)
            if name in inherited_symbols:
                return {
                    "status": "RESOLVED",
                    "scope": "INHERITED_MEMBER",
                    "ancestor": base_class,
                }

        # 4. 项目 Autoload
        if name in self.project_scanner.autoloads:
            return {
                "status": "RESOLVED",
                "scope": "PROJECT_AUTOLOAD",
                "source": self.project_scanner.autoloads[name],
            }

        # 5. 自定义 class_name
        if name in self.project_scanner.custom_classes_meta:
            return {
                "status": "RESOLVED",
                "scope": "CUSTOM_CLASS",
                "source": self.project_scanner.custom_classes_meta[name]["path"],
            }

        # 6. 引擎单例、内置类、全局函数
        engine_result = self.kb.query_symbol(name)
        if engine_result["status"] == "RESOLVED":
            engine_result["scope"] = "ENGINE_BUILTIN"
            return engine_result

        # 7. 未定义
        return {"status": "UNDEFINED", "scope": "UNKNOWN", "type": None}


class GDScriptLightweightAnalyzer:

    def __init__(self, resolver: GodotSymbolResolver):
        self.resolver = resolver

    def _strip_strings_and_comments(self, line: str) -> str:
        line = re.sub(r"#.*$", "", line)
        return re.sub(r'("[^"]*"|\'[^\']*\')', '""', line)

    def analyze_code(self, code_text: str) -> dict[str, Any]:
        lines = code_text.splitlines()

        # 1. 提取当前脚本继承的基类
        base_class = ""
        extends_match = re.search(
            r"^\s*extends\s+([A-Za-z0-9_]+)", code_text, re.MULTILINE
        )
        if extends_match:
            base_class = extends_match.group(1)

        # 2. 提取脚本内部声明的变量、方法、信号
        member_vars = set()
        class_methods = set()
        signals = set()

        var_pattern = re.compile(
            r"^\s*(?:@\w+\s+)*(?:var|const)\s+([A-Za-z0-9_]+)"
        )
        func_pattern = re.compile(r"^\s*func\s+([A-Za-z0-9_]+)")
        signal_pattern = re.compile(r"^\s*signal\s+([A-Za-z0-9_]+)")

        for line in lines:
            v_match = var_pattern.search(line)
            if v_match:
                member_vars.add(v_match.group(1))
            f_match = func_pattern.search(line)
            if f_match:
                class_methods.add(f_match.group(1))
            s_match = signal_pattern.search(line)
            if s_match:
                signals.add(s_match.group(1))

        class_members = member_vars | class_methods | signals

        # 3. 提取所有函数形参
        func_params = set()
        func_sig_pattern = re.compile(r"func\s+[A-Za-z0-9_]+\s*\(([^)]*)\)")
        for line in lines:
            for match in func_sig_pattern.finditer(line):
                for p in match.group(1).split(","):
                    p_clean = p.strip()
                    if p_clean:
                        p_name = re.split(r"[:=]", p_clean)[0].strip()
                        if p_name:
                            func_params.add(p_name)

        # 4. 提取局部声明变量
        local_vars = set()
        local_var_pattern = re.compile(r"\bvar\s+([A-Za-z0-9_]+)")
        for line in lines:
            for m in local_var_pattern.finditer(line):
                local_vars.add(m.group(1))

        all_known_local = local_vars | func_params

        # 5. GDScript 保留关键字与核心内置信号白名单
        keywords = {
            "extends",
            "class_name",
            "func",
            "var",
            "const",
            "signal",
            "enum",
            "if",
            "elif",
            "else",
            "for",
            "while",
            "return",
            "pass",
            "break",
            "continue",
            "self",
            "true",
            "false",
            "null",
            "void",
            "int",
            "float",
            "bool",
            "String",
            "and",
            "or",
            "not",
            "in",
            "is",
            "as",
            "super",
            "set",
            "get",
            "preload",
            "load",
            "assert",
            "await",
            "ready",
            "tree_entered",
            "tree_exited",
            "pressed",
            "button_down",
            "button_up",
            "mouse_entered",
            "mouse_exited",
            "area_entered",
            "area_exited",
            "body_entered",
            "body_exited",
            "gui_input",
            "changed",
        }

        diagnostics = []
        standalone_ident_pattern = re.compile(
            r"(?<![@.%\w])([A-Za-z_][A-Za-z0-9_]*)\b"
        )
        # 链式调用正则：捕获 Singleton.method / Singleton.property
        singleton_call_pattern = re.compile(
            r"\b([A-Za-z_][A-Za-z0-9_]*)\.([A-Za-z_][A-Za-z0-9_]*)\b"
        )

        for idx, raw_line in enumerate(lines, start=1):
            clean_line = self._strip_strings_and_comments(raw_line)
            if not clean_line.strip():
                continue

            # A. 校验单例链式调用成员的合法性
            for s_match in singleton_call_pattern.finditer(clean_line):
                obj_name, member_name = s_match.group(1), s_match.group(2)
                if (
                    obj_name in self.resolver.kb.singletons
                    or obj_name in self.resolver.project_scanner.autoloads
                ):
                    if not self.resolver.resolve_singleton_member(obj_name, member_name):
                        diagnostics.append(
                            {
                                "line": idx,
                                "symbol": f"{obj_name}.{member_name}",
                                "message": f"单例 '{obj_name}' 不存在成员或方法: '{member_name}'",
                            }
                        )

            # B. 校验独立标识符
            tokens = standalone_ident_pattern.findall(clean_line)
            for token in tokens:
                if token in keywords or token.isdigit():
                    continue

                res = self.resolver.resolve(
                    token,
                    local_scope=all_known_local,
                    class_members=class_members,
                    base_class=base_class,
                )
                if res["status"] == "UNDEFINED":
                    diagnostics.append(
                        {
                            "line": idx,
                            "symbol": token,
                            "message": f"未定义的符号或引用: '{token}'",
                        }
                    )

        return {
            "class_members": list(class_members),
            "diagnostics": diagnostics,
        }