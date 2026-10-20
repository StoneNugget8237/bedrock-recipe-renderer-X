"""把**真实窗口**截成 PNG —— 用来肉眼核对界面，而不是靠离线重画猜。

    python tools/screenshot_ui.py [输出路径] [--appearance light|dark] [--widgets]

为什么需要它：``ImageGrab.grab()`` 在这种会话里抓不到内容（实测返回的是一张
灰蒙蒙的、和屏幕对不上的图），所以之前只能靠 Pillow 离线重画 + 量常量来验证界面。
其实 Win32 的 ``PrintWindow(hwnd, hdc, PW_RENDERFULLCONTENT)`` 可以让窗口**自己**
把内容画进一块内存 DC —— 无边框窗口、Mica 背景都能画出来。这条路通了以后，
「改完看一眼」就不再是奢望。（抓图原语都在 ``mcrender.ui.win32``，这里只用它，
不要在这份工具里另写一份 —— 以前那份漏了 64 位句柄的 ``argtypes``，会偶发
``OverflowError``。）

``--widgets`` 会把第一个下拉展开一起截。**面板就在主窗口里面**（是字段下方的一块
子画布，见 ``FluentComboBox`` 的类文档），所以 ``win32.grab(root)`` 一次就带上了它 ——
不需要再单独抓一次弹窗、按屏幕坐标贴回来（那是面板还是独立 ``Toplevel`` 时才要做的事，
那份 ``punch`` / ``compose_menu`` 的抠色键代码也一并删掉了）。

解释器缺 tkinter 时会自动换一个（复用 ``smoke_ui`` 的查找逻辑）。
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from smoke_ui import ensure_gui_python          # noqa: E402  （顺带做解释器切换）

ensure_gui_python(os.path.abspath(__file__))

from mcrender import app                        # noqa: E402
from mcrender.ui import theme, widgets, win32   # noqa: E402
from mcrender.ui.window import MainWindow       # noqa: E402

grab = win32.grab                               # 兼容老调用（尖刀脚本会 import 它）


# ------------------------------------------------------------------ 场景
def shoot(out: str, appearance: str, open_menu: bool) -> int:
    theme.configure_appearance(appearance)
    cfg = dict(app.load_config() or {})
    win = MainWindow([], cfg=cfg)
    win.cfg["appearance"] = appearance
    theme.configure_appearance(appearance)
    root = win.root
    root.geometry("920x740+80+60")
    for _ in range(8):
        root.update()

    boxes = [w for w in _walk(root) if isinstance(w, widgets.FluentComboBox)]
    if open_menu and boxes:
        boxes[0]._open()
        for _ in range(8):
            root.update()
        # 展开是带动画的：截图要的是**稳定态**，不然抓到的是一块只露出中间一段的面板。
        # ``_settle()`` 把露出带铺满（不是改 place），update 一次让画布换上新底图。
        boxes[0]._settle()
        for _ in range(4):
            root.update()

    img = win32.grab(root)
    if img is None:
        print("! 抓不到窗口内容（这台机器没有可用的 Win32 抓图？）", file=sys.stderr)
        return 1

    if open_menu and boxes and boxes[0]._popup is not None:
        boxes[0]._close()

    img.save(out)
    print(f"截图已写出：{out}  ({img.size[0]}×{img.size[1]})\n"
          "（挑不出毛病的话，说明这一版界面看着是对的）")
    win.close()
    return 0


def _walk(widget):
    yield widget
    for child in widget.winfo_children():
        yield from _walk(child)


def main(argv) -> int:
    parser = argparse.ArgumentParser(description="给真实窗口截图")
    parser.add_argument("out", nargs="?", help="输出 PNG（默认写系统临时目录）")
    parser.add_argument("--appearance", choices=("light", "dark", "system"),
                        default="light", help="外观（默认浅色，看图更清楚）")
    parser.add_argument("--widgets", "--open-menu", dest="open_menu",
                        action="store_true", help="把第一个下拉展开一起截进去")
    args = parser.parse_args(argv[1:])
    out = args.out or os.path.join(tempfile.gettempdir(), "mcrender_ui_shot.png")
    return shoot(out, args.appearance, args.open_menu)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
