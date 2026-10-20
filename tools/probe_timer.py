"""量 Tk 定时器的**真实**节奏 —— 给动画的帧间隔定标用。

    python tools/probe_timer.py

为什么要有它：`after(ms)` 只是「不早于 ms」的请求，实际落点由两层决定 ——

1. **系统计时器粒度**（Windows 默认 ~15.6ms）。不处理的话 8ms 的请求会落到 13ms 上下，
   80ms 的动画只画得出 5~7 帧、还忽快忽慢。所以 ``win32.timer_resolution(True)``
   在动画期间把精度提到 1ms。这个脚本会把「提 / 不提」两遍都跑出来给你看差别。
2. **每帧自己的开销**（Pillow 混图 + 换图 + Tk 重画面板，~2ms）。它跟系统粒度无关，
   而且**只有真动画里才有** —— 所以「请求 8ms，实际 8.2ms」这种账在空跑里成立、
   进了真动画就变成 10.2ms。真动画里的数字看 ``tools/smoke_ui.py`` 的 ``[1b]`` 一节。

**什么时候需要重跑**：换机器 / 换 Windows 版本 / 发现动画变卡 / 想改
``widgets.FRAME_MS``。拿这里的系统层数字和冒烟里的真动画数字一起看，再决定帧间隔。

纯测量，不碰项目里任何东西；只开一个空窗口泵定时器。缺 tkinter 的解释器会自动换一个
（复用 ``smoke_ui`` 的查找逻辑）。
"""
from __future__ import annotations

import os
import statistics
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from smoke_ui import ensure_gui_python                          # noqa: E402

# ⚠️ ``ensure_gui_python`` 必须在**任何**会拉 tkinter 的 import **之前**调用 ——
# 它靠 ImportError 判断当前解释器行不行，而 `import tkinter` 放前面会直接抛出去。
ensure_gui_python(os.path.abspath(__file__))

import tkinter as tk                                            # noqa: E402

from mcrender.ui import win32                                   # noqa: E402

INTERVALS = (4, 6, 8, 10, 16)
SPAN_MS = 120                   # 每档量 120ms，够看 80ms 动画里的帧数


def measure(interval: int, high: bool) -> tuple:
    """跑一串 ``after(interval)``，返回 (所有回调时刻 ms, 相邻间隔 ms)。"""
    root = tk.Tk()
    root.geometry("200x100+80+80")
    root.update()
    if high:
        win32.timer_resolution(True)
    stamps: list = []
    t0 = time.perf_counter()
    state = {"job": None, "guard": None}

    def tick():
        stamps.append((time.perf_counter() - t0) * 1000)
        if stamps[-1] >= SPAN_MS:
            root.quit()
            return
        state["job"] = root.after(interval, tick)

    state["job"] = root.after(0, tick)
    # 兜底：正常路径会自己 quit，这只是防「一个回调都到不了」时卡死。
    # ⚠️ 退出前必须把它 cancel 掉 —— 否则 destroy 之后它才会到期，
    # 刷一串 ``invalid command name "…quit"``（就是项目里「关窗安静」那条规矩）。
    state["guard"] = root.after(SPAN_MS + 500, root.quit)
    root.mainloop()
    for job in (state["job"], state["guard"]):
        if job is not None:
            try:
                root.after_cancel(job)
            except Exception:
                pass
    root.destroy()
    if high:
        win32.timer_resolution(False)
    gaps = [stamps[i] - stamps[i - 1] for i in range(1, len(stamps))]
    return stamps, gaps


def main() -> int:
    if os.name != "nt":
        print("这个探针只在 Windows 上有意义。")
        return 0
    print(f"系统计时器粒度探针（每档量 {SPAN_MS}ms）\n")
    for high in (False, True):
        title = "提过精度（win32.timer_resolution(True)）" if high else "默认（不提精度）"
        print(f"===== {title} =====")
        for interval in INTERVALS:
            stamps, gaps = measure(interval, high)
            if not gaps:
                print(f"  after({interval:2d})  : 没采到帧")
                continue
            in_span = len([s for s in stamps if s <= 80])
            print(f"  after({interval:2d})  : 中位间隔 {statistics.median(gaps):6.2f}ms"
                  f"   min {min(gaps):6.2f}  max {max(gaps):6.2f}"
                  f"   → 80ms 内实到 {in_span:2d} 帧"
                  f"   {[round(s) for s in stamps if s <= 80]}")
        print()
    print("读数提示：")
    print("  · 「中位间隔」远大于请求值 → 被系统粒度卡住了，动画期间必须提精度；")
    print("  · 提过精度后**空跑**基本就等于请求值（0.1ms 级），所以真动画里多出来的 ~2ms")
    print("    是**每帧自己的开销**（Pillow 混图 + 换图 + Tk 重画面板），不是定时器的锅；")
    print("  · 要定帧间隔，把这里的数字加上那 ~2ms 再看 —— 或者直接看")
    print("    `python tools/smoke_ui.py` 的 [1b] 一节，那里量的就是真动画。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
