"""预览界面里用 Pillow 自绘的图形（外观图标 / 下拉箭头 / 开关 / 开关动画中间帧）。

界面里的这些图形是画出来的，光看代码看不出形状对不对；而开发机不一定装了
tkinter（装了也未必想开窗口）。这个脚本绕开 customtkinter，直接调用
``mcrender/ui/icons.py`` 把每个图形按浅 / 深两种主题渲染出来，拼成一张 PNG。

    python tools/preview_ui_icons.py [输出路径]

默认写到系统临时目录并打印完整路径。顺带做两件自检：

- 量开关的实测尺寸与留白对称性（改过 ``icons.TRACK_W / TRACK_H / KNOB`` 后跑一下，
  就知道有没有画歪）；
- 逐点核对缓动曲线与颜色插值（``shape.fast_out_slow_in`` 的端点 / 单调性、
  ``soft_in_out`` 的两端速度与中心对称性、``icons.mix`` 在 t=0/1 必须返回原色）——
  动画的首末帧要和静态端点**逐像素一致**，这几条是那个前提的离线保证。
"""
from __future__ import annotations

import ast
import importlib.util
import os
import sys
import tempfile
import types

from PIL import Image, ImageChops, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UI = os.path.join(ROOT, "mcrender", "ui")

UP = 4          # 放大倍数（LANCZOS，用来看真实形状）
PAD = 18
LABEL_H = 16


def load_icons():
    """载入 icons.py，同时把 theme 换成一个只读色板的小桩。

    icons.py 里写的是 ``from . import theme``，而包 ``mcrender.ui`` 的
    ``__init__`` 会连带导入 customtkinter —— 所以这里造一个假的包，
    让相对导入落到桩上。色板直接从 theme.py 的源码里解析，保证用的是真值。
    """
    with open(os.path.join(UI, "theme.py"), encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    colors = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                getattr(t, "id", "") == "COLORS" for t in node.targets):
            colors = ast.literal_eval(node.value)
    if not colors:
        raise SystemExit("没从 theme.py 里解析到 COLORS")

    pkg = types.ModuleType("_icons_stub")
    pkg.__path__ = [UI]
    sys.modules["_icons_stub"] = pkg

    theme = types.ModuleType("_icons_stub.theme")
    theme.COLORS = colors

    def color(name, dark=None):
        pair = colors[name]
        return pair if dark is None else pair[1 if dark else 0]

    theme.color = color
    sys.modules["_icons_stub.theme"] = theme

    spec = importlib.util.spec_from_file_location("_icons_stub.icons",
                                                 os.path.join(UI, "icons.py"))
    module = importlib.util.module_from_spec(spec)
    sys.modules["_icons_stub.icons"] = module
    spec.loader.exec_module(module)
    return module, colors


def main(argv) -> int:
    icons, colors = load_icons()

    specs = [
        ("跟随系统", "appear", ("system",)),
        ("浅色", "appear", ("light",)),
        ("深色", "appear", ("dark",)),
        ("下拉箭头", "chevron", ()),
        ("开关 关", "switch", (False, False)),
        ("开关 悬停", "switch", (False, True)),
        ("开关 开", "switch", (True, False)),
        # 切换动画的中间帧：横轴是**线性时间进度**，位置取自缓动、颜色取自线性。
        # 算式直接找控件的同一份（shape.travel_frame），不在这里抄一遍。
        ("动画 25%", "switch_anim", (0.25,)),
        ("动画 50%", "switch_anim", (0.50,)),
        ("动画 75%", "switch_anim", (0.75,)),
    ]

    def image(kind, dark, args):
        if kind == "appear":
            return icons.appearance_pair(args[0])[1 if dark else 0]
        if kind == "chevron":
            return icons.chevron_pair()[1 if dark else 0]
        if kind == "switch_anim":
            progress, travel = icons.shape.travel_frame(args[0], 0.0, 1.0)
            return icons.switch_pair(True, False,
                                     progress=progress, travel=travel)[1 if dark else 0]
        return icons.switch_pair(args[0], args[1])[1 if dark else 0]

    band = []
    for dark in (False, True):
        items = []
        for name, kind, args in specs:
            img = image(kind, dark, args)
            items.append((name, img, img.resize((img.width * UP, img.height * UP),
                                                Image.LANCZOS)))
        band.append((colors["window"][1 if dark else 0],
                     colors["text"][1 if dark else 0], items))

    width = 16
    for _bg, _fg, items in band:
        width = max(width, 16 + sum(big.width + PAD for _n, _o, big in items))
    height = sum(PAD + LABEL_H + max(b.height for _n, _o, b in items) + PAD
                 for _bg, _fg, items in band)

    sheet = Image.new("RGB", (width, height), colors["window"][0])
    draw = ImageDraw.Draw(sheet)
    y = 0
    for bg, fg, items in band:
        band_h = PAD + LABEL_H + max(b.height for _n, _o, b in items) + PAD
        draw.rectangle([0, y, width, y + band_h], fill=bg)
        x = 16
        base = y + PAD + LABEL_H
        for name, small, big in items:
            draw.text((x, y + PAD - 2), name, fill=fg)
            sheet.paste(big, (x, base), big)                 # 放大版看形状
            sheet.paste(small, (x, base + big.height + 4), small)   # 原始尺寸看实际观感
            x += big.width + PAD
        y += band_h

    out = (argv[1] if len(argv) > 1
           else os.path.join(tempfile.gettempdir(), "mcrender_ui_icons.png"))
    sheet.save(out)
    print(f"预览图已写出：{out}  ({sheet.width}×{sheet.height})")

    # 开关尺寸核对：设计值 vs 实测。LANCZOS 缩回来时边缘是抗锯齿的，
    # 所以按纯色匹配量出来的直径会比设计值小约 1px —— 看趋势，别当绝对值。
    off = icons.switch_pair(False, False)[0]
    w, h = off.size
    probe = off.load()
    knob = [(x, y) for y in range(h) for x in range(w)
            if probe[x, y][3] > 200 and abs(probe[x, y][0] - 0x5C) < 14
            and abs(probe[x, y][1] - 0x5C) < 14 and abs(probe[x, y][2] - 0x5C) < 14]
    print(f"开关轨道 {w}×{h}（设计 {icons.TRACK_W}×{icons.TRACK_H}），"
          f"滑块设计直径 {icons.KNOB}（占轨道 "
          f"{icons.KNOB / icons.TRACK_H * 100:.0f}%）")
    if knob:
        xs = [x for x, _y in knob]
        ys = [y for _x, y in knob]
        size = max(ys) - min(ys) + 1
        left, top = min(xs), min(ys)
        print(f"  实测滑块直径 {size}px，左留白 {left}px vs 上下留白 {top}px"
              f"  {'对称 ✓' if abs(left - top) <= 1 else '不对称 ✗'}")

    # ------------------------------------------------------- 缓动 / 颜色自检
    # 动画的首末帧必须与静态端点**逐像素一致** —— 否则切换动画收尾那一下会跳。
    # 这两条是那个前提的离线保证：曲线端点必须是 0/1，颜色插值端点必须是原色。
    sh = icons.shape
    print("\n=== 缓动曲线（shape.fast_out_slow_in）===")
    vals = [sh.fast_out_slow_in(i / 20) for i in range(21)]
    mono = all(vals[i] <= vals[i + 1] + 1e-9 for i in range(20))
    print(f"  f(0)={vals[0]:.4f}  f(1)={vals[-1]:.4f}  单调递增 {mono}"
          f"  f(0.5)={vals[10]:.4f}（> 0.5 = 起步快）")
    bad = []
    if abs(vals[0]) > 1e-6 or abs(vals[-1] - 1.0) > 1e-6 or not mono:
        bad.append("fast_out_slow_in 端点 / 单调性不对")
    tc = [sh.ease_out_cubic(i / 20) for i in range(21)]
    print(f"  ease_out_cubic: f(0)={tc[0]:.4f}  f(1)={tc[-1]:.4f}  f(0.5)={tc[10]:.4f}")
    if abs(tc[0]) > 1e-6 or abs(tc[-1] - 1.0) > 1e-6:
        bad.append("ease_out_cubic 端点不对")

    # 下拉走的就是这条：两端速度都得是 0（起步不猛冲），且中心对称 —— 收起靠
    # ``1 - soft_in_out(t)`` 复用同一段运动，不对称的话收起来的手感就和展开对不上。
    print("\n=== 缓动曲线（shape.soft_in_out，下拉展开 / 收起用）===")
    so = [sh.soft_in_out(i / 20) for i in range(21)]
    mono_so = all(so[i] <= so[i + 1] + 1e-9 for i in range(20))
    start_slope = (so[1] - so[0]) * 20.0
    end_slope = (so[20] - so[19]) * 20.0
    mirror = max(abs(so[i] - (1.0 - so[20 - i])) for i in range(21))
    print(f"  f(0)={so[0]:.4f}  f(1)={so[-1]:.4f}  单调递增 {mono_so}  f(0.5)={so[10]:.4f}")
    print(f"  起步斜率≈{start_slope:.2f}（0 = 不猛冲）  收尾斜率≈{end_slope:.2f}"
          f"  中心对称误差 {mirror:.2e}")
    if abs(so[0]) > 1e-6 or abs(so[-1] - 1.0) > 1e-6 or not mono_so:
        bad.append("soft_in_out 端点 / 单调性不对")
    if start_slope > 0.6 or end_slope > 0.6:
        bad.append(f"soft_in_out 起步 / 收尾太猛（{start_slope:.2f} / {end_slope:.2f}）")
    if mirror > 1e-4:
        bad.append(f"soft_in_out 不是中心对称（误差 {mirror:.2e}），收起会与展开不一致")

    print("\n=== 颜色插值（icons.mix 必须从两端原色出发 / 收束）===")
    for name in ("accent", "switch_stroke", "switch_knob", "on_accent", "control_hover"):
        for i, dark in enumerate((False, True)):
            token = colors[name][i]
            if (icons.mix(token, "#000000", 0.0).upper() != token.upper()
                    or icons.mix("#000000", token, 1.0).upper() != token.upper()):
                bad.append(f"mix 端点不等于原色：{name}[{'深' if dark else '浅'}]")
    if not bad:
        print("  ✓ 每个令牌在 t=0 / t=1 处都返回原色")

    print("\n=== 动画首末帧 vs 静态端点（逐像素）===")
    for dark in (False, True):
        for on in (False, True):
            base = icons.switch_pair(on, False)[1 if dark else 0]
            t = 1.0 if on else 0.0
            anim = icons.switch_pair(on, False, progress=t, travel=t)[1 if dark else 0]
            same = ImageChops.difference(base.convert("RGBA"),
                                         anim.convert("RGBA")).getbbox() is None
            print(f"  {'深' if dark else '浅'}色 on={on}: "
                  f"{'逐像素一致 ✓' if same else '有差异 ✗'}")
            if not same:
                bad.append(f"动画端点与静态端点不一致：dark={dark} on={on}")

    if bad:
        for line in bad:
            print(f"  ✗ {line}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
