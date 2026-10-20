"""把开关「悬停时滑块长大」的过程抽帧拼成一张胶片图（真机截的像素）。

    python tools/preview_switch_hover.py [输出路径] [--appearance light|dark]

默认写 ``docs/anim_switch_hover.png``。

为什么要有它：这一条动画只有 100ms，肉眼看就是「一下就大了」——长得好不好看、
是不是**围绕自己的圆心**长大、关态那层悬停底色跟滑块是不是一起淡进来，只能靠
把帧摊开来看。``smoke_ui`` 只能给数字（差了几像素、画了几帧）。

做法照抄 ``preview_ui_anim.py``：真建窗口，按**固定进度**喂 ``_frame_hover``
（和真动画同一个函数），每帧抓一次窗口像素、按同一块矩形裁下来，两种状态各拼一行。
进度写死 → 每次出来的图可比对，能拿两张图对拍「这次改动到底改了什么」。

⚠️ 喂帧前 ``_Motion`` 必须是停的（否则它会在两次喂帧之间插进自己的帧）；
``_frame_hover`` 走的是 ``_render(live=True)``，所以即使没有动画在跑也会画出中间帧。

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

# 0 = 静止尺寸、1 = 悬停尺寸。中间几档按 ``fast_out_slow_in`` 的走向取，
# 一眼就能看出「前快后慢」（曲线本身在 shape.fast_out_slow_in）。
HOVER_STEPS = (0.0, 0.2, 0.4, 0.6, 0.8, 1.0)
GAP = 14                        # 帧与帧之间的缝
PAD = 12                        # 图四周的留白
LABEL_H = 26                    # 每行上方放标签的高度
MARGIN = 10                     # 裁图时在开关本体外再留一点


def main(argv) -> int:
    parser = argparse.ArgumentParser(description="开关悬停动画抽帧胶片图")
    parser.add_argument("out", nargs="?")
    parser.add_argument("--appearance", choices=("light", "dark"), default="light")
    args = parser.parse_args(argv[1:])

    import tkinter as tk

    import customtkinter as ctk

    from mcrender.ui import icons, theme, widgets, win32

    dark = args.appearance == "dark"
    ctk.set_appearance_mode("Dark" if dark else "Light")
    root = ctk.CTk()
    root.geometry("420x220+120+60")
    bg = theme.color("window", dark)
    ctk.CTkFrame(root, fg_color=bg, corner_radius=0).place(
        x=0, y=0, relwidth=1, relheight=1)
    root.update()

    fonts = theme.Fonts()
    cases = []
    for on in (False, True):
        switch = widgets.FluentSwitch(root, text="", variable=tk.BooleanVar(value=on),
                                      font=fonts.small, animate=False)
        switch.pack(anchor="w", padx=30, pady=12)
        cases.append((on, switch))
    root.update()

    hx, hy = root.winfo_rootx(), root.winfo_rooty()

    def region(switch):
        """每个开关各自的裁图矩形（两块控件是上下摞着的，别共用一块）。"""
        return (switch.winfo_rootx() - hx - MARGIN,
                switch.winfo_rooty() - hy - MARGIN,
                switch.winfo_rootx() - hx + switch.winfo_width() + MARGIN,
                switch.winfo_rooty() - hy + switch.winfo_height() + MARGIN)

    def knob_width(shot, on: bool) -> int:
        """量滑块在正中那一行的宽度。

        先圈出轨道（与窗口底色不同的那块），再在它里面数滑块色：开态滑块是白的、
        轨道是强调色；关态滑块是深灰、轨道几乎透明。不先圈轨道的话，窗口底色本身也
        「很白」，会把整条边都算成滑块。
        """
        rgb = shot.convert("RGB")
        px = rgb.load()
        w, h = rgb.size
        bg = rgb.getpixel((0, 0))

        def differs(c):
            return sum(abs(c[i] - bg[i]) for i in range(3)) > 24

        xs = [x for x in range(w) for y in range(h) if differs(px[x, y])]
        ys = [y for y in range(h) for x in range(w) if differs(px[x, y])]
        if not xs:
            return 0
        x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
        row = (y0 + y1) // 2
        if on:      # 白滑块压在强调色轨道上
            hit = lambda c: min(c) > 200                      # noqa: E731
        else:       # 深灰滑块压在几乎透明的轨道上
            hit = lambda c: max(c) < 140                      # noqa: E731
        return sum(1 for x in range(x0, x1 + 1) if hit(px[x, row]))

    print(f"解释器：{sys.executable}")
    print(f"外观 {args.appearance}，滑块 {icons.KNOB} → {icons.KNOB_HOVER} 逻辑像素\n")

    rows = []
    for on, switch in cases:
        switch._hover, switch._hover_from, switch._hover_to = True, 0.0, 1.0
        rect = region(switch)
        frames = []
        print(f"=== {'开' if on else '关'}态：悬停 → 滑块长大 ===")
        for p in HOVER_STEPS:
            switch._frame_hover(p)
            root.update()
            shot = win32.grab(root)
            if shot is None:
                print("  ! 抓不到窗口内容")
                return 1
            crop = shot.crop(rect)
            frames.append((f"{p:.2f}", crop))
            print(f"  p={p:<4} hover_s={switch._hover_s:.2f}"
                  f"  滑块宽 {knob_width(crop, on)}px")
        rows.append(("开态（蓝轨道）" if on else "关态（灰轨道）", frames))
        switch._hover = False
        switch._hover_s = 0.0

    # ---- 拼接：一行一种状态，帧号在每帧上方；行名单独占左侧一栏，别压在第一帧上
    font = load_font(15)
    cell_w = max(shot.size[0] for _t, shots in rows for _l, shot in shots)
    cell_h = max(shot.size[1] for _t, shots in rows for _l, shot in shots)
    cols = max(len(shots) for _t, shots in rows)
    title_w = max(int(ImageDraw.Draw(Image.new("RGB", (1, 1))).textlength(title, font=font))
                  for title, _s in rows) + 10
    width = PAD * 2 + title_w + cols * cell_w + (cols - 1) * GAP
    height = PAD * 2 + len(rows) * (LABEL_H + cell_h + GAP)
    img = Image.new("RGB", (width, height), bg)
    draw = ImageDraw.Draw(img)
    fg = theme.color("text", dark)
    y = PAD
    for title, shots in rows:
        draw.text((PAD, y + LABEL_H + (cell_h - 16) // 2), title, font=font, fill=fg)
        for index, (label, shot) in enumerate(shots):
            x = PAD + title_w + index * (cell_w + GAP)
            draw.text((x + 2, y + 3), label, font=font, fill=fg)
            img.paste(shot.convert("RGB"), (x + (cell_w - shot.size[0]) // 2,
                                            y + LABEL_H))
        y += LABEL_H + cell_h + GAP

    out = args.out or os.path.join(ROOT, "docs", "anim_switch_hover.png")
    img.save(out)
    print(f"\n胶片图已写出：{out}  ({width}×{height})")

    try:
        for job in root.tk.call("after", "info") or ():
            root.tk.call("after", "cancel", job)
        root.destroy()
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
