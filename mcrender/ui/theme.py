"""Fluent / WinUI 3 设计令牌与窗口外观。

只放「外观」相关的东西：色板、字体、圆角、间距，以及 Windows 11 的
Mica 背景与标题栏着色。所有对 pywinstyles 的调用都是尽力而为 —— 拿不到
效果就静默降级，绝不让界面起不来。
"""
from __future__ import annotations

import os
import sys

import customtkinter as ctk

APPEARANCE_MODES = ("system", "dark", "light")
APPEARANCE_LABELS = {"system": "跟随系统", "dark": "深色", "light": "浅色"}

# ------------------------------------------------------------------ 色板
# 每个令牌都是 (浅色, 深色)，交给 CustomTkinter 在切换外观时自动挑一个。
# 数值取自 WinUI 3 的 Fluent 深/浅色资源表。
COLORS = {
    "window":        ("#F3F3F3", "#202020"),   # 窗口底
    "layer":         ("#FFFFFF", "#2B2B2B"),   # 卡片
    "layer_stroke":  ("#E5E5E5", "#3D3D3D"),   # 卡片描边
    "card":          ("#FFFFFF", "#272727"),   # 嵌套卡片
    "control":       ("#FFFFFF", "#2D2D2D"),   # 输入框 / 次级按钮底
    "control_hover": ("#F5F5F5", "#333333"),
    "control_stroke": ("#D6D6D6", "#3F3F3F"),
    "accent_line":   ("#8A8A8A", "#6B6B6B"),   # 输入框底边
    "text":          ("#1A1A1A", "#FFFFFF"),
    "text_muted":    ("#5D5D5D", "#C8C8C8"),
    "text_faint":    ("#8A8A8A", "#9A9A9A"),
    # 语义色：失败 / 出错提示用（日志行着色那边用的是同一组值，见 LOG_COLORS）
    "error":         ("#C42B1C", "#FF99A4"),
    "accent":        ("#0067C0", "#60CDFF"),
    "on_accent":     ("#FFFFFF", "#003A5C"),
    "accent_hover":  ("#0A73CC", "#7AD6FF"),
    "drop_bg":       ("#F0F6FC", "#252A2E"),
    "drop_stroke":   ("#B7D3EA", "#3E4A54"),
    "log_bg":        ("#FFFFFF", "#181818"),
    "log_stroke":    ("#E5E5E5", "#333333"),
    # 自绘控件（WinUI 3 度量）：开关关闭态是「透明轨道 + 描边 + 深色圆点」
    "switch_stroke": ("#8A8A8A", "#9A9A9A"),
    "switch_knob":   ("#5C5C5C", "#D6D6D6"),
    # 下拉列表只有两档底色：普通选项**不铺底色**（直接露出面板底，读起来是一片
    # 干净的列表，而不是一格一格的方块），只有下面两种状态才出现色块：
    #   menu_hover    = 指针划过；
    #   menu_selected = 当前值。
    # 两档**故意取同一个色值**：加深程度一致，指针划过的深浅就是选中时的深浅，
    # 这样列表不会出现「越划越深」的两级台阶；「哪个是当前值」只由左侧蓝竖条说明。
    # 底色本身做得很浅（浅色模式下只比白底深一点点，像 WinUI 那样几乎是一层淡淡的底），
    # 免得看着像一块压在字后面的深灰「阴影」。
    # 两者都要比面板底（layer）深得出来，`tools/preview_ui_combo.py` 会断言色差。
    "menu_hover":    ("#F0F0F0", "#4C4C4C"),
    "menu_selected": ("#F0F0F0", "#4C4C4C"),
}

# 日志行着色（与 Fluent 语义色一致）
LOG_COLORS = {
    "ok":      ("#0F7B0F", "#6CCB5F"),
    "warn":    ("#9D5D00", "#E8A33D"),
    "error":   ("#C42B1C", "#FF99A4"),
    "muted":   ("#6E6E6E", "#9A9A9A"),
    "plain":   ("#1A1A1A", "#D6D6D6"),
}

# ------------------------------------------------------------------ 字体
FONT_FAMILY = "Microsoft YaHei UI"
FONT_MONO = "Consolas"

# 负值 = 像素高度，和设计稿里的 px 一一对应，不受系统 DPI 的点值换算影响
# menu 比 small 大一号：下拉列表是「读选项」的场景，字号小会很难认
FONT_SIZES = {"title": -20, "heading": -15, "body": -13, "small": -12,
              "menu": -14, "log": -12}


def color(name: str, dark: bool | None = None):
    """取一个设计令牌。默认返回 (浅色, 深色) 元组，``dark`` 给定时取单值。"""
    pair = COLORS[name]
    if dark is None:
        return pair
    return pair[1] if dark else pair[0]


def configure_appearance(mode: str) -> str:
    """把 CustomTkinter 的外观模式设成 system / dark / light，返回生效值。"""
    mode = mode if mode in APPEARANCE_MODES else "system"
    try:
        ctk.set_appearance_mode(mode)
    except Exception:
        mode = "system"
    return mode


def _wins():
    try:
        import ctypes
        return ctypes.windll
    except Exception:
        return None


def _is_win11() -> bool:
    if os.name != "nt":
        return False
    try:
        return sys.getwindowsversion().build >= 22000
    except Exception:
        return False


def apply_backdrop(window, dark: bool) -> str:
    """给窗口套上 Mica（Win11）/ 亚克力（Win10）背景，并给标题栏着色。

    返回实际生效的风格名，空串表示没套上（非 Windows、系统太旧、或
    pywinstyles 不可用）—— 这种情况下界面照常显示，只是少了半透明底。
    """
    if os.name != "nt":
        return ""
    try:
        import pywinstyles
    except Exception:
        return ""

    applied = ""
    for style in (("mica", "acrylic") if _is_win11() else ("acrylic",)):
        try:
            pywinstyles.apply_style(window, style)
            applied = style
            break
        except Exception:
            continue
    if not applied:
        return ""

    # 标题栏跟随深浅色，避免深色界面顶着一条白色标题栏
    header = COLORS["window"][1 if dark else 0]
    title = COLORS["text"][1 if dark else 0]
    for fn_name, kw in (("change_header_color", {"color": header}),
                        ("change_title_color", {"color": title}),
                        ("change_border_color", {"color": header})):
        fn = getattr(pywinstyles, fn_name, None)
        if fn is None:
            continue
        try:
            fn(window, **kw)
        except Exception:
            pass
    return applied


def icon_candidates() -> list:
    """窗口图标的位置，按优先级：exe 旁边 → 打包内 → 源码根目录。"""
    from ..app import ROOT, app_dir, bundle_dir

    roots = []
    for root in (app_dir(), bundle_dir(), ROOT):
        if root and root not in roots:
            roots.append(root)
    out = []
    for root in roots:
        for name in ("icon.ico", "icon.png"):
            p = os.path.join(root, "assets", name)
            if os.path.isfile(p) and p not in out:
                out.append(p)
    return out


def set_window_icon(window) -> None:
    """给窗口设置图标，逐个候选试，失败就算了。"""
    import tkinter as tk

    from . import win32

    for path in icon_candidates():
        try:
            if path.lower().endswith(".ico"):
                window.iconbitmap(default=path)
                # ⚠️ Tk 的 iconbitmap 只把 ico 的 **16×16** 档设成**窗口类**的小图标；
                # 本机 175% 缩放下标题栏图标要 28px，Windows 只能把 16 档拉大 —— 于是
                # 「logo 糊」。这里再按窗口 DPI 用 WM_SETICON 设一次**窗口**图标，
                # 窗口级优先于类级，Windows 就会去挑 32 档、不用放大。
                win32.apply_window_icon(window, path)
                return
            window.iconphoto(True, tk.PhotoImage(file=path))
            return
        except Exception:
            continue


class Fonts:
    """一套字体对象，窗口建好之后才能构造（CTkFont 需要 Tk 存在）。"""

    def __init__(self) -> None:
        self.title = ctk.CTkFont(family=FONT_FAMILY, size=FONT_SIZES["title"], weight="bold")
        self.heading = ctk.CTkFont(family=FONT_FAMILY, size=FONT_SIZES["heading"], weight="bold")
        self.body = ctk.CTkFont(family=FONT_FAMILY, size=FONT_SIZES["body"])
        self.body_bold = ctk.CTkFont(family=FONT_FAMILY, size=FONT_SIZES["body"], weight="bold")
        self.small = ctk.CTkFont(family=FONT_FAMILY, size=FONT_SIZES["small"])
        self.menu = ctk.CTkFont(family=FONT_FAMILY, size=FONT_SIZES["menu"])
        self.mono = ctk.CTkFont(family=FONT_MONO, size=FONT_SIZES["log"])
