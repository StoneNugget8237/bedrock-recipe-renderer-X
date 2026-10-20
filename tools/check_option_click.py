"""真机回归：点下拉选项**不能**顺带触发它盖住的那个按钮。

    python tools/check_option_click.py

为什么单独做一个工具，而不是塞进 ``smoke_ui``：这个 bug 只在**真实的鼠标事件流**里
才现形。原因有两层：

1. **一次点击是两个独立事件**，``<Button-1>`` 和 ``<ButtonRelease-1>`` 之间隔着
   「用户还按着」的那段时间。只要在按下时把面板 ``destroy()``（哪怕排到 ``after_idle``
   —— 事件队列一空它就跑了，物理松手还没到），松手那次的 release 就落到**面板底下那个
   控件**上；而 ``CTkButton`` 的 command 恰恰是在 release 里触发的。
2. **release 投给谁由 OS 按指针位置决定**，``event_generate`` 是"直接塞给某个控件"，
   绕开的正是要验的那段路由。

所以这里用 ``SetCursorPos`` + ``mouse_event`` 合成真鼠标：按下 → 跑事件循环 → 松开。
``smoke_ui`` 里另有一条确定性断言（按住期间面板必须一直活着）守着同一个不变量。

⚠️ 合成鼠标事件由 **OS 按指针位置**投递，所以测试窗口必须在**前台**。脚本是后台起的，
前台往往是浏览器 / 编辑器 —— 那时点击全落到别人窗口里，被测窗口「什么都没发生」，
「按钮没被顺带触发」这类断言于是**假通过**（比失败还糟）。所以开跑前先
``win32.force_foreground`` 抢前台，抢不到就**直接作废、不下结论**；抢到之后再用
「点字段能弹出面板」预检一次，确认合成事件真的进得来。

覆盖的场景（每个都要求"按钮 command 没被顺带触发"）：

* A 点一项，面板正压着按钮           —— 主场景
* B 点面板留白处
* C 按住一项 → 拖到按钮上 → 松手      —— 验 Tk 按下时的隐式抓取
* D 连点字段两下（开、关）            —— 别把自己过早收掉
* E 按住一项 → 拖到很远 → 松手

解释器：要带 ``tkinter`` 且装了 ``customtkinter``（同 ``smoke_ui``，缺了会自动换一个）。
"""
from __future__ import annotations

import ctypes
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tools.smoke_ui import ensure_gui_python          # noqa: E402

LEFTDOWN, LEFTUP = 0x0002, 0x0004
FAILED: list[str] = []


def check(ok: bool, name: str, detail: str = "") -> None:
    if not ok:
        FAILED.append(name)
    print(f"  {'✓' if ok else '✗'} {name}" + (f"  —— {detail}" if detail else ""))


def pump(root, ms: float = 120) -> None:
    """跑真实时间 + 事件循环（idle 队列会被清空）。"""
    end = time.time() + ms / 1000.0
    while time.time() < end:
        root.update()
        time.sleep(0.004)


def main() -> int:
    ensure_gui_python(__file__)
    if os.name != "nt":
        print("! 这个检查靠合成 Win32 鼠标事件，只在 Windows 上有意义。")
        return 0
    print(f"解释器：{sys.executable}")

    import tkinter as tk                              # noqa: E402

    import customtkinter as ctk                       # noqa: E402

    from mcrender.ui import theme, widgets, win32     # noqa: E402

    u32 = ctypes.windll.user32
    u32.SetCursorPos.argtypes = [ctypes.c_int, ctypes.c_int]
    # 抢前台的那套（AttachThreadInput / SetForegroundWindow）在 ``win32`` 里，
    # 签名已钉好 —— 这里别再自己声明一遍，两处不一致只会更难查。

    def move(x, y):
        u32.SetCursorPos(int(x), int(y))

    ctk.set_appearance_mode("dark")
    root = ctk.CTk()
    root.geometry("620x460+140+140")
    root.update()
    for _ in range(4):
        pump(root, 40)

    fonts = theme.Fonts()
    combo = widgets.FluentComboBox(root, variable=tk.StringVar(value="a1"),
                                   values=["a1", "a2", "a3", "a4"],
                                   width=150, height=34,
                                   font=fonts.small, menu_font=fonts.menu)
    combo.place(x=30, y=24)
    root.update()
    pump(root, 60)

    # 先开一次面板，量出「某个选项会落在屏幕的哪个点」，再据此摆按钮 ——
    # 不摆在这儿的话，鼠标根本压不到按钮上，测出来是假的"clean"。
    combo._open()
    pump(root, 80)
    canvas, geom, bounds = combo._popup, combo._geom, combo._bounds
    # ⚠️ ``bounds``/``geom`` 是**面板坐标**，而画布比面板四周大一圈（那圈留给投影）：
    # 屏幕坐标 = 画布原点 + 面板内偏移 + **投影留白**。漏掉后面这一项就会整体偏上去
    # 十来个像素，鼠标压到的是上一行（甚至字段上）。
    pad, top = combo._shadow_pad, combo._shadow_top
    row = 2
    row_cx = canvas.winfo_rootx() + pad + geom["menu_text_x"] + 10
    row_cy = canvas.winfo_rooty() + top + bounds[row][0] + geom["row_h"] // 2
    pad_y = canvas.winfo_rooty() + top + max(1, geom["pad_v"] // 2) - 2
    combo._close()
    pump(root, 60)

    hits = {"n": 0}

    def on_click():
        hits["n"] += 1

    button = widgets.FluentButton(root, text="danger", command=on_click,
                                  width=180, height=34, corner_radius=4,
                                  fg_color="#2D2D2D", bg_color="#202020",
                                  hover_color="#333333",
                                  border_width=1, border_color="#3F3F3F")
    # ⚠️ CTk 的 place() 自己按 DPI 缩放 → 这里要**逻辑**像素；
    # 而面板是原生 tk.Canvas、量出来的都是物理像素。两边不是一套。
    scale = combo._get_widget_scaling() or 1.0
    button.place(x=24, y=(row_cy - root.winfo_rooty()) / scale - 17)
    root.update()
    pump(root, 60)

    bl, br = button.winfo_rootx(), button.winfo_rootx() + button.winfo_width()
    bt, bb = button.winfo_rooty(), button.winfo_rooty() + button.winfo_height()
    over = bl <= row_cx <= br and bt <= row_cy <= bb
    print(f"\n[0] 装置：按钮 ({bl},{bt})..({br},{bb})，点的是第 {row} 项 ({row_cx},{row_cy})")
    check(over, "鼠标要压得到按钮上（压不到就是假通过）", f"over={over}")
    if not over:
        return 1

    def open_panel():
        combo._open()
        pump(root, 120)

    def press_at(x, y, hold=110):
        move(x, y)
        pump(root, 70)
        u32.mouse_event(LEFTDOWN, 0, 0, 0, 0)
        pump(root, hold)

    def release():
        u32.mouse_event(LEFTUP, 0, 0, 0, 0)
        pump(root, 220)

    def teardown():
        if combo._popup is not None:
            combo._close()
            pump(root, 80)

    # 字段的屏幕坐标（预检与 D 都用它）
    fx = combo.winfo_rootx() + 10
    fy = combo.winfo_rooty() + combo.winfo_height() // 2

    print("\n[0b] 前置：合成事件要真的送得进窗口")
    if not win32.force_foreground(root):
        print("  ! 抢不到前台窗口（前台仍是别的程序）—— 合成的点击会落到别人窗口里，")
        print("    这个检查的下半截只会**假通过**，所以不作结论。")
        print("    把终端 / 资源管理器留在前台，或者先在任务栏点一下本窗口，再跑一次。")
        try:
            root.destroy()
        except Exception:
            pass
        return 1
    pump(root, 120)
    check(win32.foreground() == win32.top_hwnd(root), "窗口拿到了前台焦点")
    press_at(fx, fy, hold=80)
    release()
    reached = combo._popup is not None
    check(reached, "合成点击能送到窗口（点字段弹出面板）",
          f"popup={'在' if reached else '没弹'}")
    if not reached:
        print("  ! 合成点击没进来 —— 后面的「没被顺带触发」都会假通过，检查作废。")
        try:
            root.destroy()
        except Exception:
            pass
        return 1
    teardown()

    print("\n[1] 点选项 / 留白 / 拖拽")
    # A —— 主场景：面板压着按钮，点其中一项
    open_panel()
    v0, h0 = combo.get(), hits["n"]
    press_at(row_cx, row_cy)
    alive = combo._popup is not None
    release()
    check(alive, "按住不放期间面板一直活着（A）",
          f"popup={'在' if alive else '已经没了'}")
    check(combo.get() != v0, "点选项立刻生效（A）", f"{v0} -> {combo.get()}")
    check(hits["n"] == h0, "没有顺带触发下面的按钮（A）", f"按钮 {h0} -> {hits['n']}")
    check(combo._popup is None, "松手之后面板收起（A）", f"popup={combo._popup}")

    # B —— 点面板留白处
    open_panel()
    h0 = hits["n"]
    press_at(row_cx, pad_y, hold=80)
    release()
    check(hits["n"] == h0, "没有顺带触发下面的按钮（B 留白）", f"按钮 {h0} -> {hits['n']}")
    check(combo._popup is None, "点留白也会收起（B）", f"popup={combo._popup}")

    # C —— 按住一项拖到按钮上再松手（验 Tk 按下时的隐式抓取）
    open_panel()
    h0 = hits["n"]
    move(row_cx, row_cy)
    pump(root, 60)
    u32.mouse_event(LEFTDOWN, 0, 0, 0, 0)
    pump(root, 80)
    move(row_cx, (bt + bb) // 2)
    pump(root, 80)
    release()
    check(hits["n"] == h0, "拖到按钮上松手也不触发它（C）", f"按钮 {h0} -> {hits['n']}")
    check(combo._popup is None, "拖出去松手后面板也会收起（C）", f"popup={combo._popup}")

    # D —— 连点字段两下：开、关（别把自己过早收掉）
    teardown()
    h0 = hits["n"]
    press_at(fx, fy, hold=80)
    release()
    opened = combo._popup is not None
    check(opened, "点字段能展开（D）", f"popup={'在' if opened else '已经没了'}")
    press_at(fx, fy, hold=80)
    release()
    check(combo._popup is None, "再点字段能收起（D）", f"popup={combo._popup}")
    check(hits["n"] == h0, "点字段不会顺带触发下面的按钮（D）",
          f"按钮 {h0} -> {hits['n']}")

    # E —— 按住一项拖到很远再松手
    open_panel()
    h0 = hits["n"]
    move(row_cx, row_cy)
    pump(root, 60)
    u32.mouse_event(LEFTDOWN, 0, 0, 0, 0)
    pump(root, 80)
    move(root.winfo_rootx() + root.winfo_width() - 20,
         root.winfo_rooty() + root.winfo_height() - 20)
    pump(root, 80)
    release()
    check(hits["n"] == h0, "拖得很远也不触发按钮（E）", f"按钮 {h0} -> {hits['n']}")
    check(combo._popup is None, "拖得很远松手后面板也会收起（E）", f"popup={combo._popup}")

    teardown()
    try:
        root.destroy()
    except Exception:
        pass

    print(f"\n{'=' * 52}")
    if FAILED:
        for name in FAILED:
            print(f"  ✗ {name}")
        return 1
    print("全部通过 ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
