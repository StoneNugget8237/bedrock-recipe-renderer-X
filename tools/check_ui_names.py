"""检查 `mcrender/ui` 里有没有定义**与 Tk 祖先类同名**的私有成员。

    python tools/check_ui_names.py

为什么需要这个：`FluentComboBox` / `FluentSwitch` / `MainWindow` 都是 Tk 控件的子类，
一旦自己定义了与**祖先类**同名的私有成员，就会悄悄覆盖父类内部实现，而且只在特定
时机炸，编译期完全看不出来：

- ``self._bind`` —— ``tkinter.Misc`` 的内部方法，覆盖后 ``super().__init__()`` 里的
  ``self._bind('bind', ...)`` 直接 TypeError；
- ``self._canvas`` —— ``CTkFrame`` 内部用来画圆角矩形的画布，被置空后 CTk 收到
  ``<Configure>`` 去重绘，`_draw()` 里 ``self._canvas.winfo_exists()`` 抛 AttributeError。

判定**只沿真实继承链**回溯：`FluentComboBox → CTkFrame → CTkBaseClass →
ScalingBaseClass / tkinter.Frame → Widget → BaseWidget → Misc → Pack/Place/Grid`。
与 CTkLabel、CTkButton 这类**不在链上**的类同名（例如 `_text`、`_command`）不算冲突。

例外：**有意覆写**不算冲突——方法体内调了 ``super().<同名>(...)`` 的，说明是先让父类
逻辑跑完再叠加自己的行为（如 `_set_appearance_mode` 换主题时重画底板），会标成
「有意覆写」而不是撞名。
"""
from __future__ import annotations

import argparse
import ast
import glob
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UI = os.path.join(ROOT, "mcrender", "ui")

CTK_RELATIVE = (
    os.path.join("windows", "ctk_tk.py"),
    os.path.join("windows", "ctk_toplevel.py"),
    os.path.join("windows", "widgets", "ctk_frame.py"),
    os.path.join("windows", "widgets", "ctk_label.py"),
    os.path.join("windows", "widgets", "ctk_button.py"),
    os.path.join("windows", "widgets", "ctk_entry.py"),
    os.path.join("windows", "widgets", "core_widget_classes",
                 "ctk_base_class.py"),
    os.path.join("windows", "widgets", "scaling", "scaling_base_class.py"),
)


def is_private(name: str) -> bool:
    """单下划线开头的私有名；dunder（__init__ 之类）不算冲突。"""
    return name.startswith("_") and not name.startswith("__")


def members_of(node: ast.ClassDef) -> set:
    names = set()
    for stmt in ast.walk(node):
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if is_private(stmt.name):
                names.add(stmt.name)
        elif isinstance(stmt, (ast.Assign, ast.AnnAssign)):
            targets = (stmt.targets if isinstance(stmt, ast.Assign)
                       else [stmt.target])
            for target in targets:
                if (isinstance(target, ast.Attribute)
                        and isinstance(target.value, ast.Name)
                        and target.value.id == "self"
                        and is_private(target.attr)):
                    names.add(target.attr)
    return names


def super_override_names(node: ast.ClassDef) -> set:
    """本类里**有意覆写**父类的方法名。

    判据：同名方法体内出现了 ``super().<同名>(...)`` 调用。这类覆写是安全的——先让父类
    逻辑跑完，再叠加自己的行为（例如 ``_set_appearance_mode`` 里先 ``super()`` 再重画底板）。
    只有**没调 super()** 的同名方法、以及同名的 ``self._x`` 属性，才是真会把父类实现
    顶掉的危险撞名。
    """
    out = set()
    for stmt in node.body:
        if not isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        name = stmt.name
        if not is_private(name):
            continue
        for sub in ast.walk(stmt):
            if (isinstance(sub, ast.Call)
                    and isinstance(sub.func, ast.Attribute)
                    and sub.func.attr == name
                    and isinstance(sub.func.value, ast.Call)
                    and isinstance(sub.func.value.func, ast.Name)
                    and sub.func.value.func.id == "super"):
                out.add(name)
                break
    return out


def bases_of(node: ast.ClassDef) -> list:
    out = []
    for base in node.bases:
        if isinstance(base, ast.Name):
            out.append(base.id)
        elif isinstance(base, ast.Attribute):
            out.append(base.attr)
    return out


def scan(path: str) -> dict:
    """文件里的每个类 → {基类名, 私有成员}。"""
    try:
        with open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
    except Exception:
        return {}
    out = {}
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            out[node.name] = {"bases": bases_of(node),
                              "members": members_of(node),
                              "overrides": super_override_names(node)}
    return out


def ancestors(cls: str, registry: dict, seen=None) -> set:
    """沿继承链收集全部祖先类名（含自己）。"""
    seen = seen or set()
    if cls in seen or cls not in registry:
        return seen
    seen.add(cls)
    for base in registry[cls]["bases"]:
        ancestors(base, registry, seen)
    return seen


def find_tkinter_file() -> str:
    for base in (sys.prefix, os.path.dirname(os.path.dirname(sys.executable))):
        candidate = os.path.join(base, "Lib", "tkinter", "__init__.py")
        if os.path.isfile(candidate):
            return candidate
    patterns = (r"C:\Program Files\Python*\Lib\tkinter\__init__.py",
                r"C:\Python3*\Lib\tkinter\__init__.py",
                os.path.join(os.path.expanduser("~"), "AppData", "Local",
                             "Programs", "Python", "Python*", "Lib",
                             "tkinter", "__init__.py"))
    for pattern in patterns:
        hits = sorted(glob.glob(pattern))
        if hits:
            return hits[-1]
    return ""


def find_ctk_dir() -> str:
    try:
        import customtkinter
        return os.path.dirname(customtkinter.__file__)
    except Exception:
        pass
    hits = sorted(glob.glob(os.path.join(
        os.path.expanduser("~"), ".workbuddy", "binaries", "python", "envs", "*",
        "Lib", "site-packages", "customtkinter")))
    return hits[-1] if hits else ""


def main(argv) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stdlib", help="tkinter/__init__.py 路径")
    parser.add_argument("--ctk", help="customtkinter 包目录")
    args = parser.parse_args(argv[1:])

    registry = {}
    tk_path = args.stdlib or find_tkinter_file()
    if tk_path:
        registry.update(scan(tk_path))
        print(f"tkinter : {tk_path}")
    else:
        print("tkinter : 没找到源码，跳过（用 --stdlib 指定）")

    ctk_dir = args.ctk or find_ctk_dir()
    if ctk_dir:
        found = 0
        for rel in CTK_RELATIVE:
            classes = scan(os.path.join(ctk_dir, rel))
            registry.update(classes)
            found += len(classes)
        print(f"ctk     : {ctk_dir}（{found} 个类）")
    else:
        print("ctk     : 没找到 customtkinter，跳过（用 --ctk 指定）")

    if not registry:
        print("\n没有任何父类信息可对照，退出。")
        return 0

    print()
    clashes = []
    for path in sorted(glob.glob(os.path.join(UI, "*.py"))):
        filename = os.path.basename(path)
        for cls, info in sorted(scan(path).items()):
            chain = set()
            for base in info["bases"]:
                chain |= ancestors(base, registry)
            chain.discard(cls)
            if not chain:
                print(f"  - {cls}（{filename}）没有可解析的基类，跳过")
                continue
            danger = set()
            for name in chain:
                danger |= registry[name]["members"]
            hits = sorted(info["members"] & danger)
            # 有意覆写（调了 super()）不算冲突，只提示
            overrides = info.get("overrides", set())
            intentional = sorted(h for h in hits if h in overrides)
            hits = [h for h in hits if h not in overrides]
            if hits:
                for hit in hits:
                    owners = sorted(n for n in chain if hit in registry[n]["members"])
                    clashes.append((filename, cls, hit, owners))
                print(f"  ✗ {cls}（{filename}）撞名: {hits}")
            elif intentional:
                print(f"  ✓ {cls}（{filename}）祖先 {len(chain)} 个，"
                      f"{len(info['members'])} 个私有成员，无冲突"
                      f"（有意覆写: {intentional}）")
            else:
                print(f"  ✓ {cls}（{filename}）祖先 {len(chain)} 个，"
                      f"{len(info['members'])} 个私有成员，无冲突")

    if clashes:
        print("\n=== 冲突明细 ===")
        for filename, cls, name, owners in clashes:
            print(f"  {filename}: {cls}.{name}  <-- 与 {', '.join(owners)} 同名")
        print("\n改个名就行（例如 _bind → _add_binding、_canvas → _menu_canvas），"
              "记得连同 self.xxx 的引用一起改。")
        return 1

    print("\n结论: 无撞名 ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
