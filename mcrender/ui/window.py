"""Bedrock Recipe Renderer X 主窗口 —— Fluent / WinUI 3 风格，基于 CustomTkinter。

界面外观由 :mod:`mcrender.ui.theme` 提供，业务行为全部走 :mod:`mcrender.app`，
这里的职责只有「把控件搭起来 + 把事件接上去」。
"""
from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import traceback
from tkinter import filedialog, messagebox, ttk

import customtkinter as ctk

from .. import __version__
from .. import notify
from .. import update as updater
from ..app import (ARCHIVE_EXTS, Generator, app_dir, find_matching_rp,
                   load_config, save_config)
from . import dnd, icons, theme, widgets

DEFAULT_GEOMETRY = "920x740"
MIN_SIZE = (820, 620)

ICON_SIZE = 28                    # 标题区 logo 的逻辑边长（见 _build_app_icon）

TITLE = "Bedrock Recipe Renderer X"
# 版本号只有一个来源：``mcrender/__init__.py`` 的 ``__version__``（打包 / 标题栏 /
# 标题旁的色块都读它）。发版只改那一处。
VERSION = __version__
SUBTITLE = "基岩版配方 → 游戏内界面 PNG"
DROP_TEXT = "↓  将 [json / .mcaddon / .mcpack / zip / 文件夹] 拖入窗口  ↓"

PATH_ROWS = (
    ("bp", "行为包 BP"),
    ("rp", "资源包 RP"),
    ("out", "输出目录"),
    ("vanilla", "原版包"),
)


def classify(msg: str) -> str:
    """日志行 → 着色标签。"""
    s = str(msg)
    if s.startswith("✗") or "出错了" in s:
        return "error"
    if s.startswith("✓") or "完成：" in s or "已切换为纯本地模式" in s:
        return "ok"
    if s.lstrip().startswith("!") or "失败" in s or "无效" in s:
        return "warn"
    if s.startswith("─") or s.startswith("·") or s.startswith("  ↳") or "跳过" in s:
        return "muted"
    return "plain"


class MainWindow:
    def __init__(self, paths, cfg: dict | None = None):
        self.cfg = cfg or load_config()
        self.state = {"busy": False}
        self._log_queue: queue.Queue = queue.Queue()
        self._last_mode = ""
        self._closing = False
        self._log_job = None        # 日志轮询的 after 任务 id，关窗时要取消

        theme.configure_appearance(self.cfg.get("appearance", "system"))

        self.root = ctk.CTk()
        # 标题栏带版本号（``Bedrock Recipe Renderer X v1.1.0``）：任务栏 / Alt+Tab 里
        # 也能一眼看出跑的是哪一版。
        self.root.title(f"{TITLE} v{VERSION}")
        self.root.geometry(DEFAULT_GEOMETRY)
        self.root.minsize(*MIN_SIZE)
        self.root.configure(fg_color=theme.color("window"))
        self.fonts = theme.Fonts()

        self.entries: dict = {}
        self._build()
        theme.set_window_icon(self.root)

        self._last_mode = ctk.get_appearance_mode()
        self._style_log()
        self._apply_backdrop()

        self._enable_drop()
        self._center()

        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self._log_job = self.root.after(60, self._drain_log)
        if paths:
            self.root.after(200, lambda: self.on_thread(paths))
        self.write(f"输出目录：{self.cfg.get('out')}")
        if self.entries["vanilla"].get().strip():
            self.check_vanilla_pack()

    # ------------------------------------------------------------- 组装
    def _build(self):
        c = theme.color
        f = self.fonts
        body = ctk.CTkFrame(self.root, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=16, pady=14)

        # ---- 标题区
        header = ctk.CTkFrame(body, fg_color="transparent")
        header.pack(fill="x")
        self._build_app_icon(header)
        titles = ctk.CTkFrame(header, fg_color="transparent")
        titles.pack(side="left", padx=(10, 0))
        # 标题与版本色块同一行（WinUI 的 InfoBadge 就挂在标题右边），副标题另起一行
        title_row = ctk.CTkFrame(titles, fg_color="transparent")
        title_row.pack(anchor="w")
        self.title_label = ctk.CTkLabel(title_row, text=TITLE, font=f.title,
                                        text_color=c("text"), anchor="w")
        self.title_label.pack(side="left")
        self.version_badge = widgets.FluentBadge(title_row, text=f"v{VERSION}",
                                                 font=f.small, fill="accent",
                                                 text_color="on_accent")
        # 往上提一点：色块跟标题的**视觉中线**对齐，别贴着副标题那一侧
        self.version_badge.pack(side="left", padx=(8, 0), pady=(3, 0))
        ctk.CTkLabel(titles, text=SUBTITLE, font=f.small, text_color=c("text_faint"),
                     anchor="w").pack(anchor="w")
        self.backdrop_name = ""
        self._appearance_images: dict = {}
        self.appearance_button = widgets.FluentButton(
            header, text="", width=106, height=30, font=f.small, corner_radius=5,
            fg_color=c("control"), hover_color=c("control_hover"), text_color=c("text"),
            border_width=1, border_color=c("control_stroke"), compound="left",
            command=self.cycle_appearance)
        self.appearance_button.pack(side="right")
        self._sync_appearance_button()
        # 检查更新：挂在深浅色按钮左边（pack 是「先摆的贴右边」，所以后摆的在它左侧）
        self._update_busy = False
        self._update_result = None      # 工作线程写、主线程读（只放一个元组引用）
        self._update_job = None         # 结果轮询的 after id
        self.update_button = widgets.FluentButton(
            header, text="检查更新", width=88, height=30, font=f.small, corner_radius=5,
            fg_color=c("control"), hover_color=c("control_hover"), text_color=c("text"),
            border_width=1, border_color=c("control_stroke"),
            command=self.check_updates)
        self.update_button.pack(side="right", padx=(0, 8))
        # 结果的一行小字（挂在按钮左边）。系统通知发出去之后**这里也会写明结论** ——
        # 有的机器（免打扰 / 通知横幅被关）通知只进通知中心、不弹横幅，光靠 toast
        # 会让人以为「点了没反应」（用户报的）。
        self.update_hint = ctk.CTkLabel(header, text="", font=f.small,
                                        width=self.HINT_MAX_W,
                                        text_color=c("text_muted"), anchor="e")
        self.update_hint.pack(side="right", padx=(0, 12))
        # 系统通知要先在开始菜单挂一个带 AUMID 的快捷方式，否则 toast 只进通知中心、
        # **不弹横幅**。建它要起一次 PowerShell（Add-Type 编一小段 C#，~1s），
        # 扔后台先做着，别卡窗口首帧；之后每次「检查更新」都是现成的、零开销。
        threading.Thread(target=notify.ensure_registered, daemon=True).start()

        # ---- 拖放区
        drop = ctk.CTkFrame(body, fg_color=c("drop_bg"), corner_radius=8,
                            border_width=2, border_color=c("drop_stroke"))
        drop.pack(fill="x", pady=(14, 10))
        self.drop_label = ctk.CTkLabel(drop, text=DROP_TEXT, font=f.body,
                                       text_color=c("accent"), height=64)
        self.drop_label.pack(fill="x", padx=16, pady=6)
        self.drop_hint = ctk.CTkLabel(drop, text="拖入即出图；手动选包时点下方主按钮",
                                      font=f.small, text_color=c("text_faint"))
        self.drop_hint.pack(pady=(0, 10))

        # ---- 路径卡片
        paths = ctk.CTkFrame(body, fg_color=c("layer"), corner_radius=8,
                             border_width=1, border_color=c("layer_stroke"))
        paths.pack(fill="x")
        inner = ctk.CTkFrame(paths, fg_color="transparent")
        inner.pack(fill="x", padx=14, pady=12)

        for key, label in PATH_ROWS:
            self._add_path_row(inner, key, label)

        self.out_hint = ctk.CTkLabel(inner, text="", font=f.small,
                                     text_color=c("text_faint"), anchor="w")
        self.out_hint.pack(fill="x", padx=(70, 0), pady=(6, 0))
        self.entries["out"].trace_add("write", self.refresh_hint)
        self.refresh_hint()

        # ---- 选项条
        opts = ctk.CTkFrame(body, fg_color="transparent")
        opts.pack(fill="x", pady=(12, 0))

        self.layout_var = ctk.StringVar(value=self.cfg.get("layout", "modern"))
        self.scale_var = ctk.StringVar(value=str(self.cfg.get("scale", 4)))
        self.shade_var = ctk.StringVar(value=self.cfg.get("cube_shading", "vanilla"))
        self.wiki_var = ctk.StringVar(value=self.cfg.get("wiki_icons", "auto"))
        self.local_var = ctk.BooleanVar(value=bool(self.cfg.get("local_only")))

        # 四个字段的宽度一律 ≥ ``FluentComboBox.MIN_W``（120）：面板宽度就是字段宽度，
        # 字段窄于这个数时面板会被撑宽、右沿对不齐（「缩放」原来是 76，面板比字段宽
        # 一大截）。这里直接写成 120，别再把宽度拍得比它小。
        self._option(opts, "版式", self.layout_var, ("modern", "classic"), 120)
        self._option(opts, "缩放", self.scale_var, ("2", "3", "4", "6", "8"), 120)
        self._option(opts, "方块阴影", self.shade_var, ("vanilla", "reference"), 124)
        self._option(opts, "wiki 图标", self.wiki_var, ("auto", "always", "never"), 120)

        self.local_switch = widgets.FluentSwitch(opts, text="只用本地（不联网）",
                                                 variable=self.local_var, font=f.small,
                                                 text_color=c("text_muted"),
                                                 command=lambda: self.apply_cfg(save=True))
        self.local_switch.pack(side="right", padx=(0, 4))

        # ---- 按钮区（两行，避免 920px 下主按钮 + 5 个次级按钮挤在一行被裁掉）
        actions = ctk.CTkFrame(body, fg_color="transparent")
        actions.pack(fill="x", pady=(12, 0))

        self.primary_button = widgets.FluentButton(
            actions, text="▶ 开始出图（当前 BP + RP 的全部配方）", command=self.start_all,
            font=f.body_bold, height=34, corner_radius=5, fg_color=c("accent"),
            hover_color=c("accent_hover"), text_color=c("on_accent"))
        self.primary_button.pack(side="left")

        self._secondary(actions, "打开输出文件夹", self.open_out).pack(side="left", padx=(7, 0))
        self._secondary(actions, "清空日志", self.clear_log).pack(side="right")

        tools_row = ctk.CTkFrame(body, fg_color="transparent")
        tools_row.pack(fill="x", pady=(8, 0))
        for text, cmd in (("选择配方文件…", self.pick_files),
                          ("选择配方文件夹…", self.pick_recipe_folder),
                          ("补全原版资源（下载全部纹理）", self.sync_vanilla),
                          ("删除本地纹理缓存", self.clear_texture_cache)):
            self._secondary(tools_row, text, cmd).pack(side="left", padx=(0, 7))

        # ---- 日志
        log_card = ctk.CTkFrame(body, fg_color=c("log_bg"), corner_radius=8,
                                border_width=1, border_color=c("log_stroke"))
        log_card.pack(fill="both", expand=True, pady=(12, 0))

        self.log_text = ctk.CTkTextbox(log_card, corner_radius=0, border_width=0,
                                       fg_color="transparent", font=f.mono, wrap="word",
                                       activate_scrollbars=False)
        self.scrollbar = ctk.CTkScrollbar(log_card, command=self.log_text.yview,
                                          button_color=c("control_stroke"),
                                          button_hover_color=c("control_hover"))
        self.log_text.configure(yscrollcommand=self.scrollbar.set)
        self.scrollbar.pack(side="right", fill="y", padx=(0, 6), pady=6)
        self.log_text.pack(side="left", fill="both", expand=True, padx=(8, 0), pady=6)
        # CTkTextbox 内部就是一个 tk.Text，取出来配 tag 颜色
        self._text_widget = getattr(self.log_text, "_textbox", None)
        if self._text_widget is not None:
            self._text_widget.configure(insertwidth=0)

    def _build_app_icon(self, parent):
        try:
            from PIL import Image

            for path in theme.icon_candidates():
                if not path.lower().endswith(".png"):
                    continue
                # CTkImage 只会做一遍 ``image.resize(size)``，**不指定重采样** ——
                # Pillow 对 RGBA 默认 BICUBIC，比 LANCZOS 软一截（白缝最亮只有 226/255）。
                # 这里自己按**物理**像素先 LANCZOS 缩好，再把 CTkImage 的目标尺寸给成
                # 「逻辑值」让它算出同一个物理值，那一遍就退化成恒等缩放。
                scale = ctk.ScalingTracker.get_widget_scaling(parent)
                px = max(1, round(ICON_SIZE * scale))
                base = Image.open(path).convert("RGBA").resize(
                    (px, px), Image.Resampling.LANCZOS)
                img = ctk.CTkImage(light_image=base, dark_image=base,
                                   size=(ICON_SIZE, ICON_SIZE))
                self._icon_image = img
                ctk.CTkLabel(parent, text="", image=img,
                             width=ICON_SIZE).pack(side="left")
                return
        except Exception:
            pass
        block = ctk.CTkFrame(parent, width=ICON_SIZE, height=ICON_SIZE, corner_radius=4,
                             fg_color=theme.color("accent"))
        block.pack(side="left")
        block.pack_propagate(False)

    def _add_path_row(self, parent, key, label):
        c = theme.color
        f = self.fonts
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", pady=(0, 7))
        ctk.CTkLabel(row, text=label, width=64, anchor="w", font=f.small,
                     text_color=c("text_muted")).pack(side="left")
        var = ctk.StringVar(value=self.cfg.get(key, ""))
        self.entries[key] = var
        widgets.FluentEntry(row, textvariable=var, height=30, corner_radius=4, font=f.small,
                            fg_color=c("control"), border_color=c("control_stroke"),
                            text_color=c("text")).pack(side="left", fill="x", expand=True,
                                                       padx=(6, 6))
        widgets.FluentButton(row, text="选择…", width=64, height=30, corner_radius=4,
                             font=f.small, fg_color=c("control"),
                             hover_color=c("control_hover"),
                             text_color=c("text"), border_width=1,
                             border_color=c("control_stroke"),
                             command=lambda: self.picker_for(key)()).pack(side="left")

    def _option(self, parent, label, var, values, width):
        c = theme.color
        f = self.fonts
        holder = ctk.CTkFrame(parent, fg_color="transparent")
        holder.pack(side="left", padx=(0, 14))
        ctk.CTkLabel(holder, text=label, font=f.small,
                     text_color=c("text_muted")).pack(side="left", padx=(0, 6))
        menu = widgets.FluentComboBox(holder, variable=var, values=list(values),
                                      width=width, height=30, font=f.small,
                                      menu_font=f.menu,
                                      command=lambda _v: self.apply_cfg(save=True))
        menu.pack(side="left")

    def _secondary(self, parent, text, command):
        c = theme.color
        return widgets.FluentButton(parent, text=text, command=command, height=34,
                                    corner_radius=5, font=self.fonts.small,
                                    fg_color=c("control"), hover_color=c("control_hover"),
                                    text_color=c("text"), border_width=1,
                                    border_color=c("control_stroke"))

    # --------------------------------------------------------- 外观与窗口
    def _apply_backdrop(self):
        dark = ctk.get_appearance_mode() == "Dark"
        self.backdrop_name = theme.apply_backdrop(self.root, dark)

    def _appearance_icon(self, mode):
        """外观图标（跟随系统 = 显示器 / 浅色 = 太阳 / 深色 = 月亮）。

        深浅两版一起交给 ``CTkImage``，跟随系统外观切换由 CustomTkinter 自己挑。
        """
        if mode not in self._appearance_images:
            self._appearance_images[mode] = ctk.CTkImage(
                *icons.appearance_pair(mode), size=(icons.ICON, icons.ICON))
        return self._appearance_images[mode]

    def _sync_appearance_button(self):
        mode = self.cfg.get("appearance", "system")
        self.appearance_button.configure(
            text=theme.APPEARANCE_LABELS.get(mode, "跟随系统"),
            image=self._appearance_icon(mode))

    def cycle_appearance(self):
        modes = theme.APPEARANCE_MODES
        cur = self.cfg.get("appearance", "system")
        nxt = modes[(modes.index(cur) + 1) % len(modes)] if cur in modes else "system"
        self.cfg["appearance"] = nxt
        theme.configure_appearance(nxt)
        self._sync_appearance_button()
        self.apply_cfg(save=True)
        self._apply_backdrop()
        self._style_log()
        self.write(f"界面外观：{theme.APPEARANCE_LABELS[nxt]}")

    # --------------------------------------------------------- 检查更新
    def check_updates(self):
        """点「检查更新」：后台拉 GitHub 的 releases/latest，比完弹系统通知。

        网络请求放工作线程（6s 超时也够卡一下界面了），按钮先变成「检查中…」并禁用。

        ⚠️ 结果**不**用 ``root.after`` 从工作线程抛回来 —— Tk 不是线程安全的，那样写
        偶尔会「按钮一直停在检查中、什么也没弹」（实测就这样）。工作线程只往
        ``self._update_result`` 放一个元组，主线程每 60ms 取一次。
        """
        if self._update_busy:
            return
        self._update_busy = True
        self._update_result = None
        self._hint("正在检查更新…")
        try:
            self.update_button.configure(text="检查中…", state="disabled")
        except Exception:
            pass
        threading.Thread(target=self._check_updates_bg, daemon=True).start()
        self._update_job = self.root.after(60, self._poll_update)

    def _check_updates_bg(self):
        """工作线程：只做「网络 + 比较」，**不碰任何 Tk 控件**。"""
        try:
            outcome = ("ok", updater.check(VERSION))
        except Exception as exc:            # UpdateError / 意外异常都算失败
            outcome = ("error", str(exc) or type(exc).__name__)
        self._update_result = outcome

    def _poll_update(self):
        """主线程：等工作线程把结果放好（``None`` = 还没好，继续等）。"""
        self._update_job = None
        if not self._update_busy:
            return
        if self._update_result is None:
            if not self._closing:
                self._update_job = self.root.after(60, self._poll_update)
            return
        kind, payload = self._update_result
        self._update_result = None
        self._updates_ready(kind, payload)

    def _updates_ready(self, kind, payload):
        """回到主线程：还原按钮，按比较结果弹通知。"""
        self._update_busy = False
        try:
            self.update_button.configure(text="检查更新", state="normal")
        except Exception:
            pass
        if kind == "error":
            self.write(f"检查更新失败：{payload}")
            self._hint(f"检查失败：{payload}", "error")
            self._notify("检查更新失败", str(payload), tag="check-fail")
            return
        tag = payload["tag"]
        if payload["newer"]:
            self.write(f"发现新版本：{tag}（当前 v{VERSION}）")
            self._hint(f"发现新版本 {tag}", "accent")
            self._notify("新版本可用", f"最新发布版：{tag}",
                         button="前往下载", url=updater.RELEASE_URL,
                         tag="new-version")
        else:
            self.write(f"已是最新版本（{tag}）")
            self._hint("已是最新版本", "text_muted")
            self._notify("检查更新完成", "已是最新版本", tag="check-done")

    # 标题区小字那一格的宽度（**逻辑**像素）：给死，不跟着文字涨缩 —— 空间不够时
    # pack 会把标签挤窄、``anchor="e"`` 就把**左边**那截推到徽标底下（看着像从半个字
    # 开始）。给死之后框永远是这么宽，超过的部分由 ``_elide`` 变省略号。
    HINT_MAX_W = 200

    def _hint(self, text, token="text_muted"):
        """标题区那行结果小字（跟着深浅色走：``theme.color`` 不给 dark 就是一对颜色）。

        太长会被**省略号截掉**（``HINT_MAX_W``）：它夹在标题和按钮中间，pack 空间不够时
        标签会被裁掉左边一半、看着像「从半截开始」（820 宽 + 限流那种长原因就会）。完整
        原因在日志区和通知里都有。
        """
        text = self._elide(str(text))
        try:
            color = theme.color(token)
        except KeyError:                    # 令牌写错别把整行吞成「什么都没发生」
            color = theme.color("text_muted")
        try:
            self.update_hint.configure(text=text, text_color=color)
        except Exception:
            pass

    def _elide(self, text):
        """按实际字体宽度截到 ``HINT_MAX_W`` 以内（CJK 和 ASCII 宽度差太多，不能数字符）。

        ⚠️ ``CTkFont.measure()`` 给的是**逻辑**像素（同一串文字实测 72，标签画出来 126
        物理 = 72×1.75），别再乘一遍缩放 —— 乘了等于没截，标签比可用空间宽，``anchor="e"``
        会把**左边**那截推到徽标底下（看着像从半个字开始）。
        """
        try:
            font = self.fonts.small
            limit = self.HINT_MAX_W
            if font.measure(text) <= limit:
                return text
            while text and font.measure(text + "…") > limit:
                text = text[:-1]
            return text.rstrip() + "…"
        except Exception:
            return text

    def _notify(self, title, body, button=None, url=None, tag=None):
        """先弹系统通知；发不出去就退回应用内对话框（别让按钮点了没反应）。

        ``tag`` 是必须的：不带 tag 的同内容通知会被 Windows 去重（只进通知中心、
        不弹横幅），连点两次「检查更新」时第二次就没反应了（用户报过）。
        """
        if notify.toast(title, body, button=button, url=url, tag=tag):
            return
        if url:
            messagebox.showinfo(title, f"{body}\n\n下载地址：{url}")
        else:
            messagebox.showinfo(title, body)

    def _style_log(self):
        if self._text_widget is None:
            return
        dark = ctk.get_appearance_mode() == "Dark"
        self._text_widget.configure(bg=theme.color("log_bg", dark),
                                    fg=theme.color("text", dark))
        for tag, pair in theme.LOG_COLORS.items():
            self._text_widget.tag_config(tag, foreground=pair[1 if dark else 0])
        self._last_mode = ctk.get_appearance_mode()

    def _center(self):
        try:
            self.root.update_idletasks()
            w, h = 920, 740
            sw = self.root.winfo_screenwidth()
            sh = self.root.winfo_screenheight()
            self.root.geometry(f"{w}x{h}+{max(0, (sw - w) // 2)}+{max(0, (sh - h) // 3)}")
        except Exception:
            pass

    def _enable_drop(self):
        def on_drop(picked):
            self.on_thread(picked)

        if dnd.enable_drop(self.root, on_drop):
            self.drop_hint.configure(text="拖放已就绪 —— 松手即出图", text_color=theme.color("accent"))
        else:
            self.write("（窗口拖放不可用，请用下面的按钮：当前系统或远端会话不支持拖放）")

    # ------------------------------------------------------------- 日志
    def write(self, msg):
        """任何线程都能安全调用：把日志塞进队列，主线程负责渲染。"""
        self._log_queue.put(str(msg))

    def _drain_log(self):
        if self._closing:
            return
        dirty = False
        while True:
            try:
                msg = self._log_queue.get_nowait()
            except queue.Empty:
                break
            if self._text_widget is not None:
                self._text_widget.insert("end", msg + "\n", classify(msg))
            dirty = True
        if dirty:
            if self._text_widget is not None:
                self._text_widget.see("end")
        # 跟随系统外观切换时，CustomTkinter 会自己重绘，但 tk.Text 要手动同步
        if ctk.get_appearance_mode() != self._last_mode:
            self._style_log()
        self._log_job = self.root.after(60, self._drain_log)

    def clear_log(self):
        if self._text_widget is not None:
            self._text_widget.delete("1.0", "end")

    # ------------------------------------------------------------- 配置
    def apply_cfg(self, save=False):
        self.cfg.update(bp=self.entries["bp"].get(), rp=self.entries["rp"].get(),
                        out=self.entries["out"].get(), vanilla=self.entries["vanilla"].get(),
                        layout=self.layout_var.get(), scale=int(self.scale_var.get() or 4),
                        cube_shading=self.shade_var.get(), wiki_icons=self.wiki_var.get(),
                        local_only=bool(self.local_var.get()))
        if save:
            save_config(self.cfg)
        return self.cfg

    def refresh_hint(self, *_):
        base = self.entries["out"].get()
        sub = (self.cfg.get("out_subdir") or "").strip()
        if sub and os.path.basename(os.path.normpath(base or ".")) != sub:
            self.out_hint.configure(text=f"图片会放在：{os.path.join(base, sub)}")
        else:
            self.out_hint.configure(text=f"图片会放在：{base}")

    # ------------------------------------------------------------- 选择路径
    def picker_for(self, key):
        return {"bp": self.pick_bp, "rp": self.pick_rp, "out": self.pick_out,
                "vanilla": self.pick_vanilla}[key]

    def pick_dir(self, title, var, after=None):
        p = filedialog.askdirectory(title=title, initialdir=var.get() or os.getcwd())
        if p:
            var.set(os.path.normpath(p))
            self.apply_cfg(save=True)
            if after:
                after(p)

    def ready_hint(self):
        bp, rp = self.entries["bp"].get(), self.entries["rp"].get()
        if bp and rp:
            self.write("BP / RP 都已就绪 → 点「▶ 开始出图（当前 BP + RP 的全部配方）」")
        elif bp:
            self.write("已设置 BP。请再点 RP 那一行的「选择…」选资源包（否则自定义纹理会是紫黑方块）")
        elif rp:
            self.write("已设置 RP。请再点 BP 那一行的「选择…」选行为包")

    def pick_bp(self):
        def after(p):
            paired = find_matching_rp(p)
            if paired:
                self.entries["rp"].set(paired)
                self.apply_cfg(save=True)
                self.write(f"自动找到配套资源包：{paired}")
            else:
                self.write("没找到配套资源包，请手动选 RP")
            self.ready_hint()
        self.pick_dir("选择行为包 BP 文件夹（里面有 manifest.json 和 recipes）",
                      self.entries["bp"], after)

    def pick_rp(self):
        def after(p):
            self.write(f"资源包已设为：{p}")
            self.ready_hint()
        self.pick_dir("选择资源包 RP 文件夹（textures / models 所在）", self.entries["rp"], after)

    def pick_out(self):
        self.pick_dir("选择输出目录", self.entries["out"])

    def pick_vanilla(self):
        """原版包可以是文件夹，也可以是 .zip / .mcpack / .mcaddon。"""
        path = filedialog.askopenfilename(
            title="选择原版包：文件夹里的资源包，或资源包压缩文件",
            filetypes=[("资源包压缩文件", "*.zip *.mcpack *.mcaddon"), ("所有文件", "*.*")])
        if not path:
            path = filedialog.askdirectory(title="或者选择原版资源包文件夹")
        if not path:
            return
        self.entries["vanilla"].set(os.path.normpath(path))
        self.check_vanilla_pack()

    def check_vanilla_pack(self):
        """有有效原版包（文件夹或压缩文件）时直接进本地模式，不等下载。"""
        value = self.entries["vanilla"].get().strip()
        if not value:
            return
        from ..vanilla import VanillaSource
        source = VanillaSource(local_dirs=[value] if os.path.isdir(value) else [],
                               zip_files=[value] if os.path.isfile(value) else [],
                               cache_dir=os.path.join(app_dir(), "vanilla_cache"),
                               allow_download=False)
        if not source.has_pack():
            self.write(f"原版包无效（找不到 textures/item_texture.json）：{value}")
            return
        kind = "文件夹" if os.path.isdir(value) else "压缩文件"
        self.write(f"原版包（{kind}）可用，已切到本地模式，不再联网。")
        self.local_var.set(True)
        self.apply_cfg(save=True)

    # ------------------------------------------------------------- 出图
    def on_thread(self, paths_):
        threading.Thread(target=self.process, args=(paths_,), daemon=True).start()

    def process(self, paths_):
        if self.state["busy"]:
            self.write("还在处理上一条，稍等…")
            return
        paths_ = [p for p in paths_ if p]
        if not paths_:
            return
        self.state["busy"] = True
        self.root.after(0, lambda: self.primary_button.configure(state="disabled"))
        self.write("─" * 62)
        for p in paths_:
            self.write("收到：" + str(p))
        gen = Generator(self.apply_cfg(save=True), log=self.write)
        try:
            gen.run(paths_)
        except Exception:
            self.write("出错了：\n" + traceback.format_exc())
        finally:
            gen.cleanup()
            self.state["busy"] = False
            self.root.after(0, lambda: self.primary_button.configure(state="normal"))

    def start_all(self):
        """只有 BP + RP 都选好了才出图。"""
        bp, rp = self.entries["bp"].get(), self.entries["rp"].get()
        if not bp:
            self.write("还没选 BP 文件夹")
            return
        if not rp:
            paired = find_matching_rp(bp)
            if paired:
                self.entries["rp"].set(paired)
                self.apply_cfg(save=True)
                self.write(f"自动找到配套资源包：{paired}")
                rp = paired
            else:
                self.write("还没选 RP 文件夹 —— 请先选资源包，否则自定义纹理会是紫黑方块")
                return
        self.on_thread([bp])

    def pick_files(self):
        fs = filedialog.askopenfilenames(
            title="选择 recipe json 或压缩包",
            filetypes=[("配方/压缩包", "*.json *.mcaddon *.mcpack *.zip"), ("所有文件", "*.*")])
        if fs:
            self.on_thread(list(fs))

    def pick_recipe_folder(self):
        d = filedialog.askdirectory(title="选择含 recipes 的文件夹（普通文件夹也行）")
        if d:
            self.on_thread([d])

    def gen_out_dir(self):
        base = (self.apply_cfg(save=True)["out"] or "").strip()
        if not base:
            self.write("还没选输出目录")
            return None
        sub = (self.cfg.get("out_subdir") or "").strip()
        if sub and os.path.basename(os.path.normpath(base)) != sub:
            return os.path.join(base, sub)
        return base

    def open_out(self):
        d = self.gen_out_dir()
        if not d:
            return
        os.makedirs(d, exist_ok=True)
        try:
            os.startfile(d)                                    # noqa: S606
        except Exception:
            subprocess.Popen(["explorer", d])

    def sync_vanilla(self):
        def job():
            from ..vanilla import VanillaSource
            cfg_now = self.apply_cfg(save=True)
            src = VanillaSource(
                local_dirs=[cfg_now.get("vanilla")] if cfg_now.get("vanilla") else [],
                cache_dir=os.path.join(app_dir(), "vanilla_cache"), allow_download=True,
                log=self.write)
            self.write("开始补全原版资源：把原版全部物品/方块纹理下载到本地（只增不删）…")
            try:
                report = src.sync_all(log=self.write)
                self.write(f"清单 {report.get('listed', 0)} 个纹理；新下载 {report.get('fetched', 0)} 个，"
                           f"失败 {report.get('failed', 0)} 个；缓存共 {src.cache_size_mb()} MB")
                if report.get("failed"):
                    for rel in report.get("failed_list", [])[:10]:
                        self.write("  ✗ 下载失败：" + rel)
                # 索引只列出一部分贴图（石头工具、骨粉这类是直接以文件名引用的），
                # 所以再按配方走一遍，把它们也拉下来，之后本地模式才不会缺件。
                if cfg_now.get("bp") and os.path.isdir(cfg_now["bp"]):
                    self.write("按配方预取贴图（索引里没有的文件也在这一步补齐）")
                    gen = Generator(dict(cfg_now, local_only=False), log=self.write)
                    info = gen.prefetch(cfg_now["bp"])
                    gen.cleanup()
                    self.write(f"配方 {info['recipes']} 条 / 物品 {info['items']} 个，"
                               f"新下载 {info['downloaded']} 个，仍缺 {len(info['missing'])} 个")
                    for ident in info["missing"][:10]:
                        self.write("  缺：" + ident)
                    if not report.get("failed") and info["missing"]:
                        report["failed"] = len(info["missing"])
                if not report.get("failed"):
                    cfg_now["local_only"] = True
                    save_config(cfg_now)
                    self.root.after(0, lambda: self.local_var.set(True))
                    self.write("以后出图只用本地文件，不会再联网（含 wiki 图标）。")
                else:
                    self.write("有下载失败的条目，暂不切换到纯本地模式；可以再点一次补全。")
            except Exception as exc:
                self.write("补全出错：" + str(exc))
        threading.Thread(target=job, daemon=True).start()

    def clear_texture_cache(self):
        cache = os.path.join(app_dir(), "vanilla_cache")
        size = 0
        if os.path.isdir(cache):
            for root, _dirs, files in os.walk(cache):
                for f in files:
                    try:
                        size += os.path.getsize(os.path.join(root, f))
                    except OSError:
                        pass
        if not messagebox.askyesno(
                "删除本地纹理缓存",
                f"确定删除本地原版纹理缓存吗？\n\n{cache}\n"
                f"（约 {round(size / 1048576, 1)} MB）\n\n"
                "删除后下次出图会重新按需联网下载，或从本地原版包读取。"):
            return

        def job():
            import shutil
            shutil.rmtree(cache, ignore_errors=True)
            cfg_now = load_config()
            cfg_now["local_only"] = False
            save_config(cfg_now)
            self.root.after(0, lambda: self.local_var.set(False))
            self.write(f"已删除本地纹理缓存：{cache}")
            self.write("已切回按需联网模式（缺什么补什么）。")
        threading.Thread(target=job, daemon=True).start()

    def close(self):
        """关窗：先掐掉所有待触发的 after 任务，再销毁窗口。

        分两批来源：

        - 本程序自己的日志轮询 `_drain_log`（每 60ms 自续）；
        - CustomTkinter 内部的两个自续轮询：外观模式（每 30ms）与 DPI 缩放（每 100ms）。
          这两个库既不保存任务 id 也没有关闭入口。

        `root.destroy()` 会把注册过的 Tcl 命令一并删掉，之后那些仍然排着的 after
        被 Tcl 调到就找不到命令，控制台会刷一串 `invalid command name ...`（关窗时能看到）。
        所以在 destroy 之前按 Tcl 层把待触发的任务全部取消 —— 反正要退出，全清是对的。
        """
        self._closing = True
        self._cancel_pending_afters()
        try:
            self.root.destroy()
        except Exception:
            pass

    def _cancel_pending_afters(self):
        """取消 Tcl 事件队列里所有还没触发的 after 任务。"""
        try:
            pending = self.root.tk.call("after", "info")
        except Exception:
            pending = ()
        for job in pending or ():
            try:
                self.root.tk.call("after", "cancel", job)
            except Exception:
                pass
        self._log_job = None

    def run(self):
        try:
            self.root.mainloop()
        finally:
            self._closing = True


def run_gui(paths):
    """启动图形界面。``paths`` 是命令行直接带进来的文件 / 目录（可为空）。"""
    MainWindow(list(paths or [])).run()
    return 0
