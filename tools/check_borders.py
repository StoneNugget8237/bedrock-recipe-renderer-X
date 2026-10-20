"""检查真实窗口里控件的**描边质量**：四条边是否都在 + 圆角是否抗锯齿。

    python tools/check_borders.py [--appearance light|dark]

两件事：

1. **四边描边**。CustomTkinter 的圆角描边是「几个圆 + 两个大矩形」拼的，控件宽高还会被
   **取成偶数**（``math.floor(w/2)*2``），描边又**骑在路径上**画（一半在控件外）。
   在 175% 这类奇缩放下，控件物理高度会落回偶数、矮掉 0.5px，**底边那条描边有可能整条
   落到控件外** —— 肉眼看就是「上/左/右有线、下边没有」。离线读代码完全看不出来，
   必须抓真实窗口的像素来判。

2. **圆角是否抗锯齿**。``tk.Canvas`` 没有抗锯齿，圆角要么是硬阶梯、要么只有两三种过渡色。
   判据：取角上 ``N×N`` 的方块数**颜色种数** —— 抗锯齿的圆角有一圈渐变，颜色明显多。

做法：先把界面真的建起来，遍历出待检控件、记下它们相对主窗口客户区的矩形；再用
Win32 ``PrintWindow`` 把窗口画成一张位图；最后逐控件沿四条边的**中点**往里取若干像素
比色（与「内部底色」的最大单通道差超过阈值就算有线），并在角上数颜色。

解释器缺 tkinter 时会自动换一个（复用 ``smoke_ui`` 的查找逻辑）。
"""
from __future__ import annotations

import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from smoke_ui import ensure_gui_python          # noqa: E402

ensure_gui_python(os.path.abspath(__file__))

from mcrender import app                        # noqa: E402
from mcrender.ui import theme                   # noqa: E402
from mcrender.ui.window import MainWindow       # noqa: E402
from screenshot_ui import grab                  # noqa: E402

THRESHOLD = 12          # 与内部底色的「最大单通道差」超过它就认定是另一种颜色
                        # （深色模式下描边 #3F3F3F 对底色 #2D2D2D 只差 18，阈值不能设太高）
PROBE = 5               # 每条边往里探测几个像素
INSET = 7               # 取「内部底色」参照值时，从边往里多少像素
CORNER_N = 14           # 角上数颜色的方块边长
SMOOTH_MIN = 8          # 角上颜色种数 ≥ 它才算抗锯齿（硬阶梯只有 2~4 种）


def walk(widget):
    yield widget
    for child in widget.winfo_children():
        yield from walk(child)


def label_of(widget) -> str:
    kind = type(widget).__name__
    text = ""
    for getter in (lambda: widget.cget("text"), lambda: widget._text_label.cget("text")):
        try:
            text = str(getter() or "")
        except Exception:
            text = ""
        if text:
            break
    text = text.replace("\n", " ")[:22]
    return f"{kind}「{text}」" if text else kind


def num(widget, attr) -> float:
    try:
        return float(widget.cget(attr))
    except Exception:
        try:
            return float(getattr(widget, "_" + attr))
        except Exception:
            return 0.0


def has_visible_fill(widget) -> bool:
    """控件自己有没有可见底色。透明容器（布局用的 CTkFrame、开关外壳）不用查圆角 ——
    它们没有「面」，角上本来就是均匀的背景色，数出来恒为 1，只会造成假报。"""
    try:
        color = widget.cget("fg_color")
    except Exception:
        return True                     # 取不到就按「有底色」处理，宁可多查
    if color is None:
        return False
    if isinstance(color, str):
        return color.lower() != "transparent"
    return True


def collect(root):
    rx, ry = root.winfo_rootx(), root.winfo_rooty()
    cw, ch = root.winfo_width(), root.winfo_height()
    out = []
    for w in walk(root):
        if w is root or not w.winfo_ismapped():
            continue
        bw, cr = num(w, "border_width"), num(w, "corner_radius")
        if bw <= 0 and cr <= 0:
            continue
        if not has_visible_fill(w):
            continue
        try:
            x = w.winfo_rootx() - rx
            y = w.winfo_rooty() - ry
            ww, hh = w.winfo_width(), w.winfo_height()
        except Exception:
            continue
        if ww < CORNER_N * 2 or hh < CORNER_N * 2:
            continue
        if x + ww <= 0 or y + hh <= 0 or x >= cw or y >= ch:
            continue
        out.append((label_of(w), bw > 0,
                    (max(0, x), max(0, y), min(cw - 1, x + ww - 1), min(ch - 1, y + hh - 1))))
    return out


def rgb(img, x, y):
    return img.getpixel((int(x), int(y)))[:3]


def far(a, b) -> int:
    return max(abs(a[i] - b[i]) for i in range(3))


def edge_has_border(img, x, y, dx, dy, ix, iy):
    ref = rgb(img, ix, iy)
    for i in range(PROBE):
        color = rgb(img, x + dx * i, y + dy * i)
        if far(color, ref) > THRESHOLD:
            return True, "#%02X%02X%02X" % color
    return False, "#%02X%02X%02X" % ref


def check_edges(img, x0, y0, x1, y1):
    cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
    return {
        "上": edge_has_border(img, cx, y0, 0, 1, cx, min(y1, y0 + INSET)),
        "下": edge_has_border(img, cx, y1, 0, -1, cx, max(y0, y1 - INSET)),
        "左": edge_has_border(img, x0, cy, 1, 0, min(x1, x0 + INSET), cy),
        "右": edge_has_border(img, x1, cy, -1, 0, max(x0, x1 - INSET), cy),
    }


def corner_counts(img, x0, y0, x1, y1, n=CORNER_N):
    counts = []
    for cx, cy in ((x0, y0), (x1 - n + 1, y0), (x0, y1 - n + 1), (x1 - n + 1, y1 - n + 1)):
        seen = set()
        for y in range(cy, cy + n):
            for x in range(cx, cx + n):
                seen.add(rgb(img, x, y))
        counts.append(len(seen))
    return counts


def run(appearance: str) -> int:
    theme.configure_appearance(appearance)
    win = MainWindow([], cfg=dict(app.load_config() or {}))
    win.cfg["appearance"] = appearance
    theme.configure_appearance(appearance)
    root = win.root
    root.geometry("920x740+80+60")
    for _ in range(10):
        root.update()

    targets = collect(root)
    img = grab(root)
    if img is None:
        print("! 抓不到窗口内容", file=sys.stderr)
        return 1

    print(f"外观 {appearance}：{len(targets)} 个控件（带描边或圆角）\n")
    edge_bad, corner_bad = [], []
    for name, has_border, rect in targets:
        line = f"  {name}"
        if has_border:
            edges = check_edges(img, *rect)
            miss = [k for k in ("上", "下", "左", "右") if not edges[k][0]]
            if miss:
                edge_bad.append((name, miss))
                line += f"  ✗ 缺 {'/'.join(miss)}边"
            else:
                line += "  四边描边 ✓"
        else:
            line += "  （无描边）"
        counts = corner_counts(img, *rect)
        worst = min(counts)
        if worst < SMOOTH_MIN:
            corner_bad.append((name, counts))
            line += f"  ✗ 圆角 {counts}（最差 {worst} 色，像硬阶梯）"
        else:
            line += f"  圆角 {counts} 色 ✓"
        print(line)

    win.close()
    print()
    if edge_bad or corner_bad:
        if edge_bad:
            print(f"缺边 {len(edge_bad)} 个：" + ", ".join(f"{n}({'/'.join(m)})" for n, m in edge_bad))
        if corner_bad:
            print(f"圆角不抗锯齿 {len(corner_bad)} 个：" + ", ".join(n for n, _ in corner_bad))
        return 1
    print(f"结论：{len(targets)} 个控件四边描边齐全、圆角都经过抗锯齿 ✓")
    return 0


def main(argv) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--appearance", choices=("light", "dark"), default="light")
    args = parser.parse_args(argv[1:])
    return run(args.appearance)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
