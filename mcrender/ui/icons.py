"""界面自绘图形：太阳 / 月亮 / 自动 / 下拉箭头 / 开关 / 圆角底板 / 投影。

所有图形都用 Pillow 在 4 倍尺寸上画好、再 LANCZOS 缩回来，边缘是抗锯齿的 ——
比在 ``tk.Canvas`` 上手画圆角干净得多，也不用自己算圆弧。
（投影 ``soft_shadow`` 是唯一的例外：它是模糊出来的，本来就没有硬边。）

**三个外观图标是外部位图**（``assets/icons/appearance_*.png``，由用户给的 SVG
光栅化而来）：它们是单色形状，运行时只取 alpha 通道当遮罩、再按当前主题的
文字色染色，所以浅 / 深两版各染一次。位图缺失时才退回下面手绘的
``_sun`` / ``_moon`` / ``_auto``。其余图形（箭头 / 开关 / 圆角底板）仍是现画。

每种图形都出「浅色版 + 深色版」两张，包成 ``CTkImage`` 交给 CustomTkinter
按当前外观自动挑选，省得自己监听外观切换。圆角底板（``rounded_rect``）返回
裸的 PIL 图，由调用方自己决定怎么显示。

本模块本身只用到 PIL（``rounded_rect`` 可以直接拿来做像素级核对），不过它
``from . import theme``，而 theme 要 import customtkinter —— 所以还是得用
带 tkinter 的解释器跑。

尺寸：图标 16、下拉箭头 12、开关轨道 36×20（滑块静止 11、悬停 14）。轨道比 WinUI 3
定义的 40 略短、滑块也略小，按使用者的观感微调过（见 ``tools/preview_ui_icons.py``）。

开关还支持**中间帧**：``switch_pair(on, hover, progress, travel, knob)`` 的
``progress`` 是颜色进度、``travel`` 是滑块行程、``knob`` 是滑块「悬停长大」的进度，
``FluentSwitch`` 的切换 / 悬停动画就是逐帧调它画出来的（这三个参数为什么分开，
见 ``_switch`` 的说明）。
"""
from __future__ import annotations

import math
import os
import sys

from PIL import Image, ImageDraw, ImageFilter

from . import shape, theme

SS = 4                        # 超采样倍数
SHADOW_SS = 2                 # 阴影遮罩的超采样倍数（模糊本身就把边缘磨平了，2 倍足够）
SHADOW_BLUR = 2.0             # 高斯半径（= σ）= 铺开距离 / 它（见 soft_shadow）
SHADOW_TAIL = 3.0             # 尾巴铺到几倍 σ 之外才看不见（留白要 ≥ 它，见 shadow_pad）
ICON = 16                     # 图标名义尺寸
CHEVRON = 12                  # 下拉箭头
TRACK_W, TRACK_H = 36, 20     # ToggleSwitch 轨道（WinUI 3 是 40，这里收短一点）
KNOB = 11                     # ToggleSwitch 滑块直径（静止态）
KNOB_HOVER = 14               # 悬停时的滑块直径（轨道 20 的 0.70，与 Win11 参考图 25/35 一致）

# 外观图标 = 三张位图（见模块 docstring）。文件名按「该图标代表的模式」取，
# 而不是按图形本身：浅色模式配太阳、深色模式配月亮、跟随系统配显示器。
APPEARANCE_FILES = {
    "light":  "appearance_light.png",     # 太阳（浅色）
    "dark":   "appearance_dark.png",      # 月亮（深色）
    "system": "appearance_system.png",    # 显示器（跟随系统）
}

_cache: dict = {}

# 「按参数建一张图」的缓存。**只入不可变的、只读的合成图**：尺寸 / 半径 / 颜色
# 在一次会话里几乎不变，而重算一张面板（4 倍超采样 + LANCZOS）要 17~27ms、投影
# 要 9~11ms —— 这些全都挂在「点开下拉」的同步等待里。
#
# ⚠️ 拿到的图**别就地改**（缓存会把同一个对象发给下一个调用方）。要改先 ``.copy()``。
_RECT_CACHE: dict = {}
_SHADOW_CACHE: dict = {}
_CACHE_MAX = 24                 # 一张面板图约 0.3MB、投影约 0.6MB，真实键只有「几种尺寸 × 两套主题」


def clear_caches() -> None:
    """清掉建图缓存（换主题 / 换 DPI 之后想强制重算时用；平时不必调）。"""
    _RECT_CACHE.clear()
    _SHADOW_CACHE.clear()


def _remember(cache: dict, key, img):
    if len(cache) >= _CACHE_MAX:        # 够用了：真实的键只有「几种尺寸 × 两套主题」
        cache.clear()
    cache[key] = img
    return img


def _blank(w: int, h: int) -> tuple:
    s = (w * SS, h * SS)
    return Image.new("RGBA", s, (0, 0, 0, 0)), s


def _shrink(img: Image.Image, size) -> Image.Image:
    return img.resize(size, Image.LANCZOS)


def _dot(d, x, y, r, color) -> None:
    d.ellipse([x - r, y - r, x + r, y + r], fill=color)


def _rgb(value) -> tuple:
    """把 ``#RRGGBB``（或 ``#RGB``）拆成 ``(r, g, b)``。"""
    s = str(value).lstrip("#")
    if len(s) == 3:
        s = "".join(ch * 2 for ch in s)
    return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))


def mix(c0, c1, t) -> str:
    """两个颜色之间按 ``t`` 线性混合，返回 ``#RRGGBB``（``t`` 先夹到 ``[0,1]``）。

    开关的中间帧要在「关」「开」两套配色之间过渡：轨道填充、描边、滑块都走这一条。
    刻意**逐通道线性**而不转 HSL —— 两端色相差别很小（灰 → 蓝），线性插值不会发浑，
    而且和 Fluent 自己的过渡方式一致。``t=0`` / ``t=1`` 时返回的正是两端原色，
    所以动画的首末帧和 ``switch_pair`` 的端点缓存**同色**，接得上。
    """
    t = shape.clamp01(t)
    a, b = _rgb(c0), _rgb(c1)
    return "#%02X%02X%02X" % tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


# --------------------------- 外观图标（旧手绘版：只在 assets/icons 缺图时兜底）
def _sun(color: str) -> Image.Image:
    """（兜底）浅色模式：实心圆 + 八条光芒。"""
    img, (w, _h) = _blank(ICON, ICON)
    d = ImageDraw.Draw(img)
    c = w / 2
    r = w * 0.20
    lw = max(1, round(w * 0.090))
    _dot(d, c, c, r, color)
    for i in range(8):
        a = math.radians(i * 45)
        x0, y0 = c + math.cos(a) * (r + w * 0.060), c + math.sin(a) * (r + w * 0.060)
        x1, y1 = c + math.cos(a) * (r + w * 0.220), c + math.sin(a) * (r + w * 0.220)
        d.line([x0, y0, x1, y1], fill=color, width=lw)
        _dot(d, x1, y1, lw / 2, color)          # 圆头
    return _shrink(img, (ICON, ICON))


def _moon(color: str) -> Image.Image:
    """（兜底）深色模式：用「大圆挖掉一个偏移圆」做出月牙。"""
    img, (w, _h) = _blank(ICON, ICON)
    d = ImageDraw.Draw(img)
    c = w / 2
    r = w * 0.395
    d.ellipse([c - r, c - r, c + r, c + r], fill=color)
    r2 = r * 1.02
    dx = w * 0.30
    d.ellipse([c - r2 + dx, c - r2, c + r2 + dx, c + r2], fill=(0, 0, 0, 0))
    return _shrink(img, (ICON, ICON))


def _auto(color: str) -> Image.Image:
    """（兜底）跟随系统：左半实心 + 整圆描边（WinUI 的「对比度」图标）。"""
    img, (w, h) = _blank(ICON, ICON)
    pad = w * 0.135
    box = [pad, pad, w - pad, h - pad]
    d = ImageDraw.Draw(img)
    d.ellipse(box, fill=color)
    half = Image.new("L", (w, h), 0)
    ImageDraw.Draw(half).rectangle([w // 2, 0, w, h], fill=255)
    img.paste((0, 0, 0, 0), (0, 0), half)        # 抹掉右半
    d = ImageDraw.Draw(img)
    d.ellipse(box, outline=color, width=max(1, round(w * 0.085)))
    return _shrink(img, (ICON, ICON))


def _chevron(color: str) -> Image.Image:
    """下拉箭头：一个圆头的「∨」。"""
    img, (w, h) = _blank(CHEVRON, CHEVRON)
    d = ImageDraw.Draw(img)
    lw = max(1, round(w * 0.115))
    dx, dy = w * 0.30, w * 0.155
    cx, cy = w / 2, h / 2
    pts = [(cx - dx, cy - dy), (cx, cy + dy), (cx + dx, cy - dy)]
    d.line(pts, fill=color, width=lw, joint="curve")
    for x, y in pts:
        _dot(d, x, y, lw / 2, color)
    return _shrink(img, (CHEVRON, CHEVRON))


def _switch(on: bool, hover: bool, dark: bool, progress=None, travel=None,
            knob=None) -> Image.Image:
    """WinUI 3 ToggleSwitch：胶囊轨道 + 圆点滑块。

    关闭态是「透明轨道 + 描边」，鼠标悬停时轨道填一层浅灰；
    打开态轨道填强调色，滑块换成反色。

    **悬停时滑块会长大一圈**（``KNOB`` → ``KNOB_HOVER``，轨道高 20 里 11 → 14）——
    这是 Win11 的行为，用户给的参考图里 35px 轨道上的滑块是 21 → 25。

    三个动画参数分工明确（都不传就是静态端点，结果与以前**逐像素一致**）：

    - ``progress`` —— 颜色进度（0 = 关的配色、1 = 开的配色），走**线性**；
    - ``travel`` —— 滑块行程（0 = 贴左、1 = 贴右），调用方给什么就是什么；
    - ``knob`` —— 滑块的「悬停长大」进度（0 = 静止尺寸、1 = 悬停尺寸）。
      不传时按 ``hover`` 取 0/1，所以静态端点图与以前**同形同色**。

    为什么位置要分成 ``travel`` 和 ``progress`` 两个：位置要用 Fluent 的
    ``fast_out_slow_in``，颜色要线性过渡，两条曲线不一样；而且**位置必须由调用方按方向
    各自缓动** —— 如果这里对绝对进度取 ``ease(t)``，那么「关」的时候 t 从 1 往回走就变成
    「先不动、最后猛跳」了。``travel`` 不传时按 ``progress`` 自行取一次 ease（静态端点
    与旧行为一致，因为 ease(0)=0、ease(1)=1）。
    """
    t = (1.0 if on else 0.0) if progress is None else shape.clamp01(progress)
    p = shape.fast_out_slow_in(t) if travel is None else shape.clamp01(travel)
    hp = (1.0 if hover else 0.0) if knob is None else shape.clamp01(knob)

    img, (w, h) = _blank(TRACK_W, TRACK_H)
    d = ImageDraw.Draw(img)
    lw = max(1, round(SS * 1.3))
    inset = lw / 2
    box = [inset, inset, w - inset, h - inset]
    radius = (h - lw) / 2

    accent = theme.color("accent", dark)
    # 轨道填充 = 两层叠加：底下是悬停那层浅灰（alpha = hp），上面是强调色（alpha = t）。
    # 这里直接把合成结果算成 (r, g, b, a) 一次画完 —— PIL 的 ``rounded_rectangle`` 是
    # **覆盖**不是混合，分层画的话上层会把下层整块擦掉。端点：hp=1 时退化成
    # ``mix(control_hover, accent, t)`` 的不透明色、hp=0 时退化成「强调色按 t 淡入」，
    # 与加悬停动画之前逐像素相同。
    a_t, a_h = t, hp
    alpha = a_t + a_h * (1.0 - a_t)
    if alpha > 0.0:
        ar, ag, ab = _rgb(accent)
        hr, hg, hb = _rgb(theme.color("control_hover", dark))
        w_t = a_t / alpha
        d.rounded_rectangle(
            box, radius=radius,
            fill=(round(ar * w_t + hr * (1 - w_t)),
                  round(ag * w_t + hg * (1 - w_t)),
                  round(ab * w_t + hb * (1 - w_t)),
                  round(255 * alpha)))

    # 描边：关闭态是中性灰，打开态与轨道同色
    d.rounded_rectangle(box, radius=radius, width=lw,
                        outline=mix(theme.color("switch_stroke", dark), accent, t))

    # 滑块四周留白一律取 (轨道高 - 滑块) / 2：这样它到轨道左/右/上/下描边的距离
    # 完全相等。以前按「描边宽 + 固定值」算横向留白，比纵向小，看着偏左。
    # 悬停时直径变大 → 留白跟着缩小，滑块始终**居中**、往四边长出去。
    dia = KNOB + (KNOB_HOVER - KNOB) * hp
    kr = dia * SS / 2
    pad = (h - dia * SS) / 2
    left, right = pad + kr, w - pad - kr
    kx = left + (right - left) * p
    kc = mix(theme.color("switch_knob", dark), theme.color("on_accent", dark), t)
    _dot(d, kx, h / 2, kr, kc)
    return _shrink(img, (TRACK_W, TRACK_H))


# ------------------------------------------------- 外观图标位图（assets/icons）
def _icon_roots() -> list:
    """找 ``assets/`` 的候选根目录：exe 旁 → 打包内（``_MEIPASS``）→ 源码根。

    刻意**不 import ``..app``**（那会连带 ``mcrender.ui.__init__`` 的 customtkinter）：
    ``tools/preview_ui_icons.py`` 把本文件单独加载进一个假包里、只桩掉 ``theme``，
    多一个跨包导入它就加载不起来了。根目录语义与 ``theme.icon_candidates()`` 一致。
    """
    roots = []
    if getattr(sys, "frozen", False):
        roots.append(os.path.dirname(os.path.abspath(sys.executable)))
        mei = getattr(sys, "_MEIPASS", None)
        if mei:
            roots.append(mei)
    here = os.path.dirname(os.path.abspath(__file__))            # .../mcrender/ui
    roots.append(os.path.dirname(os.path.dirname(here)))         # 源码根
    out = []
    for root in roots:
        if root and root not in out:
            out.append(root)
    return out


def _appearance_mask(mode: str):
    """位图图标的 alpha 通道 —— 三张图都是单色，形状就够了，颜色在运行时染。"""
    name = APPEARANCE_FILES.get(mode, APPEARANCE_FILES["system"])
    for root in _icon_roots():
        path = os.path.join(root, "assets", "icons", name)
        if os.path.isfile(path):
            try:
                with Image.open(path) as im:
                    return im.convert("RGBA").getchannel("A")
            except Exception:
                return None
    return None


def _tinted(mask, color: str) -> Image.Image:
    """把遮罩按主题色染成 ``ICON`` 大小（先在 4 倍尺寸上缩，再 LANCZOS 缩回）。"""
    big = mask.resize((ICON * SS, ICON * SS), Image.LANCZOS)
    img = Image.new("RGBA", big.size, color)
    img.putalpha(big)
    return _shrink(img, (ICON, ICON))


# ------------------------------------------------------------------ 对外接口
def appearance_pair(mode: str) -> tuple:
    """返回 (浅色版, 深色版) 两张外观图标。

    优先用 ``assets/icons`` 下的位图（见模块 docstring）；读不到就退回下面手绘的
    ``_sun`` / ``_moon`` / ``_auto``，保证没带资源时界面也不至于空一块。
    """
    key = ("appearance", mode)
    if key not in _cache:
        mask = _appearance_mask(mode)
        if mask is None:
            fn = {"light": _sun, "dark": _moon}.get(mode, _auto)
            _cache[key] = (fn(theme.color("text", False)), fn(theme.color("text", True)))
        else:
            _cache[key] = (_tinted(mask, theme.color("text", False)),
                           _tinted(mask, theme.color("text", True)))
    return _cache[key]


def chevron_pair() -> tuple:
    """下拉箭头，颜色跟随次级文字色。"""
    if "chevron" not in _cache:
        _cache["chevron"] = (_chevron(theme.color("text_muted", False)),
                             _chevron(theme.color("text_muted", True)))
    return _cache["chevron"]


def switch_pair(on: bool, hover: bool = False, progress=None, travel=None,
                knob=None) -> tuple:
    """开关外观，返回 (浅色版, 深色版)。

    ``progress`` / ``travel`` / ``knob`` 给动画用（含义见 ``_switch``）。**中间帧不进
    缓存** —— 一条 180ms 的动画会出十几张互不相同的图，塞进模块级的 ``_cache`` 只会白占
    内存；端点那几帧仍然照常缓存，日常渲染一个字节都不多画。
    """
    if progress is None and travel is None and knob is None:
        key = ("switch", bool(on), bool(hover))
        if key not in _cache:
            _cache[key] = (_switch(on, hover, False), _switch(on, hover, True))
        return _cache[key]
    return (_switch(bool(on), bool(hover), False, progress, travel, knob),
            _switch(bool(on), bool(hover), True, progress, travel, knob))


# ------------------------------------------------------------------ 圆角底板
def rounded_rect(size, radius, fill=None, stroke=None, border=0, bg=None) -> Image.Image:
    """带描边的圆角矩形（4 倍超采样后缩回，边缘抗锯齿）。

    给下拉字段、下拉面板和选项块当底板用。**不用 ``tk.Canvas`` 自己画**：
    Tk 没有抗锯齿，圆角是一段段折线拼的，转角看着就是「不平滑、线还细」；
    而且 ``Canvas`` 与 CTk 的描边都是沿路径居中画的，贴着控件边沿时会被裁掉半条
    —— CTk 的 CTkFrame 就踩了尺寸取偶数的坑，175% 缩放下字段**底边整条描边不见**。

    参数都是**物理像素**：``size`` 目标尺寸、``radius`` 圆角半径、``border``
    描边宽（0 = 不描边）、``bg`` 先铺满的底色（不传则四个角是透明的，
    给 ``-transparentcolor`` 用）。
    """
    w, h = int(round(size[0])), int(round(size[1]))
    if w <= 0 or h <= 0:
        return Image.new("RGBA", (max(1, w), max(1, h)), (0, 0, 0, 0))
    key = (w, h, round(float(radius), 2), fill, stroke, border, bg)
    hit = _RECT_CACHE.get(key)
    if hit is not None:
        return hit                       # 只读：别就地改（见 _RECT_CACHE 的注释）
    if bg is not None:
        img = Image.new("RGBA", (w * SS, h * SS), bg)
    else:
        img = Image.new("RGBA", (w * SS, h * SS), (0, 0, 0, 0))
    W, H = img.size
    d = ImageDraw.Draw(img)

    b = max(0, round(border * SS))
    # Pillow 的 rectangle/rounded_rectangle 把描边画在**边界以内**（不是沿路径居中），
    # 所以 bbox 直接取满整张图：描边最外那条像素正好落在图像边沿，
    # 四条边粗细一致、也不会像 Canvas 那样被裁掉半条。
    box = [0, 0, W - 1, H - 1]
    r = max(0.0, min(radius * SS, W / 2, H / 2))
    if fill is not None:
        d.rounded_rectangle(box, radius=r, fill=fill)
    if stroke is not None and b > 0:
        d.rounded_rectangle(box, radius=r, outline=stroke, width=b)
    return _remember(_RECT_CACHE, key, _shrink(img, (w, h)))


def rounded_rect_pair(size, radius, fill, stroke, border=0, bg=None) -> tuple:
    """同一形状的 (浅色版, 深色版)：``fill`` / ``stroke`` / ``bg`` 传 (浅, 深) 令牌值。"""
    key = ("rect", tuple(size), round(radius, 2), border, fill, stroke, bg)
    if key not in _cache:
        def one(i):
            return rounded_rect(size, radius,
                                fill=fill[i] if fill else None,
                                stroke=stroke[i] if stroke else None,
                                border=border,
                                bg=bg[i] if bg else None)
        _cache[key] = (one(0), one(1))
    return _cache[key]


# ------------------------------------------------------------------ 投影
def shadow_pad(spread, offset=(0, 2)) -> int:
    """用 ``spread`` 生成投影时，四周至少要留多少像素才**不会把尾巴切掉**。

    ``soft_shadow`` 里高斯半径 σ = ``spread / SHADOW_BLUR``，而 ``PIL`` 的
    ``GaussianBlur(radius)`` 传进去的就是 σ —— 尾巴要到 ``SHADOW_TAIL`` 个 σ 之外才
    真的看不见。所以留白必须 ≥ 3σ（再加上 ``offset`` 那一丁点下移）。

    ⚠️ 早先这里把「留白」和 ``spread`` 当成同一个数（于是留白 = 2σ），尾巴在画布
    边沿被**硬切**了一刀 —— 面板四周那圈柔光的外沿因此是一条笔直的边，背景稍暗一点
    就看得出来（用户报的「阴影边缘不自然」）。留白现在单独算。
    """
    spread = max(0, int(round(spread)))
    if not spread:
        return 0
    return int(math.ceil(spread * SHADOW_TAIL / SHADOW_BLUR)) + int(abs(round(offset[1])))


def soft_shadow(size, radius, spread, opacity=0.42, offset=(0, 2),
                color="#000000", pad=None) -> Image.Image:
    """四周一圈柔和的投影（RGBA，可以直接 ``alpha_composite`` 到画面里）。

    参数都是**物理像素**。``size`` 是**整块画布**（含四周留白），``pad`` 是留白
    有多宽（默认 = ``spread``）；阴影的形状按「画布内缩 pad」那个圆角矩形来，也就
    正好是要投影的那块面板。``radius`` 要和面板的圆角一致。

    做法是把一个**实心**圆角矩形遮罩丢给高斯模糊：

    - 一定要实心。用 ``outline`` 画一圈再模糊的话，中间会空出一块比边缘浅 ——
      因为描边本身就只有一圈，模糊只是把那一圈抹开。
    - 模糊半径（= σ）取 ``spread / SHADOW_BLUR``，尾巴到 ``SHADOW_TAIL`` 个 σ 之外
      才看不见 —— 留白够不够要拿 :func:`shadow_pad` 算，别想当然地取 ``spread``。
    - ``offset`` 把阴影整体**下移**一点点（WinUI 浮出控件的投影就是略偏下的：
      光源在上方，所以底下那圈比顶上厚）。补偿办法是把遮罩里的形状先上移
      ``offset``、模糊完再整体下移回来 —— 形状本身仍落在留白正中，只是模糊的尾巴
      偏下。直接挪遮罩的话底部会在图像边沿被切掉。

    ``opacity`` 是**遮罩中心**的不透明度。⚠️ 面板边缘那一圈的实际值只有它的
    **一半**左右 —— 模糊一个「阶跃」边，边界处的值正好是 50%。调阴影浓淡时按这个
    换算，别照着 ``opacity`` 去猜（0.22 → 面板边上约 11% 黑）。
    """
    w, h = int(round(size[0])), int(round(size[1]))
    spread = max(0, int(round(spread)))
    pad = spread if pad is None else max(0, int(round(pad)))
    if w <= 2 * pad or h <= 2 * pad or spread <= 0:
        return Image.new("RGBA", (max(1, w), max(1, h)), _rgb(color) + (0,))
    key = (w, h, round(float(radius), 2), spread, pad, round(float(opacity), 4),
           (int(round(offset[0])), int(round(offset[1]))), color)
    hit = _SHADOW_CACHE.get(key)
    if hit is not None:
        return hit                       # 只读：别就地改（见 _RECT_CACHE 的注释）

    big = SHADOW_SS
    ox, oy = int(round(offset[0])), int(round(offset[1]))
    mask = Image.new("L", (w * big, h * big), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [(pad - ox) * big, (pad - oy) * big,
         (w - pad - ox) * big - 1, (h - pad - oy) * big - 1],
        radius=max(0, radius * big), fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(spread * big / SHADOW_BLUR))
    if oy:                                  # 模糊完再挪回来，尾巴才不会被切
        shifted = Image.new("L", mask.size, 0)
        shifted.paste(mask, (0, oy * big))
        mask = shifted
    mask = mask.resize((w, h), Image.LANCZOS)

    img = Image.new("RGBA", (w, h), _rgb(color) + (0,))
    img.putalpha(mask.point(lambda v: round(v * shape.clamp01(opacity))))
    return _remember(_SHADOW_CACHE, key, img)
