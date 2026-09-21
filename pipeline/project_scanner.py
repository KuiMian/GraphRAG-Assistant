from pathlib import Path
import re


class GodotProjectScanner:

    def __init__(self, project_root: str | Path | None = None):
        self.project_root = Path(project_root) if project_root else None
        self.autoloads: dict[str, str] = {}
        # class_name -> {"path": str, "extends": str, "members": set[str]}
        self.custom_classes_meta: dict[str, dict] = {}

        if self.project_root and self.project_root.exists():
            self.rescan()

    def set_project_root(self, project_root: str | Path):
        self.project_root = Path(project_root)
        self.rescan()

    def rescan(self):
        self.autoloads.clear()
        self.custom_classes_meta.clear()
        if not self.project_root or not self.project_root.exists():
            return

        print(f"[*] 正在扫描项目工程: {self.project_root.resolve()} ...")
        self._scan_autoloads()
        self._scan_custom_classes()
        print(f"[✓] 项目扫描完毕:")
        print(f"    - Autoload 单例: {len(self.autoloads)} 个")
        print(f"    - 自定义全局类: {len(self.custom_classes_meta)} 个")

    def _scan_autoloads(self):
        project_file = self.project_root / "project.godot"
        if not project_file.exists():
            return
        try:
            content = project_file.read_text(encoding="utf-8", errors="ignore")
            match = re.search(r"\[autoload\]\s*([\s\S]*?)(?=\n\[|$)", content)
            if match:
                for line in match.group(1).splitlines():
                    line = line.strip()
                    if line and not line.startswith(";") and "=" in line:
                        parts = line.split("=", 1)
                        name = parts[0].strip()
                        path = parts[1].strip().strip('"').replace("*", "")
                        self.autoloads[name] = path
        except Exception as e:
            print(f"[!] 扫描 Autoload 失败: {e}")

    def _scan_custom_classes(self):
        cname_pattern = re.compile(
            r"^\s*class_name\s+([A-Za-z0-9_]+)", re.MULTILINE
        )
        extends_pattern = re.compile(
            r"^\s*extends\s+([A-Za-z0-9_]+)", re.MULTILINE
        )
        member_pattern = re.compile(
            r"^\s*(?:@\w+\s+)*(?:var|const|func|signal)\s+([A-Za-z0-9_]+)",
            re.MULTILINE,
        )

        for gd_file in self.project_root.rglob("*.gd"):
            if ".godot" in gd_file.parts:
                continue
            try:
                text = gd_file.read_text(encoding="utf-8", errors="ignore")
                c_match = cname_pattern.search(text)
                if c_match:
                    c_name = c_match.group(1)
                    e_match = extends_pattern.search(text)
                    extends_class = (
                        e_match.group(1) if e_match else "RefCounted"
                    )
                    members = set(member_pattern.findall(text))

                    self.custom_classes_meta[c_name] = {
                        "path": str(gd_file.resolve()),
                        "extends": extends_class,
                        "members": members,
                    }
            except Exception:
                continue