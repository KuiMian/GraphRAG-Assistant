from pathlib import Path
import sys
import time

from symbol_resolver import (
    GDScriptLightweightAnalyzer,
    GodotSymbolResolver,
)


def scan_godot_project(project_path: str | Path, kb_path: str | Path):
    project_dir = Path(project_path).resolve()
    if not project_dir.exists():
        print(f"[!] 找不到工程目录: {project_dir}")
        return

    print("=" * 60)
    print(f"🚀 开始扫描 Godot 工程: {project_dir.name}")
    print(f"📁 路径: {project_dir}")
    print("=" * 60)

    start_time = time.time()

    resolver = GodotSymbolResolver(kb_path=kb_path, project_root=project_dir)
    analyzer = GDScriptLightweightAnalyzer(resolver)

    gd_files = [
        f for f in project_dir.rglob("*.gd") if ".godot" not in f.parts
    ]
    print(f"\n[*] 共发现 {len(gd_files)} 个 GDScript 脚本，开始分析...\n")

    total_issues = 0
    scanned_files = 0
    problematic_files = 0

    for gd_file in gd_files:
        rel_path = gd_file.relative_to(project_dir)
        try:
            content = gd_file.read_text(encoding="utf-8", errors="ignore")
        except Exception as e:
            print(f"[!] 无法读取文件: {rel_path} ({e})")
            continue

        result = analyzer.analyze_code(content)
        diagnostics = result.get("diagnostics", [])
        scanned_files += 1

        if diagnostics:
            problematic_files += 1
            print(
                f"❌ [{rel_path}] 发现 {len(diagnostics)} 处疑似未定义引用:"
            )
            for diag in diagnostics:
                print(f"    第 {diag['line']:>3} 行 -> {diag['message']}")
                total_issues += 1
            print("-" * 50)

    cost_time = time.time() - start_time

    print("\n" + "=" * 60)
    print("📊 扫描结果汇总")
    print("=" * 60)
    print(f"耗时: {cost_time:.2f} 秒")
    print(f"扫描脚本数: {scanned_files} / {len(gd_files)}")
    print(f"存在疑问的文件数: {problematic_files}")
    print(f"未定义符号总数: {total_issues}")


if __name__ == "__main__":
    current_dir = Path(__file__).resolve().parent
    default_kb = current_dir.parent / "assets" / "godot_enriched_kb.json"

    target_project = (
        sys.argv[1]
        if len(sys.argv) > 1
        else r"D:\DevTools\Godot\01Games\autobattler1"
    )

    scan_godot_project(target_project, default_kb)