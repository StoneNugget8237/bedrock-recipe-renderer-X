"""把下拉的三段动画抽帧拼成一张「胶片图」（真机截的像素，不是离线推演）。

    python tools/preview_ui_anim.py [输出路径] [--appearance light|dark]

默认写 ``docs/anim_combo_light.png`` / ``docs/anim_combo_dark.png``（按外观自动取名）。

为什么要有它：动画好不好只有**看**才知道 —— 「以当前值那一行为中心向两侧延展」顺不顺、
四周那圈投影够不够柔和、收起是不是展开的镜像，``smoke_ui`` 那一节只能给出数字
（帧数、间隔、带子几何）。而这类判断改一次就要看一次，靠手快截图显然不现实。

做法：真建一个窗口，按**固定进度**把帧一格一格喂进去
（``_frame_expand`` / ``_frame_shadow`` / ``_frame_collapse``），每帧 ``PrintWindow``
抓一次窗口像素，按**同一个矩形**裁下来（字段 + 整个面板盒子，所以既看得到字段没动、
也看得到面板怎么长），三段各拼一行。进度写死 → 每次出来的图可比对，能拿两张图对拍
「这次改动到底改了什么」。

⚠️ 喂帧前必须把 ``_Motion`` 停掉（``_open`` / ``_close`` 都会自己起一段真动画），否则它
会在两次喂帧之间插进自己的帧，拼出来看着「顺序不对」。

解释器：要带 ``tkinter`` 且装了 ``customtkinter``（缺了会自动换一个，复用 ``smoke_ui``
的查找逻辑）。
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

from PIL import Image, ImageDraw                 # noqa: E402

from preview_ui_combo import load_font           # noqa: E402

# 0 = 起点、1 = 终点。三条进度序列的分工：
#   展开   —— 看「从当前值那一行向两侧长」以及淡入；
#   投影   —— 只在展开播完之后跑（见 FluentComboBox._fade_shadow_in），看那圈柔光；
#   收起   —— 必须是展开的镜像（上下两条边一起往里收）。
EXPAND_STEPS = (0.0, 0.15, 0.3, 0.45, 0.6, 0.75, 0.9, 1.0)
SHADOW_STEPS = (0.0, 0.25, 0.5, 0.75, 1.0)
COLLAPSE_STEPS = (0.0, 0.2, 0.4, 0.6, 0.8, 1.0)

VALUES = ["100%", "125%", "150%(推荐)", "175%", "200%", "225%"]
CURRENT = "175%"                # 与用户给的 Win11 截图一致：第 4 项（0 基下标 3）
GAP = 14                        # 帧与帧之间留的缝
PAD = 12                        # 图四周的留白
LABEL_H = 26                    # 每行上方放标签的高度
MARGIN = 18                     # 裁图时在「字段 + 画布」外再留一点


def grab_region(win, rect):
    """抓一帧窗口内容并裁出 ``rect``（**物理像素**，相对客户区）。

    刻意不复用 ``panel_image()``：那个只认 ``_menu_canvas``（展开态），而收起动画期间
    面板已经交给 ``_closing`` 了；这里按固定矩形裁，三段动画用的是同一块地方，
    拼出来的每一帧天然对齐（字段一动不动就是最好的参照物）。
    """
    from mcrender.ui import win32

    shot = win32.grab(win)
    if shot is None:
        return None
    return shot.crop(rect)


def main(argv) -> int:
    parser = argparse.ArgumentParser(description="下拉动画抽帧胶片图")
    parser.add_argument("out", nargs="?")
    parser.add_argument("--appearance", choices=("light", "dark"), default="light")
    args = parser.parse_args(argv[1:])

    import tkinter as tk

    import customtkinter as ctk

    from mcrender.ui import theme, widgets

    ctk.set_appearance_mode("Dark" if args.appearance == "dark" else "Light")
    root = ctk.CTk()
    root.geometry("560x620+120+60")
    dark = args.appearance == "dark"
    bg = theme.color("window", dark)
    bg_frame = ctk.CTkFrame(root, fg_color=bg, corner_radius=0)
    bg_frame.place(x=0, y=0, relwidth=1, relheight=1)
    root.update()

    fonts = theme.Fonts()
    # ⚠️ 字段要往下摆够：面板现在**盖在字段上**（选中那一行的中线对齐字段中线，见
    # ``FluentComboBox._anchor``），选中的是第 4 项，面板顶边会跑到字段上方约
    # ``pad + pad_v + 3 行``那么高 —— 摆太高会被「夹进客户区」那条规则顶下来，
    # 胶片图里就看不到真实的相对位置了。
    box = widgets.FluentComboBox(root, variable=tk.StringVar(value=CURRENT),
                                 values=VALUES, width=200, height=32,
                                 font=fonts.small, menu_font=fonts.menu)
    box.place(x=36, y=160)
    root.update()

    box._open()
    box._motion.cancel()                    # 自己喂帧，别让真动画插进来
    root.update()
    canvas = box._popup
    origin = box._reveal_origin

    # 裁图矩形 = 字段 ∪ 画布（**满尺寸**），再四周留 MARGIN —— 三段动画共用它。
    #
    # 画布现在**全程满尺寸、一个像素都不动**（裁剪靠三段拼贴，见
    # ``FluentComboBox._paint``），所以 ``canvas.winfo_*`` 在这里也能用；仍取 ``box._box``
    # 是因为它不依赖几何管理器有没有把 ``place`` 落下来，而且万一以后画布又要动，
    # 这里不会跟着坏掉。
    hx, hy = root.winfo_rootx(), root.winfo_rooty()
    cx, cy, cw, ch = box._box
    left = min(box.winfo_rootx(), hx + cx) - hx - MARGIN
    top = min(box.winfo_rooty(), hy + cy) - hy - MARGIN
    right = max(box.winfo_rootx() + box.winfo_width(), hx + cx + cw) - hx + MARGIN
    bottom = max(box.winfo_rooty() + box.winfo_height(), hy + cy + ch) - hy + MARGIN
    rect = (max(0, left), max(0, top), right, bottom)

    print(f"解释器：{sys.executable}")
    print(f"外观 {args.appearance}；画布 {cw}×{ch}"
          f"（含四周投影留白），裁剪区 {rect[2] - rect[0]}×{rect[3] - rect[1]}"
          f"（左上角相对客户区 ({rect[0]}, {rect[1]})），"
          f"露出带的原点 y={origin}（「{CURRENT}」那一行的中线，画布坐标）\n")

    rows = []

    def record_row(title, frames):
        if any(shot is None for _l, shot in frames):
            print("  ! 抓不到窗口内容，这一行跳过")
            return
        rows.append((title, frames))
        print(f"  {title}：{len(frames)} 帧 {[lb for lb, _s in frames]}")

    def outside_band_diff(shot, band, base):
        """数「露出带以外」有多少像素和展开前**不一样**（应当是 0）。

        这条是给「裁剪到底有没有落到屏幕上」兜底的：``_paint`` 那三段拼贴对不对，
        只能靠 ``frame_image()`` 逐像素断言（见 smoke_ui 的 [1b]）；抓屏这边只要证明
        **画布真的按那一帧重画了**就够 —— 而这一条一旦不为 0 就说明屏幕上的画面和
        算出来的帧不一致（拼贴贴错位置没盖上、或者 Tk 没重画）。
        """
        if shot is None or base is None:
            return None
        band_top, band_bottom = band
        canvas_y0 = cy - rect[1]        # 画布满矩形顶边落在这一格里的 y
        count = 0
        for y in range(shot.size[1]):
            canvas_y = y - canvas_y0
            if band_top <= canvas_y < band_bottom:
                continue
            for x in range(0, shot.size[0], 7):
                if shot.getpixel((x, y)) != base.getpixel((x, y)):
                    count += 1
        return count

    base_shot = grab_region(root, rect)     # 展开前的样子 = 带外应当逐像素等于它

    # ---- 第一段：展开（露出带从「当前值」那一行向两侧长 + 整张底图淡入）
    print("=== 展开（progress → 露出带）===")
    frames = []
    for p in EXPAND_STEPS:
        box._frame_expand(p)
        root.update()
        top_, bottom_ = box._reveal_span()
        shot = grab_region(root, rect)
        frames.append((f"{p:.2f}", shot))
        print(f"  p={p:<5} 露出带 y {top_}..{bottom_}（高 {bottom_ - top_}）"
              f"  中线 {(top_ + bottom_) / 2:.1f}  面板alpha={box._fade_alpha:.2f}"
              f"  投影={box._shadow_alpha:.2f}"
              f"  带外与展开前不同的像素={outside_band_diff(shot, (top_, bottom_), base_shot)}")
    record_row("展开", frames)

    # ---- 第二段：投影淡入（面板已经整个露出来，只是那圈柔光由 0 加到满）
    print("\n=== 投影淡入（展开之后紧接着的第二段）===")
    box._reveal = None
    frames = []
    for p in SHADOW_STEPS:
        box._frame_shadow(p)
        root.update()
        frames.append((f"{p:.2f}", grab_region(root, rect)))
        print(f"  p={p:<5} 投影不透明度={box._shadow_alpha:.2f}")
    record_row("投影淡入", frames)

    # ---- 第三段：收起（必须是展开的镜像：上下两条边一起往里收）
    print("\n=== 收起（progress → 露出带）===")
    box._close()                            # 真走一次收起：画布交给 _closing，底图跟着交接
    box._motion.cancel()
    root.update()
    frames = []
    for p in COLLAPSE_STEPS:
        box._frame_collapse(p)
        root.update()
        top_, bottom_ = box._reveal_span()
        frames.append((f"{p:.2f}", grab_region(root, rect)))
        print(f"  p={p:<5} 露出带 y {top_}..{bottom_}（高 {bottom_ - top_}）"
              f"  中线 {(top_ + bottom_) / 2:.1f}  面板alpha={box._fade_alpha:.2f}")
    record_row("收起", frames)

    if not rows:
        return 1

    # ---- 拼接：一行一段，段名在行首、帧号在每帧上方
    font = load_font(15)
    cell_w = max(shot.size[0] for _t, shots in rows for _l, shot in shots)
    cell_h = max(shot.size[1] for _t, shots in rows for _l, shot in shots)
    cols = max(len(shots) for _t, shots in rows)
    width = PAD * 2 + cols * cell_w + (cols - 1) * GAP
    height = PAD * 2 + len(rows) * (LABEL_H + cell_h + GAP)
    img = Image.new("RGB", (width, height), bg)
    draw = ImageDraw.Draw(img)
    fg = theme.color("text", dark)
    y = PAD
    for title, shots in rows:
        draw.text((PAD, y), title, font=font, fill=fg)
        for index, (label, shot) in enumerate(shots):
            x = PAD + index * (cell_w + GAP)
            draw.text((x + 2, y + 3), label, font=font, fill=fg)
            img.paste(shot.convert("RGB"), (x, y + LABEL_H))
        y += LABEL_H + cell_h + GAP

    out = args.out or os.path.join(ROOT, "docs", f"anim_combo_{args.appearance}.png")
    img.save(out)
    print(f"\n胶片图已写出：{out}  ({width}×{height})")

    box._close(animate=False)
    try:
        for job in root.tk.call("after", "info") or ():
            root.tk.call("after", "cancel", job)
        root.destroy()
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
