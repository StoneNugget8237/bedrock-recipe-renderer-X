"""图形界面层（CustomTkinter / Fluent）。

导入本包会连带导入 ``customtkinter``,所以命令行路径不要碰它 ——
``mcrender_gui.py`` 只在真的要开窗口时才 import。
"""
from .window import MainWindow, run_gui

__all__ = ["MainWindow", "run_gui"]
