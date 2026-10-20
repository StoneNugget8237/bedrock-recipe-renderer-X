"""合成表生成器 —— 把 recipe json / 文件夹 / .mcaddon .mcpack .zip 拖进来，直接出合成表图片。

这里只是**入口**，真正的实现分两层：

- :mod:`mcrender.app`     —— 路径、配置、附加包处理、批量出图（不依赖任何 GUI 库）
- :mod:`mcrender.ui`      —— CustomTkinter / Fluent 界面

命令行（无窗口，只走 ``mcrender.app``，不需要装 customtkinter）：

    mcrender_gui.exe --headless <文件或目录> [...]
    mcrender_gui.exe --sync-vanilla <行为包目录>
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# 老版本把这些名字都定义在本文件里，保留转发，外部脚本不受影响
from mcrender.app import (ARCHIVE_EXTS, CONFIG_PATH, DEFAULTS, Generator,  # noqa: E402,F401
                          app_dir, bundle_dir, find_matching_rp, find_recipes,
                          icon_path, is_archive, load_config, manifest_info,
                          pack_kinds, save_config, seed_caches, unpack_archive)


def _bundled_vanilla_dirs() -> list:
    """内置的原版资源目录（随包携带的 assets/ui 与 assets/vanilla_extra）。"""
    out = []
    for base in (app_dir(), bundle_dir()):
        for name in ("ui", "vanilla_extra"):
            d = os.path.join(base, "assets", name)
            if os.path.isdir(d) and d not in out:
                out.append(d)
    return out


def sync_vanilla(args) -> int:
    """补全原版资源（不生成图片）。"""
    from mcrender.vanilla import VanillaSource

    cfg = load_config()
    src = VanillaSource(local_dirs=_bundled_vanilla_dirs() +
                                   ([cfg["vanilla"]] if cfg.get("vanilla") else []),
                        cache_dir=os.path.join(app_dir(), "vanilla_cache"),
                        allow_download=True, log=print)
    report = src.sync_all(log=print)
    passed = not report.get("failed")
    if passed:
        cfg["local_only"] = True
        save_config(cfg)
    print(f"纹理：清单 {report.get('listed', 0)}，新下载 {report.get('fetched', 0)}，"
          f"失败 {report.get('failed', 0)}，跳过 {len(report.get('skipped', []))}；"
          f"缓存 {src.cache_size_mb()} MB")
    for rel in (report.get("failed_list") or [])[:20]:
        print("  ✗ " + rel)
    print("已切换为纯本地模式（不再联网）" if passed else "仍有失败条目，未切换纯本地模式")
    for folder in [a for a in args if os.path.isdir(a)]:
        gen = Generator(cfg)
        info = gen.prefetch(folder)
        print(f"预取 {os.path.basename(folder)}：{info['recipes']} 条配方 / "
              f"{info['items']} 个物品，新下载 {info['downloaded']} 个文件，"
              f"仍未找到 {len(info['missing'])} 个")
        for ident in info["missing"][:15]:
            print("  ? " + ident)
        gen.cleanup()
    return 0


def main(argv) -> int:
    args = list(argv[1:])
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    seed_caches()

    if args and args[0] == "--sync-vanilla":
        return sync_vanilla(args[1:])

    if args and args[0] == "--headless":
        gen = Generator(load_config())
        try:
            ok = gen.run(args[1:])
        finally:
            gen.cleanup()
        return 0 if ok else 1

    # 只有真要开窗口时才 import 界面层（连带 customtkinter）
    from mcrender.ui import run_gui

    run_gui(args)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
