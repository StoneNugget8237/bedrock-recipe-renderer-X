"""真实窗口冒烟测试：把界面**真的建起来**跑一遍（不是离线渲染推测）。

    python tools/smoke_ui.py

为什么需要它：`mcrender/ui` 里大量行为只有在真实 Tk 窗口里才看得出来，离线静态分析
一个都抓不到。这几个真 bug 就是它（或它的前身）抓出来的：

- ``FluentComboBox._bind`` —— 撞上 ``tkinter.Misc._bind``，``super().__init__()`` 直接 TypeError；
- ``FluentComboBox._canvas`` —— 撞上 ``CTkFrame`` 内部画布，关一次下拉就 AttributeError；
- 下拉面板没跟 DPI 缩放 —— 175% 缩放下字段 21px 字、面板里只有 14px，「文字过小」。
- 字段里的文字 / 箭头那两个不透明 CTkLabel 压住底边描边 —— 「下方框线不均匀」；
- 选项文字和字段文字共用左边距，正好从选中竖条**里面**起步 —— 「竖线与文字过近」；
- ``tk.Canvas`` 把 ``lift`` / ``tkraise`` 都重定向成了 ``tag_raise``，面板抬不起来。

⚠️ 「点下拉选项会顺带点到下面盖着的按钮」那个 bug **不是**它抓到的 —— 它当时用的断言
（按下后立刻看面板还在不在）一路通过，因为**一次点击是两个事件**，问题出在按下与松手
之间的那段时间、以及 release 由 OS 按指针位置投递这两点上，``event_generate`` 都模拟不出来。
那个 bug 是 ``tools/check_option_click.py``（合成真鼠标事件）抓的；本文件里保留了它的
确定性版本 —— 「按住期间面板必须一直活着」，跑够真实时间把 idle 队列清空才算数。

历史上还有几个只在「面板是独立窗口」时才存在的 bug（分层窗口把色块叠在文字层之上 →
「选中选项无法显示文字」；烘焙抓图时竖条还在画布上被反解成文字色的竖条 →
「竖线有时渲染为黑色」；反解文字层的遮罩是直角的，够不着圆角上的描边弧 →
「深色模式下角落颜色有些奇怪」；弹窗是 -topmost，焦点落在它上面时切走收不到 FocusOut →
「切窗口后下拉依然置顶」）。面板改成本窗口里的子控件之后这几个机制**整体删掉了**，
对应的断言也换成了「面板就是子控件 / 四角露的是真实背景」这类新的判据。

检查项：

1. **几何** —— 面板左沿与字段对齐、**选中那一行的中线压在字段中线上**（面板因此向上伸出
   字段之外、盖住字段）、竖条与文字留出的缝、四周那圈投影的浓淡渐变；几何随 DPI 换算。
   （这一节构造下拉时带 ``animate=False``：量的是**稳定态**。）
2. **动画** —— 下拉展开是**以选中那一行为中心向两侧延展**（露出带 + 淡显出来）、
   展开途中**没有**投影、会自己走到铺满画布且完全不透明，紧接着投影由 0 淡到 1
   （``SHADOW_MS``）；每个动作都画满 ≥10 帧
   （帧数靠帧间隔 6ms + 动画期间提计时器精度抢来，播完必须归还），收起瞬间
   ``_popup`` 就归位但画布还播完收起动画再销毁；开关逐帧滑到端点；``animate=False``
   时全程同步（见 ``check_animations()``）。
3. **面板在主窗口里** —— 同一个顶层窗口（任务栏里不会多一个）、整个落在客户区里、
   四角露的是被盖住的真实背景、主窗口一动就收起。
4. **点选项的时序** —— 按下时值立刻生效、但面板必须**活到松手**（松手那一下由面板吃掉，
   不会漏给下面盖着的按钮），松手之后才收。合成真鼠标的完整版在 ``check_option_click.py``。
5. **主窗口** —— 整个窗口建得起来，4 个下拉都能展开、开关能切、外观循环能走。
6. **关窗安静** —— 关窗后不能再刷 ``invalid command name``（见下面「为什么要单独起进程」）。

**为什么要单独起进程查关窗**：Tcl 对已删除命令的报错走的是 ``bgerror``，只打印到
stderr，不抛 Python 异常、也拦不住。所以只能在子进程里跑一遍关窗，再看 stderr 干不干净。

解释器：需要带 ``tkinter`` **且**装了 ``customtkinter``。当前解释器不满足时会自动在同机
其它 Python 里找一个重新执行自己（找不到就明确报错，而不是假装通过）。
"""
from __future__ import annotations

import glob
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

_GUARD = "MC_RENDER_SMOKE_REEXEC"


# ------------------------------------------------------------------ 解释器
def _has_gui(python: str) -> bool:
    try:
        done = subprocess.run([python, "-c", "import tkinter, customtkinter"],
                              capture_output=True, timeout=60)
    except Exception:
        return False
    return done.returncode == 0


def find_gui_python() -> str | None:
    """找一个能跑界面的解释器：带 tkinter 且装了 customtkinter。"""
    if _has_gui(sys.executable):
        return sys.executable
    candidates: list[str] = []
    if os.name == "nt":
        bases = [os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Python"),
                 r"C:\Program Files", r"C:\Program Files (x86)", "C:\\"]
        for base in bases:
            if base and os.path.isdir(base):
                candidates += sorted(glob.glob(os.path.join(base, "Python3*", "python.exe")))
                candidates.append(os.path.join(base, "python.exe"))
    for name in ("python3", "python"):
        for directory in os.environ.get("PATH", "").split(os.pathsep):
            candidates.append(os.path.join(directory, name))
    seen = set()
    for exe in candidates:
        if not exe or exe in seen or not os.path.isfile(exe):
            continue
        seen.add(exe)
        if _has_gui(exe):
            return exe
    return None


def ensure_gui_python(script: str | None = None) -> None:
    """当前解释器跑不了界面就把自己交给一个能跑的解释器，别静默跳过检查。

    ``script`` 是要重新执行的脚本，默认是**本文件**。被别的工具 import 时务必
    显式传自己的 ``__file__`` —— 否则会去重跑 smoke_ui 而不是调用者的脚本。
    """
    try:
        import tkinter  # noqa: F401
        import customtkinter  # noqa: F401
        return
    except Exception:
        pass
    if os.environ.get(_GUARD):
        print("! 换了解释器仍然缺 tkinter / customtkinter，界面无法实跑。", file=sys.stderr)
        sys.exit(2)
    found = find_gui_python()
    if not found:
        print("! 本机没找到「带 tkinter 且装了 customtkinter」的 Python，界面无法实跑。\n"
              "  装一个再试：pip install customtkinter", file=sys.stderr)
        sys.exit(2)
    target = script or os.path.abspath(__file__)
    print(f"（当前解释器缺 tkinter，改用：{found}）")
    os.environ[_GUARD] = "1"
    os.execv(found, [found, target] + sys.argv[1:])


# ------------------------------------------------------------------ 检查项
RESULT: list[tuple[bool, str, str]] = []


def record(ok: bool, name: str, detail: str = "") -> None:
    RESULT.append((ok, name, detail))
    print(f"  {'✓' if ok else '✗'} {name}" + (f"  —— {detail}" if detail else ""))


def walk(widget):
    yield widget
    for child in widget.winfo_children():
        yield from walk(child)


def check_geometry() -> None:
    """下拉字段与面板的位置、几何是否跟 DPI 走，以及面板**是不是主窗口里的子控件**。"""
    print("\n[1] 下拉定位、DPI 换算与「面板在主窗口里」")
    import tkinter as tk

    import customtkinter as ctk

    from mcrender.ui import icons, theme, widgets, win32

    ctk.set_appearance_mode("dark")
    root = ctk.CTk()
    root.geometry("600x400+200+200")
    root.update()

    # 铺一块**明显的绿色**当底：面板四角露出来的必须是它，而不是窗口底色。
    # 只用窗口底色的话，「四角填了窗口底色」这个错误实现也会通过（它俩颜色很近）。
    bg_frame = ctk.CTkFrame(root, fg_color="#0F7B0F", corner_radius=0)
    bg_frame.place(x=0, y=0, relwidth=1, relheight=1)
    root.update()

    fonts = theme.Fonts()
    # ``animate=False``：这一节量的是**稳定态**的几何与像素（色块、竖条、描边），
    # 面板带着展开动画时抓到的是一块正在长高的画布，位置对不上也没法逐像素判。
    # 动画本身由 ``check_animations()`` 单独验（那节会真的按时间泵帧）。
    box = widgets.FluentComboBox(root, variable=tk.StringVar(value="x2"),
                                 values=["x1", "x2", "x4", "x8"], width=150, height=30,
                                 font=fonts.small, menu_font=fonts.menu, animate=False)
    # ⚠️ 字段要往下摆够：面板是**盖在字段上**的（选中那一行对齐字段中线），选中的是第 2 项，
    # 面板顶边会跑到字段上方 ``pad + pad_v + 1 行`` 那么高。贴着顶边摆的话那一下会被
    # 「夹进客户区」顶下来，下面「选中行压在字段中线上」就量不到了（量到的是夹过之后的位置）。
    box.pack(padx=30, pady=90)
    root.update()

    # 展开**之前**先抓一张「没有面板」的窗口内容，当作背景真值
    baseline = win32.grab(root)

    box._open()
    root.update()
    geom, popup = box._geom, box._popup
    scale = box._get_widget_scaling()
    # ⚠️ 画布比面板四周大一圈（那一圈留给投影，见 ``_open``）。所有「面板几何」的测算
    # 都要先减掉这个偏移；``geom`` / ``_bounds`` 本身仍是**面板坐标**。
    pad, top = box._shadow_pad, box._shadow_top

    record(popup.winfo_rootx() + pad == box.winfo_rootx(), "面板左沿与字段对齐",
           f"字段 x={box.winfo_rootx()} 面板 x={popup.winfo_rootx()}(+{pad})")
    # 右沿也要齐：面板宽度**就是字段宽度**（字段在构造时按 ``MIN_W`` 兜底，见
    # ``FluentComboBox.__init__``）。早先面板取 ``max(字段宽, 120 逻辑像素)``，
    # 「缩放」字段只有 76 逻辑像素，一开菜单右边就探出一大截（用户报的「选框与
    # 下拉菜单对不齐」）。
    record(popup.winfo_rootx() + pad + box._panel_box[2]
           == box.winfo_rootx() + box.winfo_width(),
           "面板右沿与字段右沿对齐（面板与选框同宽）",
           f"字段右 {box.winfo_rootx() + box.winfo_width()} "
           f"面板右 {popup.winfo_rootx() + pad + box._panel_box[2]}")
    # 竖直位置 = 「选中那一行的中线 == 字段中线」—— Win11 的 ComboBox 浮出控件就是这么摆的
    # （拿系统设置里那个「缩放」下拉的前后两张截图配准后逐像素量过，偏差 0）。
    # 于是面板**向上伸出字段之外**，把字段连同它上面的内容一起盖住：这就是用户要的
    # 「以选中的选项为中心覆盖原来的显示区域」。早先那版是「挂在字段下方、顶边间距 = gap」。
    cur = str(box._var.get())
    pick = box._values.index(cur) if cur in box._values else 0
    row_cy = box._bounds[pick][0] + geom["row_h"] // 2
    panel_cy = popup.winfo_rooty() + top + row_cy
    field_cy = box.winfo_rooty() + box.winfo_height() // 2
    record(panel_cy == field_cy, "选中那一行的中线压在字段中线上",
           f"选中行 y={panel_cy} 字段 y={field_cy}（差 {panel_cy - field_cy}px，"
           f"「{cur}」是第 {pick + 1} 项）")
    record(popup.winfo_rooty() + top < box.winfo_rooty(),
           "面板向上伸出字段之外（盖住字段，而不是挂在它下面）",
           f"面板顶 y={popup.winfo_rooty() + top} 字段顶 y={box.winfo_rooty()}")
    record(top == pad
           and popup.winfo_height() == box._panel_box[3] + 2 * pad
           and popup.winfo_width() == box._panel_box[2] + 2 * pad,
           "画布四周留出等宽的投影余地（四边对称，顶边不再只探到字段底沿）",
           f"画布 {popup.winfo_width()}×{popup.winfo_height()}，"
           f"面板 {box._panel_box[2]}×{box._panel_box[3]}，四周留白={pad}（top={top}）")
    # 用户报的「阴影边缘不自然」：留白只要给到 ``spread``，高斯尾巴（~3σ）就会在画布
    # 边沿被硬切一刀 —— 面板四周那圈柔光的外沿变成一条直边，背景稍暗就看得出来。
    # 这条直接从生成的 alpha 通道上量：画布最外那一圈的 alpha 必须是 0。
    spread = max(0, round(box.SHADOW_SPREAD * scale))
    shadow = icons.soft_shadow((popup.winfo_width(), popup.winfo_height()),
                               radius=geom["radius"], spread=spread, pad=pad,
                               opacity=box.SHADOW_OPACITY)
    band = shadow.getchannel("A")
    bw, bh = band.size
    ring = max(band.crop((0, 0, bw, 1)).getextrema()[1],
               band.crop((0, bh - 1, bw, bh)).getextrema()[1],
               band.crop((0, 0, 1, bh)).getextrema()[1],
               band.crop((bw - 1, 0, bw, bh)).getextrema()[1])
    record(ring == 0,
           "投影的尾巴在画布边沿已经衰减到 0（柔光外沿不会被硬切出一条直边）",
           f"σ={spread / icons.SHADOW_BLUR:.1f}px，留白={pad}px（需 ≥{icons.SHADOW_TAIL}σ），"
           f"画布最外圈 alpha={ring}")
    row_h, font_size = geom["row_h"], geom["font"][1]
    record(abs(row_h - widgets.FluentComboBox.ROW_H * scale) <= 1, "行高跟 DPI 缩放",
           f"row_h={row_h} ≈ {widgets.FluentComboBox.ROW_H}×{scale:.2f}")
    record(abs(abs(font_size) - abs(theme.FONT_SIZES["menu"]) * scale) <= 2,
           "选项字号跟 DPI 缩放",
           f"{font_size} ≈ {theme.FONT_SIZES['menu']}×{scale:.2f}")
    record(abs(font_size) > abs(theme.FONT_SIZES["menu"]), "选项字号大于逻辑值（没被漏缩放）",
           f"{font_size} vs {theme.FONT_SIZES['menu']}×1.00")

    # ---- 本轮改动的核心：面板必须是主窗口里的**子控件**，不是第二个窗口
    record(popup.master is root and popup.winfo_toplevel() is root,
           "面板挂在主窗口上（不是独立 Toplevel）",
           f"parent={popup.winfo_parent()} toplevel={popup.winfo_toplevel()}")
    record(win32.top_hwnd(popup) == win32.top_hwnd(root),
           "面板与主窗口共用一个顶层窗口（任务栏 / 切换器里不会多一个）",
           f"面板={win32.top_hwnd(popup)} 主窗口={win32.top_hwnd(root)}")

    hx, hy = root.winfo_rootx(), root.winfo_rooty()
    hw, hh = root.winfo_width(), root.winfo_height()
    px, py = popup.winfo_rootx() - hx, popup.winfo_rooty() - hy
    pw, ph = popup.winfo_width(), popup.winfo_height()
    record(px >= 0 and py >= 0 and px + pw <= hw and py + ph <= hh,
           "面板整个落在主窗口客户区里（子控件超出就会被裁掉）",
           f"面板 ({px},{py}) {pw}×{ph}，客户区 {hw}×{hh}")

    # 行距：量画出来的图元（色块是 image item，锚点在左中，coords 给 (x, y)）。
    # ⚠️ 图元坐标是**画布坐标**（比面板坐标多一个 ``(pad, top)`` 的投影留白），
    # 先减回去再和 ``geom`` 比 —— ``geom`` 与 ``_bounds`` 一律是面板坐标。
    pts = [box._menu_canvas.coords(item) for item in box._rows]
    xs0 = [round(p[0]) - pad for p in pts]
    cys = [round(p[1]) - top for p in pts]
    block_w, block_h = box._block_img.size
    pitch = cys[1] - cys[0]
    top0 = cys[0] - block_h // 2
    between = (cys[1] - block_h // 2) - (top0 + block_h)
    record(abs(pitch - geom["pitch"]) <= 1, "行距 = 行高 + 间距",
           f"{pitch}px (pitch={geom['pitch']})")
    record(abs(between - geom["item_gap"]) <= 1, "相邻选项块之间留出了行间距",
           f"{between}px (ITEM_GAP={widgets.FluentComboBox.ITEM_GAP}×{scale:.2f}"
           f"={geom['item_gap']})")
    record(abs(top0 - geom["pad_v"]) <= 1 and abs(xs0[0] - geom["pad_h"]) <= 1,
           "选项块按内边距缩在面板里",
           f"上沿 {top0} 左沿 {xs0[0]}")
    record(abs(block_h - geom["row_h"]) <= 1, "选项块高 = 行高",
           f"{block_h}px (row_h={geom['row_h']})")

    def card_rgb():
        c = box._palette["card"]
        return tuple(int(c[1:][k:k + 2], 16) for k in (0, 2, 4))

    def row_shaded(img, index):
        """第 index 项画出来有没有色块（``None`` = 抓不到图）。

        判据贴着色块**上沿往里 3px** 那条横线取：色块除圆角外整行铺满，而文字与蓝竖条
        都是**竖直居中**的 —— 贴着上沿探就不会撞上它们。以前取行中线，那时文字也在中线
        附近，会把「文字」误判成「有色块」。
        """
        if img is None:
            return None
        y = box._bounds[index][0] + 3
        xs = (geom["pad_h"] + 20, geom["pad_h"] + 60)
        return any(img.getpixel((x, y))[:3] != card_rgb() for x in xs)

    # 底色：普通项必须是「不显示」（露出面板底），只有当前值 / 悬停项才显色块。
    # 早期版本给每个选项都铺了一层灰底，列表看着是一格一格的方块。
    root.update()
    shaded = [row_shaded(box.panel_image(), i) for i in range(4)]
    record(shaded == [False, True, False, False],
           "普通项不铺底色（只当前值有色块）", f"色块={shaded}")
    box._hover_to(2)
    root.update()
    shaded = [row_shaded(box.panel_image(), i) for i in range(4)]
    record(shaded == [False, True, True, False], "悬停项才补上色块", f"色块={shaded}")
    box._hover_to(-1)
    root.update()

    # 选中竖条：用户要求「细一点、短一点」（宽 3 / 高 16 逻辑像素）
    pw_img, ph_img = box._pill_img.size
    want_w, want_h = round(widgets.FluentComboBox.PILL_W * scale), \
        round(widgets.FluentComboBox.PILL_H * scale)
    record(abs(pw_img - want_w) <= 1 and abs(ph_img - want_h) <= 1,
           "选中竖条细而短（宽 3 / 高 16 逻辑像素）",
           f"量出 {pw_img}×{ph_img}px，期望 {want_w}×{want_h}")

    # 用户反馈：「竖线与文字过近，没有留出一定距离」。以前选项文字和字段文字**共用**
    # ``text_x``（9 逻辑像素），而竖条占 ``pad_h + pill_x .. + pill_w`` = 7..10 ——
    # 文字正好从竖条**里面**起步（量真机截图：竖条 x=37..41，文字第 40 列就开始了）。
    # 现在选项文字的左沿改由「竖条右沿 + MENU_TEXT_GAP」算，按竖条走。
    pill_r_geom = geom["pad_h"] + geom["pill_x"] + geom["pill_w"]
    want_text_x = pill_r_geom + round(widgets.FluentComboBox.MENU_TEXT_GAP * scale)
    record(geom["menu_text_x"] >= want_text_x - 1 and geom["menu_text_x"] > geom["text_x"],
           "选项文字左沿从竖条右沿让出 MENU_TEXT_GAP",
           f"menu_text_x={geom['menu_text_x']} ≥ {want_text_x}"
           f"（字段文字 text_x={geom['text_x']}，竖条右沿={pill_r_geom}）")

    # 再量一次**画出来的最终图**：几何对不代表渲染对，得有可见的缝
    layer = box.panel_image()
    if layer is None:
        record(False, "竖条与选项文字之间有可见缝隙（量最终图）", "抓不到面板内容")
    else:
        from collections import Counter

        def rgb(hex_color):
            return tuple(int(hex_color[1:][k:k + 2], 16) for k in (0, 2, 4))

        accent = rgb(box._palette["accent"])
        picked_row = box._picked.index(True) if True in box._picked else 0
        ry0, ry1 = box._bounds[picked_row]
        cy = (ry0 + ry1) // 2
        prow = [layer.getpixel((x, cy))[:3] for x in range(layer.size[0])]
        pill = [x for x, c in enumerate(prow)
                if max(abs(c[i] - accent[i]) for i in range(3)) <= 12]
        if not pill:
            record(False, "竖条与选项文字之间有可见缝隙（量最终图）", "量不到竖条")
        else:
            # 判「墨迹」用「偏离该行背景色」—— 抗锯齿的中性灰离蓝色比离近黑还近，
            # 按最近色分类会把文字边缘误判成竖条（踩过）。
            bg = Counter(prow[max(pill) + 1:]).most_common(1)[0][0]
            ink = [x for x, c in enumerate(prow)
                   if x > max(pill) and max(abs(c[i] - bg[i]) for i in range(3)) > 60]
            gap_px = (min(ink) - max(pill) - 1) if ink else None
            record(gap_px is not None
                   and gap_px >= round(widgets.FluentComboBox.MENU_TEXT_GAP * scale) - 2,
                   "竖条与选项文字之间有可见缝隙（量最终图）",
                   f"竖条右沿={max(pill)} 文字首列={min(ink) if ink else None}"
                   f" 缝={gap_px}px")

    # 用户要求：鼠标触碰的加深程度要和选中的加深程度一致 —— 两档直接取同一个色值
    record(box._palette["hover"] == box._palette["selected"],
           "悬停与当前值同深浅（加深程度一致）",
           f"hover={box._palette['hover']} selected={box._palette['selected']}")

    # 用户反馈：字段「圆角过渡不平滑、角落线条太细」、下拉面板「下端无框线」。
    # 现在两者都是 Pillow 画的圆角图，直接量像素：四条边都得有描边。
    box._draw_field()                       # 自检里不等 after_idle，直接画一次
    root.update()

    # 字段里的文字与箭头必须**真的摆进去了**。
    # 这是个很容易整块丢掉的坑：CTk 的 ``place()`` **不接受 width=/height=**
    # （传了直接抛 ValueError），而它会被 ``_layout_children`` 的兜底 except 吞掉
    # —— 现象是「字段变成一块空白底，文字和箭头全都不见」，不报任何错。
    for label, widget in (("文字", box._text), ("箭头", box._arrow)):
        record(widget.winfo_ismapped() and 0 < widget.winfo_height() < box.winfo_height(),
               f"字段里的{label}摆进去了，且没盖住描边",
               f"映射={widget.winfo_ismapped()} y={widget.winfo_y()} "
               f"高={widget.winfo_height()} 字段高={box.winfo_height()}")

    def edges(img, name):
        w, h = img.size
        px = img.convert("RGB").load()
        got = [px[w // 2, 0], px[w // 2, h - 1], px[0, h // 2], px[w - 1, h // 2]]
        center = px[w // 2, h // 2]
        detail = ("边=" + "/".join("#%02X%02X%02X" % c for c in got)
                  + f"  中=#{center[0]:02X}{center[1]:02X}{center[2]:02X}")
        record(all(s == got[0] for s in got) and center != got[0],
               f"{name}四条边都有描边（含底边）", detail)

    field, panel = box._field_img, box._panel_img
    if field is None or panel is None:
        record(False, "字段 / 面板底板图已生成", f"field={field} panel={panel}")
    else:
        edges(field, "字段底板")
        edges(panel, "下拉面板")
        # 转角抗锯齿：不抗锯齿的话角上只会有「底色 / 描边 / 填充」三种颜色
        corner = field.convert("RGB").crop((0, 0, 12, 12))
        n = len(corner.getcolors(maxcolors=4096) or [])
        record(n > 3, "字段转角是抗锯齿的（有过渡色）", f"12×12 角上有 {n} 种颜色")

    # 用户反馈：「选择区域下方框线不均匀」。上面那两条只查了**画出来的底板图**，
    # 它是好的 —— 坏在**真窗口**里：字段里文字 / 箭头那两个 CTkLabel 是不透明的
    # 底色块，以前它们高 49、起点 y=2 → 一直盖到第 50 行，而描边正好占第 50/51 行，
    # 于是底边上半行被啃掉一条（浅色模式量出来 210~255 跳）。所以必须抓真实窗口。
    #
    # ⚠️ 必须在**面板关着**的时候抓：新定位下面板是**盖在字段上**的，而
    # ``win32.grab()`` 走 ``PrintWindow(PW_RENDERFULLCONTENT)`` —— 它给的是「这块矩形
    # **在屏幕上**的样子」，会把压在上面的兄弟画布一起抓进来（实测量到 76 = 面板底色，
    # 而不是字段描边的 62，四条边全被判成不均）。这一条要验的是「字段里的 Label 有没有
    # 啃到描边」，跟面板在不在毫无关系，所以关掉再量 —— 量完再开回来给后面几节用。
    box._close()
    root.update()
    shot = win32.grab(box)
    if shot is None:
        record(False, "字段四条描边整条一样深（没被里面的控件啃掉）", "抓不到窗口内容")
    else:
        w, h = shot.size
        rows = {}
        for y in (0, 1, h - 2, h - 1):
            vals = [sum(shot.getpixel((x, y))[:3]) / 3 for x in range(6, w - 6)]
            rows[y] = (min(vals), max(vals))
        bad = [y for y, (lo, hi) in rows.items() if hi - lo > 25]
        record(not bad, "字段四条描边整条一样深（没被里面的控件啃掉）",
               " ".join(f"y={y}:{lo:.0f}~{hi:.0f}" for y, (lo, hi) in rows.items())
               + "（面板关着量的）")
    box._open()
    root.update()
    # 关一次再开会换一块新画布，前面拿到的 ``popup`` 已经是**死窗口**了（再问它
    # ``winfo_width()`` 直接 ``bad window path name``）。重新取一份。
    geom, popup = box._geom, box._popup

    # ---- 面板四角：子控件没有 alpha，圆角那四块靠「背景快照」填。
    # 判据：四角像素必须**基本等于展开之前**抓的那张图里的同一个点 —— 也就是它露出来的
    # 就是被盖住的真实内容（这里是那块绿底），而不是一块方色的底或窗口底色。
    # ⚠️ 现在四角还紧贴着圆角投影，会被压暗一点点，所以判「接近 + 只会更暗」，不是全等。
    # ⚠️ ``panel_image()`` 裁的是**面板**那一块（不含四周投影留白），所以基准图的取样
    # 也要从面板的左上角算起 —— 也就是画布左上角再往里让 ``(pad, top)``。
    final = box.panel_image()
    if final is None or baseline is None:
        record(False, "面板四角露的是被盖住的背景（不是一块方色底）", "抓不到图")
    else:
        def rgb_(value) -> tuple:
            if isinstance(value, (tuple, list)):
                value = value[1] if ctk.get_appearance_mode() == "Dark" else value[0]
            text = str(value).lstrip("#")
            return tuple(int(text[i:i + 2], 16) for i in (0, 2, 4))

        def far(a, b) -> int:
            return max(abs(p - q) for p, q in zip(a, b))

        fw, fh = final.size
        probes = [(3, 3), (fw - 4, 3), (3, fh - 4), (fw - 4, fh - 4)]
        same, detail = [], []
        for cx, cy in probes:
            got = final.getpixel((cx, cy))[:3]
            want = baseline.getpixel((px + pad + cx, py + top + cy))[:3]
            same.append(all(0 <= want[i] - got[i] <= 25 for i in range(3)))
            detail.append(f"({cx},{cy}){'#%02X%02X%02X' % got}/基准{'#%02X%02X%02X' % want}")
        # 反面对照：既不能是面板自己的卡片色，也不能是窗口底色（都是「一块方色底」的形态）
        foreign = [rgb_(box._palette["card"]), rgb_(theme.color("window", True))]
        off = [far(final.getpixel(p)[:3], c) for p in probes for c in foreign]
        record(all(same) and min(off) > 30,
               "面板四角露的是被盖住的背景（不是一块方色底）",
               f"四角={detail}；离卡片色 / 窗口底至少 {min(off)} 级")

    # ---- 四周那圈投影（用户要求「下拉列表四周淡入阴影」）。
    # ``animate=False`` 时它是**静态**画在底图上的（不淡入），所以这里可以直接量像素：
    # 面板左边那一列必须比背景暗（有阴影），越往外越淡（是**柔和渐变**而不是一块平色），
    # 到了画布最外沿又基本回到背景（否则四周会出现一道硬边）。
    shadowed = box.panel_image(with_shadow=True)
    if shadowed is None or baseline is None:
        record(False, "面板四周有柔和投影（贴边最浓、往外渐淡、外沿归零）", "抓不到图")
    else:
        def lum(color) -> float:
            return sum(color[:3]) / 3.0

        row = top + box._panel_box[3] // 2          # 面板竖直中线所在的那一行
        bg = lum(baseline.getpixel((px + pad - 2, py + row)))   # 展开前同一点 = 纯背景
        near = lum(shadowed.getpixel((pad - 2, row)))           # 离面板左边 2px
        mid = lum(shadowed.getpixel((pad - max(2, pad // 2), row)))
        far = lum(shadowed.getpixel((1, row)))                  # 画布最外一列
        record(near < bg - 3 and near < mid < far and far >= bg - 3,
               "面板四周有柔和投影（贴边最浓、往外渐淡、外沿归零）",
               f"背景亮度 {bg:.0f} → 贴边 {near:.0f} → 半程 {mid:.0f} → 外沿 {far:.0f}")
        record(final.size == (box._panel_box[2], box._panel_box[3])
               and shadowed.size == (popup.winfo_width(), popup.winfo_height()),
               "panel_image() 默认只裁面板，with_shadow=True 才连投影留白一起给",
               f"面板 {final.size}，含投影 {shadowed.size}")

    # 用户反馈：「选中选项无法显示文字」。以前是「面板 + 文字」当底图、色块直接叠在
    # 它上面，于是当前值 / 悬停项的文字被色块整条盖住。现在画布图元的顺序就是
    # **色块在下、文字在上**（见 ``_open`` 里建图元那里的注释），这里按各种悬停状态
    # 各合成一张最终图，数每一行「只可能是文字」的像素。
    if final is None:
        record(False, "选项文字没有被色块盖住", "抓不到面板内容")
    else:
        def rgb2(hex_color):
            return tuple(int(hex_color[1:][k:k + 2], 16) for k in (0, 2, 4))

        def far(a, b) -> int:
            return max(abs(a[i] - b[i]) for i in range(3))

        def text_pixels(img, index) -> int:
            """只数「只可能是文字」的像素：离面板底 / 色块 / 蓝竖条都足够远。

            深色模式下文字是**亮**的、浅色模式下是**暗**的，所以不能用「暗像素」
            当判据（第一版就是栽在这上面：深色模式数出来只剩抗锯齿的十几像素）。
            """
            y0, y1 = box._bounds[index]
            count = 0
            for y in range(y0, y1):
                for x in range(geom["text_x"], img.size[0] - geom["pad_h"]):
                    color = img.getpixel((x, y))[:3]
                    if min(far(color, rgb2(box._palette["card"])),
                           far(color, rgb2(box._palette["hover"])),
                           far(color, rgb2(box._palette["accent"]))) > 60:
                        count += 1
            return count

        for hover in (-1, 0, 1):
            # 面板可能被**外面的**窗口事件收掉（主窗口一 Configure 就会收，见
            # ``_on_host_configure``；跑测试时窗口被挪动 / DPI 通知都会触发）——
            # 那是环境噪声，不是被测行为，重新打开接着量。
            if box._popup is None:
                box._open()
                root.update()
            box._hover = hover
            box._repaint()
            root.update()
            img = box.panel_image()
            counts = [text_pixels(img, i) for i in range(len(box._values))]
            where = "不悬停" if hover < 0 else f"悬停第 {hover} 项"
            record(all(c > 5 for c in counts),
                   f"选项文字没有被色块盖住（{where}）",
                   f"各选项文字像素 {counts}")
        box._hover = -1
        box._repaint()
        root.update()

    # 回归：面板是**子控件**，主窗口一被缩放 / 挪动，它就会浮在错误的位置上 ——
    # ``_on_host_configure`` 负责收起。这里要点在于**只认主窗口自己的 Configure**：
    # Tk 的 bindtag 里有 toplevel 那一档，子控件的 Configure 也会调到这里来，
    # 不判来源的话面板自己 place 上去那一下就会把它收掉。
    class _Ev:
        pass

    ev = _Ev()
    ev.widget = box
    box._on_host_configure(ev)
    record(box._popup is not None, "子控件自己的 Configure 不会误收面板",
           f"popup={'在' if box._popup is not None else '已经没了'}")
    ev.widget = root
    box._on_host_configure(ev)
    record(box._popup is None, "主窗口一缩放 / 挪动就收起面板", f"popup={box._popup}")
    root.update()

    # 用户反馈：「当下拉菜单的选项下方覆盖着按钮后，点击选项会顺带点击下方按钮」。
    # 真因是**收在了按下的那一刻**：一次点击 = 两个独立事件，中间隔着「用户还按着」的
    # 那段时间。按下就 destroy（哪怕排到 ``after_idle`` —— 事件队列一空它就跑了），
    # 松手那次 release 就落到面板底下的控件上；而 ``CTkButton`` 的 command 恰恰在 release
    # 里触发（customtkinter ``_on_release``）。
    #
    # ⚠️ 这条断言**必须跑够真实时间把 idle 队列清空**，只看「按下那一瞬间」是看不出来的
    # （第一版断言就是这么漏掉它的）。而「OS 按指针位置投递 release」这件事
    # ``event_generate`` 也模拟不出来 —— 真实鼠标那一路在
    # ``tools/check_option_click.py`` 里单跑（合成真鼠标事件）。
    box._open()
    for _ in range(3):
        root.update()
    before = box.get()
    # ⚠️ ``event_generate`` 的坐标是**画布坐标**（= 面板坐标 + ``_shadow_top``），而
    # ``_bounds`` 是面板坐标。少加这个偏移就点到面板顶上那圈投影留白里去了 —— 连第 0 行
    # 都够不着，断言会以「值压根没变」的形式失败。
    hit = dict(x=box._geom["text_x"] + 4,
               y=box._shadow_top
                 + box._bounds[0][0] + box._geom["row_h"] // 2)
    # 泵的这段时间里**把「前台窗口」钉成我们自己**：否则自动化跑的时候主窗口多半没在前台，
    # 「主窗口失去前台就收起」那条（``_on_root_focus_out`` + 60ms 定时器）会先把它收掉，
    # 断言就测不到本来要测的东西了。这里只关心「按下这条路有没有收面板」。
    real_foreground = win32.foreground
    try:
        if win32.available():
            win32.foreground = lambda: win32.top_hwnd(root)
        box._menu_canvas.event_generate("<Button-1>", **hit)
        deadline = time.time() + 0.25      # 真实时间 + 事件循环 → idle 队列一定被清空
        while time.time() < deadline:
            root.update()
            time.sleep(0.01)
    finally:
        win32.foreground = real_foreground
    record(box._popup is not None,
           "按住不放的这段时间里面板一直活着（松手才不会漏给下面的按钮）",
           f"popup={'在' if box._popup is not None else '已经没了'}")
    record(box.get() != before, "点选项立刻生效", f"{before} -> {box.get()}")
    if box._menu_canvas is not None:
        box._menu_canvas.event_generate("<ButtonRelease-1>", **hit)
    root.update()
    record(box._popup is None, "松手之后才收起面板", f"popup={box._popup}")

    # 点主窗口别处也要收起（面板现在跟其它控件是同一个窗口里的兄弟，全靠这条绑定）。
    box._open()
    for _ in range(3):
        root.update()
    root.event_generate("<Button-1>", x=2, y=2)
    root.update()
    record(box._popup is None, "点主窗口别处会收起面板", f"popup={box._popup}")

    # 用户反馈：「切换至其他窗口后下拉菜单依然置顶显示」。
    # 以前弹窗是 overrideredirect 的 -topmost 独立窗口，焦点落到它身上后切走收不到
    # FocusOut，只能轮询前台窗口兜底。现在面板是子控件，**跟主窗口同生共死** ——
    # 主窗口一失去前台，整块面板跟着窗口一起被盖住，不需要任何额外机制。
    # 这里只验「前台判定」这一条：主窗口在前台才算数。
    box._open()
    for _ in range(3):
        root.update()
    if os.name != "nt":
        record(True, "只在主窗口处于前台时才算「没走失」", "非 Windows，跳过")
    else:
        main_h = win32.top_hwnd(root)
        real_foreground = win32.foreground
        verdict = {}
        for label, handle in (("主窗口", main_h), ("别人的", 0xABCDEF), ("没了", None)):
            win32.foreground = (lambda h=handle: h)
            verdict[label] = box._host_is_foreground()
        record(verdict == {"主窗口": True, "别人的": False, "没了": False},
               "只在主窗口处于前台时才算「没走失」", f"{verdict}")
        win32.foreground = real_foreground
    box._close()
    root.update()

    # 字段宽度兜底：传一个比 ``MIN_W`` 还窄的宽度，控件自己也必须长到 ``MIN_W`` ——
    # 否则面板（宽度 = 字段宽度）会窄得放不下选项，或者反过来把字段撑宽、右沿错开。
    narrow = widgets.FluentComboBox(root, variable=tk.StringVar(value="a"),
                                    values=["a", "b"], width=60, height=30,
                                    font=fonts.small, menu_font=fonts.menu,
                                    animate=False)
    narrow.pack(padx=30, pady=(0, 20))
    root.update()
    want = round(widgets.FluentComboBox.MIN_W * narrow._get_widget_scaling())
    record(abs(narrow.winfo_width() - want) <= 1,
           "比 MIN_W 还窄的字段会被撑到 MIN_W（面板与选框才可能同宽）",
           f"传入 60，实际 {narrow.winfo_width()}px（MIN_W={want}px）")
    narrow._open()
    root.update()
    record(narrow.winfo_width() == narrow._panel_box[2],
           "窄字段的面板仍然跟字段同宽", f"字段 {narrow.winfo_width()}px "
           f"面板 {narrow._panel_box[2]}px")
    narrow._close(animate=False)
    root.update()
    _quiet_destroy(root)


def check_animations() -> None:
    """下拉展开 / 收起、投影淡入、开关切换的动画必须**真的在动**，而且一定会停到终点。

    这一节刻意**不关动画**（上面那节为了逐像素判几何把 ``animate=False`` 了），
    按真实时间泵帧采样中间状态。要验六件事：

    1. 展开是「从当前值那一行向两侧长」的 —— 开头只是一条线、不是一步到位；而且**每一行
       在屏幕上的位置逐帧不变**（带子扫过、内容钉死）。少了后半句，只裁底板也能让数字全绿，
       但屏幕上会看到整份列表悬在背景上；
    2. **百来毫秒里真的画得出 ≥10 帧**、帧帧高度不同（帧数靠帧间隔 + 计时器精度抢来），
       并且展开 / 收起 / 投影三段的实测时长都在配置值附近 —— 掉帧不会拖长（按真实时间推进）；
    3. **投影是展开之后的第二段** —— 展开途中它必须是 0，播完才由 0 淡到 1；
    4. 动画期间提着计时器精度、播完（或被掐掉）**必须归还**，不然一直提着白耗电；
    5. 动画会自己走完并落下 ``after``（残留的 after 是关窗噪音的常见来源）；
    6. 收起瞬间 ``_popup`` 就置 None（「松手由面板吃掉」的时序一个字没变），
       但画布还多活一会儿把收起动画播完再销毁；``animate=False`` 时完全同步。
    """
    print("\n[1b] 动画（下拉展开 / 收起、开关切换）")
    import time
    import tkinter as tk

    import customtkinter as ctk

    from mcrender.ui import icons, theme, widgets, win32

    ctk.set_appearance_mode("dark")
    root = ctk.CTk()
    root.geometry("620x460+120+120")
    root.update()
    fonts = theme.Fonts()
    record(isinstance(widgets.system_animations(), bool),
           "读到了系统的「显示动画」总开关（animate=None 时跟随它）",
           f"系统动效 = {widgets.system_animations()}")

    def pump(seconds: float) -> None:
        """按真实时间跑事件循环 —— ``update()`` 只会跑**已到期**的定时器。"""
        end = time.time() + seconds
        while time.time() < end:
            root.update()
            time.sleep(0.008)

    def sample(seconds: float, getter) -> list:
        """按真实时间采样一串状态。"""
        out, end = [], time.time() + seconds
        while time.time() < end:
            root.update()
            out.append(getter())
            time.sleep(0.008)
        return out

    box = widgets.FluentComboBox(root, variable=tk.StringVar(value="a3"),
                                 values=["a1", "a2", "a3", "a4"], width=160, height=32,
                                 font=fonts.small, menu_font=fonts.menu, animate=True)
    box.pack(padx=30, pady=30)
    root.update()

    box._open()
    full = box._box[3]
    # 露出带 = 「已经看得见」的那一段画布，展开 / 收起都是围着它做（见 _reveal_band）。
    # 带子现在是靠**三段拼贴**（带外贴背景、带内贴底图）落到屏幕上的，画布窗口全程不动
    # （见 ``_paint``）—— 所以这里采两样东西：**画布窗口的矩形**和**每一行的屏幕锚点 y**，
    # 两者都必须一帧不变。窗口每帧被 ``place`` 一次，Windows 就会把旧像素整块搬走、
    # 等下一次重画才补齐，屏幕上看就是撕裂 / 上下跳（用户报的那个 bug）。
    def row_screen_ys() -> tuple:
        canvas = box._menu_canvas
        if canvas is None:
            return ()
        rect = (canvas.winfo_rootx(), canvas.winfo_rooty(),
                canvas.winfo_width(), canvas.winfo_height())
        return (rect, ) + tuple(canvas.winfo_rooty() + int(canvas.coords(item)[1])
                                for item in box._rows)

    ease = sample(0.06, lambda: (box._reveal_span(), box._fade_alpha,
                                 box._shadow_alpha, row_screen_ys()))
    spans = [s for s, _a, _m, _y in ease]
    heights = [b - a for a, b in spans]
    alphas = [a for _s, a, _m, _y in ease]
    shadows = [m for _s, _a, m, _y in ease]
    screen_ys = [y for _s, _a, _m, y in ease]
    record(bool(heights) and 0 < min(heights) < full and max(heights) <= full,
           "下拉是「长出来」的（开头明显比整块画布矮）",
           f"画布满高 {full}，开头采样 {heights[:6]}")
    # 本轮改动的核心：**以「当前值」那一行为中心向两侧延展**（Win11 ComboBox 的浮出控件
    # 就是这么开的），而不是从字段下沿往下长。判据三条缺一不可：
    # ① 头一帧的带子塌在那一行的中线上；② 途中上沿只往上走、下沿只往下走（两侧同时长）；
    # ③ 铺满整块画布时正好等于 (0, 满高)——这条在下面 pump 之后单独断言。
    origin = box._reveal_origin
    picked = box._values.index(box.get())
    tops = [a for a, _b in spans]
    bottoms = [b for _a, b in spans]
    center = ((spans[0][0] + spans[0][1]) / 2) if spans else -1
    record(bool(spans) and abs(center - origin) <= 4
           and min(tops) < origin - 10 and max(bottoms) > origin + 10,
           "展开以「当前值」那一行为中心向两侧延展（上沿上移、下沿下移）",
           f"第 {picked} 行中线 y={origin}，首帧带 {spans[0]}（中 {center:.1f}）、"
           f"上沿到 {min(tops)}、下沿到 {max(bottoms)}")
    # 这一节的核心：**画布窗口与内容都钉死，动的只有那三段拼贴**。展开途中窗口每被
    # ``place`` 一次，屏幕上就可能闪一帧「旧像素被整块搬走」的残影 —— 用户报的撕裂 /
    # 上下跳就是这么来的。判据两条：窗口矩形逐帧相同、每一行的屏幕锚点 y 逐帧相同。
    rects = [row[0] for row in screen_ys if row]
    # 每一行各自是一条「逐帧 y」序列 —— 同一行在帧与帧之间必须不变（行与行本来就不同高）
    series = [set(col) for col in zip(*[row[1:] for row in screen_ys])] if screen_ys else []
    record(len(rects) >= 3 and len(set(rects)) == 1
           and series and all(len(v) == 1 for v in series),
           "展开途中画布窗口和每一行的屏幕位置一个像素都没动（不可能撕裂）",
           f"{len(rects)} 帧采样，窗口矩形 {sorted(set(rects))}，"
           f"各行逐帧 y {[sorted(v) for v in series]}")
    # ⚠️ 这里的判据是「**全程实心**」，与早先那条（要求途中有半透明帧）正好相反。
    # 底图淡入 + 文字不淡 = 展开前半程「数字浮在窗口按钮上、面板只有一层薄雾」——
    # 用户报的「不平滑 / 频闪」拍到的就是那一帧（40ms 时面板只有 16% 不透明）。
    # Win11 的浮出控件也是纯裁剪。所以带子怎么裁都行，带内必须已经是整块实心面板。
    record(bool(alphas) and min(alphas) >= 0.999,
           "展开途中面板始终实心（只裁剪、不淡入 —— 半透明会让文字先浮在界面上）",
           f"透明度采样 {[round(a, 2) for a in alphas[:6]]}")
    # 用户要求「在动画播放完毕后在下拉列表四周淡入阴影」—— 所以展开这段里影子必须是 0，
    # 那圈柔光属于紧接着的第二段动画（见 _fade_shadow_in）。
    record(bool(shadows) and max(shadows) == 0.0,
           "展开途中还没有投影（那圈柔光是展开播完才淡入的）",
           f"投影透明度采样 {[round(s, 2) for s in shadows[:6]]}")
    pump(0.5)
    canvas_h = box._popup.winfo_height() if box._popup else None
    record(canvas_h == full and box._reveal_span() == (0, full),
           "展开动画自己走到终点（露出带铺满整块画布）",
           f"画布 {canvas_h} / 满高 {full}，露出带 {box._reveal_span()}")
    record(box._fade_alpha >= 1.0, "展开结束后面板完全不透明", f"alpha={box._fade_alpha:.3f}")
    record(not box._motion.running(), "展开动画跑完不留残余 after", "已停")

    # 展开途中指针划过依然要能高亮 —— 动画只夹画布窗口 + 挪图元偏移，交互一个没动
    box._hover_to(2)
    record(box._menu_canvas is not None
           and box._menu_canvas.itemcget(box._rows[2], "state") == "normal",
           "展开途中悬停高亮照常工作（动画没冻住交互）", "row2 = normal")
    box._hover_to(-1)

    box._close()
    record(box._popup is None and box._closing is not None,
           "收起瞬间 _popup 就归位（松手时序没变），画布留着播完收起动画",
           f"popup={box._popup} closing={'在' if box._closing is not None else '无'}")
    # 收起必须是展开的镜像：上下两条边**一起往里收**（缩回「当前值」那一行）——
    # 旧实现是「顶边钉在字段下沿、只抬底边」，量出来的顶边永远还是 0。
    tail = sample(0.05, box._reveal_span)
    tail_h = [b - a for a, b in tail] if tail else []
    tail_center = ((tail[-1][0] + tail[-1][1]) / 2) if tail else -1
    record(len(tail) >= 2 and tail_h[-1] < tail_h[0]
           and tail[-1][0] > 10 and tail[-1][1] < full - 10,
           "收起是展开的镜像（上下两条边一起往里收，缩回「当前值」那一行）",
           f"{tail[0] if tail else None} → {tail[-1] if tail else None}"
           f"（原点 y={box._reveal_origin}，末帧中线 {tail_center:.1f}）")
    pump(0.35)
    record(box._closing is None, "收起动画播完后画布被销毁", "已销毁")

    # 帧数靠**帧间隔 + 计时器精度**抢来（80ms 里要 10 帧）。这里把帧回调换成记账的壳，
    # 直接数它跑了几次、每次进度换算出的高度差没差 —— 比从外面按时间采样准：采样自己的
    # sleep 粒度和帧间隔是同一个量级，数出来的帧数会随机器抖动。
    #
    # ⚠️ 壳必须**在 _open()/_close() 之前**装好：`_Motion.start` 在启动那一刻就把
    # `on_frame` 抓在手里了，之后再换实例属性是换不掉的。
    from mcrender.ui import shape

    def count_frames(attr, action, seconds):
        """跑一段动画，返回 (每帧 时间戳+进度, 起动后提着的精度份数)。

        ⚠️ 这里**不能用 ``pump()``**：它 sleep 8ms，跟帧间隔是同一个量级，会把动画自己
        的定时器挤晚（实测从 9 帧掉到 8 帧、跨度从 83ms 涨到 91ms）—— 量出来的就不是
        程序真实的样子了。观察者只 sleep 1ms，只占 1/8 的预算。
        """
        seen, real = [], getattr(box, attr)
        setattr(box, attr, lambda p, *a: (seen.append((time.perf_counter(), p)),
                                          real(p, *a))[1])
        try:
            action()
            held = win32.timer_holds()
            end = time.time() + seconds
            while time.time() < end:
                root.update()
                time.sleep(0.001)
        finally:
            setattr(box, attr, real)
        return seen, held

    frames, held = count_frames("_frame_expand", box._open, 0.3)
    record(held >= 1,
           "展开一开始就提了计时器精度（不提的话 after 会被量化到 13ms 以上）",
           f"提着 {held} 份")
    # 等**两段**动画都播完（展开 + 投影淡入）再量精度有没有还 —— 只等展开那一段的话，
    # 投影还在跑，精度本来就该提着，断言会假失败。
    pump(0.45)
    full = box._box[3]                # 展开动画的终点：露出带铺满整块画布
    gaps = sorted((frames[i][0] - frames[i - 1][0]) * 1000 for i in range(1, len(frames)))
    spent = (frames[-1][0] - frames[0][0]) * 1000 if frames else -1
    # 拿控件自己的算式算每帧的带高 —— 和真跑的那份逐字一致（带高恰好是 s × 满高）
    drawn = [b - a for a, b in (box._reveal_band(shape.soft_in_out(p)) for _t, p in frames)]
    record(len(frames) >= 10 and len(set(drawn)) >= 10,
           "百来毫秒里画满 ≥10 帧、且帧帧带高都不同（不是几帧跳到位）",
           f"{len(frames)} 帧 / {len(set(drawn))} 档高度 {drawn[:14]}，满高 {full}")
    record(gaps and gaps[len(gaps) // 2] <= 12,
           "帧间隔中位数 ≤12ms（帧间隔 + 计时器精度都拧到位才做得到）",
           f"中位 {gaps[len(gaps) // 2]:.1f}ms，全部 {[round(g, 1) for g in gaps]}")
    record(box.EXPAND_MS * 0.85 <= spent <= box.EXPAND_MS * 1.6,
           "展开总时长落在配置值附近（含排帧开销，没被掉帧拖长）",
           f"实测 {spent:.0f}ms / 配置 {box.EXPAND_MS}ms")
    record(win32.timer_holds() == 0,
           "展开 + 投影两段都播完，计时器精度还回去了（不然一直提着白耗电）",
           f"提着 {win32.timer_holds()} 份")

    # 第二段：投影淡入。单独数一遍它的帧（同样按真实时间推进，不受掉帧影响）。
    box._close()
    pump(0.3)
    shadow_frames, _held = count_frames("_frame_shadow", box._open, 0.6)
    shadow_spent = ((shadow_frames[-1][0] - shadow_frames[0][0]) * 1000
                    if shadow_frames else -1)
    record(len(shadow_frames) >= 8
           and box.SHADOW_MS * 0.85 <= shadow_spent <= box.SHADOW_MS * 1.8,
           "投影自己淡入（展开之后紧接着的第二段动画）",
           f"{len(shadow_frames)} 帧 / 实测 {shadow_spent:.0f}ms / 配置 {box.SHADOW_MS}ms")
    record(box._shadow_alpha >= 1.0 and not box._motion.running()
           and win32.timer_holds() == 0,
           "投影淡入到全亮、不留残余 after、精度也已归还",
           f"shadow_alpha={box._shadow_alpha:.3f}，提着 {win32.timer_holds()} 份")

    # 两段动画的接缝：**展开落地那张图必须与投影淡入的第一帧逐像素相同**。
    # 投影那段混的是 ``plain → full``（只让四周柔光长出来）；一旦退回去混背景快照
    # ``empty``，第一帧 ``mix`` 就是 1 —— 整块面板（连底带字的底子）瞬间变透明，
    # 屏幕上「菜单先整个闪没、再花 150ms 浮回来」，用户报的「打开频闪」就是它。
    # ⚠️ 这条只能这样验：真机抓屏是按真实时间跑的，正好卡在那一帧的概率很低；
    # 这里把两帧都**手工喂**进去，再逐像素比。
    from PIL import ImageChops

    box._frame_expand(1.0)              # 展开最后一帧：面板铺满、底子已经全实
    landed = box.frame_image()
    box._frame_shadow(0.0)              # 投影段第一帧：柔光还没长出来
    seam = box.frame_image()
    seam_diff = (ImageChops.difference(landed.convert("RGB"), seam.convert("RGB"))
                 if landed is not None and seam is not None else None)
    record(seam_diff is not None and seam_diff.getbbox() is None,
           "展开落地 → 投影淡入的接缝不掉帧（面板不会先闪成背景再淡回来）",
           f"两帧差异 bbox {seam_diff.getbbox() if seam_diff is not None else '拿不到图'}")
    box._settle()                       # 手工喂过帧，恢复成稳定态再往下验

    shrink, _held = count_frames("_frame_collapse", box._close, 0.3)
    shrink_spent = (shrink[-1][0] - shrink[0][0]) * 1000 if shrink else -1
    record(len(shrink) >= 7 and box.COLLAPSE_MS * 0.85 <= shrink_spent
           <= box.COLLAPSE_MS * 1.8,
           "收起同样够帧、并且时长也和配置值对得上",
           f"{len(shrink)} 帧 / 实测 {shrink_spent:.0f}ms / 配置 {box.COLLAPSE_MS}ms")
    record(win32.timer_holds() == 0 and box._closing is None,
           "收起播完不留残余：精度已归还、画布已销毁",
           f"提着 {win32.timer_holds()} 份，closing={box._closing}")

    # 动画被中途掐掉（含关窗销毁那条路）也必须把精度还回去
    box._open()
    root.update()
    box._motion.cancel()
    record(win32.timer_holds() == 0,
           "动画被中途掐掉也归还计时器精度（cancel 那条路）",
           f"提着 {win32.timer_holds()} 份")
    box._close()
    pump(0.25)

    # 动画还在播的时候把控件销毁（关窗 / 切页面）—— 这条最容易漏，单独用一次性控件验，
    # 不能借上面那个 box（它后面还要用）。
    doomed = widgets.FluentComboBox(root, variable=tk.StringVar(value="d1"),
                                    values=["d1", "d2", "d3"], width=160, height=32,
                                    font=fonts.small, menu_font=fonts.menu, animate=True)
    doomed.pack(padx=30, pady=4)
    root.update()
    doomed._open()                      # 动画刚起（精度已提）
    held_before = win32.timer_holds()
    doomed.destroy()                    # 画布、控件一起没
    root.update()
    record(held_before >= 1 and win32.timer_holds() == 0,
           "动画途中销毁控件也归还计时器精度（destroy 那条路）",
           f"销毁前 {held_before} 份 → 销毁后 {win32.timer_holds()} 份")

    # 展开途中立刻重开：不能留下两块野画布
    box._open()
    root.update()
    box._close()
    box._open()
    pump(0.35)
    record(box._popup is not None and box._closing is None,
           "展开 / 收起连点不会留下多余的画布", f"closing={box._closing}")

    # _settle() 要在**动画中途**验才作数：这时露出带还只是一条线 / 一小段。
    box._close()
    pump(0.3)
    box._open()
    root.update()                       # after(0) 那一帧跑掉，露出带处在起点
    half = box._reveal_span()
    box._settle()
    root.update()
    canvas_h = box._popup.winfo_height() if box._popup else None
    record(box._popup is not None and half != (0, box._box[3])
           and box._reveal_span() == (0, box._box[3])
           and canvas_h == box._box[3],
           "_settle() 立刻把展开推到终点（截图 / 像素断言用）",
           f"中途露出带 {half} → {box._reveal_span()}"
           f"，画布高 {canvas_h} / 满高 {box._box[3]}")
    box._close()
    pump(0.3)

    # 露出带以外必须**逐像素**等于背景快照 —— 「面板只在那一段里出现」只能这样验：屏幕抓图
    # 带窗口非客户区的偏移（``PrintWindow`` 从窗口左上角画起），拿来判逐像素会整体错位；
    # ``frame_image()`` 按 ``_paint`` 记下的参数把这一帧重放出来，坐标就是画布坐标。
    box._open()
    box._motion.cancel()
    root.update()
    box._frame_expand(0.35)
    root.update()
    band_top, band_bottom = box._reveal_span()
    frame, empty = box.frame_image(), box._fade["empty"]
    cols = (20, box._box[2] // 2, box._box[2] - 20)
    outside_same, inside_diff = True, False
    if frame is None:
        record(False, "露出带以外逐像素等于背景（只有带子里才是面板）",
               "frame_image() 拿不到合成帧")
    else:
        for y in range(box._box[3]):
            for x in cols:
                same = frame.getpixel((x, y)) == empty.getpixel((x, y))
                if band_top <= y < band_bottom:
                    inside_diff = inside_diff or not same
                elif not same:
                    outside_same = False
        record(outside_same and inside_diff,
               "露出带以外逐像素等于背景（只有带子里才是面板）",
               f"带 {band_top}..{band_bottom}，带外接缝 "
               f"{'干净' if outside_same else '有残留'}，带内有内容={inside_diff}")

    # 叠放顺序也要钉住：**带内那块拼贴必须压在内容图元下面**（它只是面板的底子），
    # 带外两块必须压在内容图元上面（它们负责把文字盖回背景）。压反了的话屏幕上是
    # 「面板长出来了、里面一个字都没有」，而上面那条逐像素断言照样全绿 —— 因为
    # ``frame_image()`` 只是把参数重算一遍，它不知道图元的叠放顺序。这条是补那个盲区。
    tiles = box._fade["tiles"]
    canvas = box._menu_canvas
    # ⚠️ 用 ``find_all()`` 的下标比（越靠后越在上层），别用 ``find_above`` —— 它返回的
    # 不是「所有压在它上面的图元」，语义容易判反（这里正是栽过的那一次）。
    order = {item: i for i, item in enumerate(canvas.find_all())}
    texts = [i for i in canvas.find_all() if canvas.type(i) == "text"]
    mid, outers = order.get(tiles[1], -1), (order.get(tiles[0], -1),
                                            order.get(tiles[2], -1))
    mid_below = bool(texts) and all(order[i] > mid for i in texts)
    outer_above = all(all(order[i] < outer for i in texts) for outer in outers)
    record(mid_below and outer_above,
           "拼贴的叠放对：带内那块在文字下面、带外两块在文字上面",
           f"带内拼贴下标 {mid}（文字最小 {min(order[i] for i in texts) if texts else '-'}）、"
           f"带外两块 {outers}")

    # 展开途中指针也必须点得准。画布窗口现在是**满尺寸摆着不动**的，Tk 给的 ``event.y``
    # 就是画布坐标 —— 用**真实的窗口位置**把每一行的中线换算成 ``event.y``，再让命中判定
    # 还回行号（不自己重算一遍公式）。带子外的行还没露出来，必须点不着（返回 -1）。
    canvas = box._menu_canvas
    canvas_rooty = canvas.winfo_rooty()
    walked, outside = [], []
    for index, (y0, y1) in enumerate(box._bounds):
        rel = box._shadow_top + (y0 + y1) // 2          # 这一行中线（画布坐标）
        screen_y = box._box[1] + rel                    # 客户区坐标
        seen = box._index_at(screen_y)
        if band_top <= rel < band_bottom:
            walked.append((index, seen))
        else:
            outside.append((index, seen))
    record(band_top > 0 and walked and all(i == hit for i, hit in walked)
           and all(hit == -1 for _i, hit in outside),
           "展开途中指针的命中判定跟着带子走（带内的行点得准、带外还没露出来的点不着）",
           f"带 {band_top}..{band_bottom}，带内逐行反推 {walked}，带外 {outside}")
    box._close()
    pump(0.3)

    switch_var = tk.BooleanVar(value=False)
    switch = widgets.FluentSwitch(root, text="只用本地（不联网）",
                                  variable=switch_var, font=fonts.small, animate=True)
    switch.pack(padx=30, pady=10)
    root.update()

    def frames(seconds: float) -> list:
        """采样开关画过的每一帧（**留引用**，否则对象被回收、id 会撞车）。"""
        out, end = [], time.time() + seconds
        while time.time() < end:
            root.update()
            out.append(switch._knob.cget("image"))
            time.sleep(0.008)
        return out

    switch.toggle()
    seen = frames(0.3)
    record(len({id(img) for img in seen}) >= 5,
           "开关是逐帧滑过去的（不是直接换图）",
           f"途中画了 {len({id(img) for img in seen})} 帧")
    record(switch.get() is True and abs(switch._progress - 1.0) < 1e-6
           and not switch._motion.running(),
           "开关动画落到「开」端点并停下", f"progress={switch._progress:.3f}")

    switch.toggle()
    back = frames(0.3)
    record(len({id(img) for img in back}) >= 5 and abs(switch._progress) < 1e-6,
           "关回去同样逐帧 + 落回「关」端点",
           f"途中 {len({id(img) for img in back})} 帧，progress={switch._progress:.3f}")

    # ---- 悬停：滑块（中间那个圆）在 HOVER_MS 里长大一圈（用户给的 Win11 对比图）
    def knob_width(img) -> int:
        """正中间那一行上数「滑块色」的像素宽（浅色开态：白滑块 + 蓝轨道）。"""
        row, px = img.size[1] // 2, img.convert("RGBA").load()
        return sum(1 for x in range(img.size[0]) if min(px[x, row][:3]) > 200)

    flat, grown = icons._switch(True, False, False), icons._switch(True, True, False)
    record(knob_width(grown) - knob_width(flat) >= 2,
           "悬停时滑块明显变大（KNOB → KNOB_HOVER）",
           f"静止 {knob_width(flat)}px → 悬停 {knob_width(grown)}px"
           f"（{icons.KNOB} → {icons.KNOB_HOVER} 逻辑像素）")
    # 动画的终点必须与静态端点**逐像素相同**，否则悬停结束时滑块会「啪」地跳一下
    record(icons._switch(True, True, False, 1.0, 1.0, 1.0).tobytes() == grown.tobytes()
           and icons._switch(True, False, False, 1.0, 1.0, 0.0).tobytes() == flat.tobytes(),
           "悬停动画的终点与静态端点逐像素一致（收起时不跳）")

    switch._hover = True
    switch._animate_hover(1.0)
    grew = frames(0.35)
    record(len({id(img) for img in grew}) >= 5 and abs(switch._hover_s - 1.0) < 1e-6,
           "悬停时滑块是逐帧长大的（不是一步到位）",
           f"途中 {len({id(img) for img in grew})} 帧，hover_s={switch._hover_s:.2f}")
    switch._hover = False
    switch._animate_hover(0.0)
    shrank = frames(0.35)
    record(len({id(img) for img in shrank}) >= 5 and abs(switch._hover_s) < 1e-6,
           "移开指针后逐帧缩回原尺寸",
           f"途中 {len({id(img) for img in shrank})} 帧，hover_s={switch._hover_s:.2f}")

    # 悬停着点一下：滑块要一边长大一边滑过去，两条时间线谁也别打断谁
    switch._hover = True
    switch._animate_hover(1.0)
    switch.toggle()
    together = frames(0.45)
    record(switch.get() is True and abs(switch._progress - 1.0) < 1e-6
           and abs(switch._hover_s - 1.0) < 1e-6
           and not switch._motion.running() and not switch._hover_motion.running(),
           "悬停中切换：长大与滑动两条动画各自跑到终点，互不打断",
           f"途中 {len({id(img) for img in together})} 帧，"
           f"progress={switch._progress:.2f} hover_s={switch._hover_s:.2f}")
    switch._hover = False
    switch._animate_hover(0.0)
    frames(0.3)

    quiet = widgets.FluentSwitch(root, text="不动画", variable=tk.BooleanVar(value=False),
                                 font=fonts.small, animate=False)
    quiet.pack(padx=30, pady=10)
    root.update()
    quiet.toggle()
    root.update()
    record(quiet.get() is True and not quiet._motion.running(),
           "animate=False 的开关直接换图（同步）", "已到端点")

    box_plain = widgets.FluentComboBox(root, variable=tk.StringVar(value="b1"),
                                       values=["b1", "b2"], width=160, height=32,
                                       font=fonts.small, menu_font=fonts.menu,
                                       animate=False)
    box_plain.pack(padx=30, pady=10)
    root.update()
    box_plain._open()
    root.update()
    record(box_plain._popup is not None
           and box_plain._popup.winfo_height() == box_plain._box[3],
           "animate=False 时展开同步完成", f"高 {box_plain._popup.winfo_height()}")
    box_plain._close()
    root.update()
    record(box_plain._popup is None and box_plain._closing is None,
           "animate=False 时收起同步完成", "已销毁")

    # ---- 收起必须从「当前这一帧」接着走 ----------------------------------
    # 展开没播完就关掉（点得快 / 连点字段）时，旧实现第一帧直接喂 `_reveal_band(1.0)`：
    # 面板先跳成**全开**再往回收，屏幕上就是「刚关掉的菜单又整个冒了一下」。
    box._open()
    pump(0.045)
    before_span, before_mix = box._reveal_span(), box._fade["mix"]
    box._close()
    box._frame_collapse(0.0)            # 收起的第一帧（progress=0）
    after_span = box._reveal_span()
    after_mix = box._closing_fade["mix"]
    box._kill_closing()
    pump(0.25)                          # 让画面刷新干净，别把这一步的残留带进下一条
    record((after_span[1] - after_span[0]) <= (before_span[1] - before_span[0]) + 2
           and after_mix >= before_mix - 0.01,
           "收起从当前进度接着走（不会先跳成全开 = 菜单又冒出来）",
           f"关之前带 {before_span} mix {before_mix:.2f} → 第一帧 {after_span} "
           f"mix {after_mix:.2f}")

    # ---- 新面板的背景快照里不能有「刚关掉的菜单」 -------------------------
    # ``PrintWindow`` 给的是窗口**上一次合成**的表面：``destroy()`` 之后立刻抓，上一块
    # 菜单原样还在图上（实测只有跑一轮事件循环才刷得新；``update_idletasks`` /
    # ``RedrawWindow`` / ``UpdateWindow`` / ``DwmFlush`` 全都不行）。这张快照就是露出带
    # 以外看到的东西 —— 被拍进去的话，刚关掉的菜单会在下一块的动画里**原样重播**
    # （用户报的「刚关闭的菜单一部分又显示出来」）。
    #
    # ⚠️ 所以**不能**拿「现抓一张干净基准」来逐像素比。实测同一个窗口前后两次
    # ``PrintWindow`` 都不保证是同一帧：后一次会返回**更旧**状态的表面（整排后加的
    # 控件凭空不见，差异只落在字段描边那 3 列上，看着像「差 1~2px 的一条细线」）。
    # 能逐像素比的只有**我们自己存下来的那张图** —— 旧面板打开时抓的 ``empty`` 就是
    # 「它底下原本的样子」，补丁贴回去之后重叠区必须和它分毫不差。
    from PIL import ImageChops

    other = widgets.FluentComboBox(root, variable=tk.StringVar(value="c1"),
                                   values=["c1", "c2"], width=160, height=32,
                                   font=fonts.small, menu_font=fonts.menu, animate=True)
    other.place(x=40, y=120)            # 与 box 的矩形重叠，才拍得到
    root.update()
    box._open()
    pump(0.30)
    box_empty = box._fade["empty"].convert("RGB")   # 旧面板底下原本的样子（唯一真值）
    box._close(animate=False)                       # 先把场上清空，抓一张干净基准
    pump(0.10)
    clean = win32.grab(root).convert("RGB")
    box._open()
    pump(0.30)
    other._open()                       # 真机点击顺序：旧面板还开着就开新的
    pump(0.35)
    record(box._popup is None and box._closing is None,
           "开新面板时旧面板立刻收掉（不留在快照里）",
           f"旧面板 popup={box._popup} closing={box._closing}")

    bx, by, bw, bh = box._box
    ox, oy, ow, oh = other._box
    x0, y0 = max(bx, ox), max(by, oy)
    x1, y1 = min(bx + bw, ox + ow), min(by + bh, oy + oh)
    record(x1 > x0 and y1 > y0,
           "新旧两块面板的矩形确实有重叠（没有的话这条验证不到）",
           f"重叠区 {(x0, y0, x1, y1)}")
    if x1 > x0 and y1 > y0:
        got = other._fade["empty"].convert("RGB").crop(
            (x0 - ox, y0 - oy, x1 - ox, y1 - oy))
        want = box_empty.crop((x0 - bx, y0 - by, x1 - bx, y1 - by))
        diff = ImageChops.difference(got, want)
        record(diff.getbbox() is None,
               "背景快照里补回了旧面板底下原本的样子（旧菜单没留在里面）",
               f"重叠区 {x1 - x0}×{y1 - y0}，与旧面板的干净背景差 {diff.getbbox()}")

    # 屏幕级：两块都关掉、跑一轮事件循环，画面必须回到「谁都没开」的样子 ——
    # 用户看到的就是这一张图（关掉的面板留在屏幕上 = 残影）。
    other._close(animate=False)
    pump(0.20)
    after = win32.grab(root).convert("RGB")
    diff = ImageChops.difference(after, clean)
    record(diff.getbbox() is None,
           "都收起来之后屏幕上没有残影（关掉的面板不会留在画面上）",
           f"与关之前的画面差 {diff.getbbox()}")

    _quiet_destroy(root)


def _check_fluent_controls(win) -> None:
    """按钮 / 输入框的底板必须换成 Pillow 画的那一层。

    换成 Pillow 的理由是圆角质量：CTk 的圆角用字形拼，半径小的时候一圈只有 5~8 级
    过渡色，放大看是硬阶梯。这里断言「没有漏网的原生 CTkButton / CTkEntry」+
    「每个都铺了底板」+「底板圆角确实有一堆过渡色」+「悬停会换底色」。
    """
    import customtkinter as ctk
    from mcrender.ui import widgets

    tree = list(walk(win.root))
    bare = sorted({type(w).__name__ for w in tree
                   if type(w) in (ctk.CTkButton, ctk.CTkEntry)})
    record(not bare, "按钮 / 输入框没有漏网的 CTk 原生实现",
           f"残留 {bare}" if bare else "全部走 FluentButton / FluentEntry")

    buttons = [w for w in tree if isinstance(w, widgets.FluentButton)]
    entries = [w for w in tree if isinstance(w, widgets.FluentEntry)]
    # 13 = 6 个次级按钮（选择…×4 + 打开输出目录 + 删除缓存之类）+ 主按钮 + 外观 + 检查更新
    record(len(buttons) == 13 and len(entries) == 4, "识别到按钮 / 输入框",
           f"{len(buttons)} 个按钮、{len(entries)} 个输入框")

    total = len(buttons) + len(entries)
    painted = [w for w in buttons + entries if w._surface_item is not None]
    record(len(painted) == total, "每个都铺上了 Pillow 底板", f"{len(painted)}/{total}")

    probe = next((w for w in buttons if w._surface_img is not None), None)
    if probe is None:
        record(False, "按钮底板的圆角是抗锯齿的", "拿不到底板图")
    else:
        n = 12
        # 不用 ``getdata()``：Pillow 14 起弃用（会刷 DeprecationWarning）；``getcolors``
        # 没这个包袱，也和本文件其它「数角上过渡色」的检查一致。
        colors = len(probe._surface_img.crop((0, 0, n, n)).convert("RGB")
                     .getcolors(maxcolors=1 << 20) or [])
        record(colors > 8, "按钮底板的圆角是抗锯齿的",
               f"{n}×{n} 角上有 {colors} 种颜色（原生 CTk 只有 5~8 种）")

    probe = next((w for w in buttons if w.cget("hover_color") is not None), None)
    if probe is None:
        record(False, "悬停时底板换了底色", "找不到带 hover_color 的按钮")
    else:
        before = probe._surface_key
        probe._on_enter()
        win.root.update_idletasks()
        after = probe._surface_key
        probe._on_leave()
        win.root.update_idletasks()
        record(before[2] != after[2], "悬停时底板换了底色",
               f"{before[2]} -> {after[2]}")
        record(probe._surface_key[2] == before[2], "移开后底板颜色还原",
               f"{probe._surface_key[2]}")


def check_main_window() -> None:
    """真实主窗口：控件齐、下拉能开、开关能切、外观能换、版本号显示对。"""
    print("\n[2] 主窗口")
    import customtkinter as ctk

    from mcrender import __version__, app
    from mcrender.ui import icons, theme, widgets
    from mcrender.ui.window import TITLE, MainWindow

    cfg_before = app.load_config()          # 外观会被写进 config，测完要还原
    win = MainWindow([])
    win.root.update()
    boxes = [w for w in walk(win.root) if isinstance(w, widgets.FluentComboBox)]
    switches = [w for w in walk(win.root) if isinstance(w, widgets.FluentSwitch)]
    record(len(boxes) == 4, "识别到 4 个下拉", f"实际 {len(boxes)}")
    record(len(switches) == 1, "识别到 1 个开关", f"实际 {len(switches)}")

    # ---- 版本号：标题栏里是「vX.Y.Z」，标题旁边还有一块 WinUI 风格的色块
    # 单一来源 = ``mcrender.__version__``；这里不写死 "1.1.0"，改版本号不用改测试。
    record(win.root.title() == f"{TITLE} v{__version__}",
           "标题栏按 v<版本号> 显示当前版本", f"{win.root.title()!r}")
    badge = getattr(win, "version_badge", None)
    if badge is None:
        record(False, "标题旁有版本色块", "窗口上没有 version_badge")
    else:
        record(badge._text.cget("text") == f"v{__version__}",
               "色块里的文字就是当前版本号",
               f"{badge._text.cget('text')!r}（__version__={__version__}）")
        img = badge._badge_img
        scale = badge._get_widget_scaling()
        record(img is not None
               and abs(img.size[1] - round(badge.HEIGHT * scale)) <= 1,
               "色块高度按逻辑像素跟着 DPI 缩放",
               f"{img.size if img is not None else None}"
               f" ≈ {badge.HEIGHT}×{scale:.2f}")
        if img is not None:
            dark = ctk.get_appearance_mode() == "Dark"
            # 别取正中：那儿是「v1.1.0」那行白字。取左内边距那一列的腰部 —— 空的填充色。
            center = img.convert("RGB").getpixel(
                (max(1, round(3 * scale)), img.height // 2))
            corner = img.convert("RGB").getpixel((0, 0))
            accent = theme.color("accent", dark)
            window = theme.color("window", dark)
            record(center == icons._rgb(accent) and corner == icons._rgb(window),
                   "色块是「强调色填充 + 圆角外露窗口底色」（不是方块）",
                   f"中心 {center} vs 强调色 {accent}；左上角 {corner} vs 窗口 {window}")
            n = 10
            colors = len(img.crop((0, 0, n, n)).convert("RGB")
                         .getcolors(maxcolors=1 << 20) or [])
            record(colors > 8, "色块的圆角是抗锯齿的",
                   f"{n}×{n} 角上有 {colors} 种颜色")
        label = getattr(win, "title_label", None)
        if label is not None:
            record(badge.winfo_rootx() >= label.winfo_rootx() + label.winfo_width()
                   and badge.winfo_rooty() < label.winfo_rooty() + label.winfo_height()
                   and badge.winfo_rooty() + badge.winfo_height() > label.winfo_rooty(),
                   "色块挂在标题右侧、与标题同一行",
                   f"标题 x={label.winfo_rootx()}..{label.winfo_rootx() + label.winfo_width()}"
                   f"，色块 x={badge.winfo_rootx()}")

    opened, fits = [], []
    for index, box in enumerate(boxes):
        box._open()
        win.root.update()
        ok = box._popup is not None and box._geom is not None
        opened.append(f"{box.get()}:{'开' if ok else '失败'}")
        if ok:
            px = box._popup.winfo_rootx() - win.root.winfo_rootx()
            py = box._popup.winfo_rooty() - win.root.winfo_rooty()
            fits.append(px >= 0 and py >= 0
                        and px + box._popup.winfo_width() <= win.root.winfo_width()
                        and py + box._popup.winfo_height() <= win.root.winfo_height())
        box._close()
        win.root.update()
    record(all("失败" not in text for text in opened), "每个下拉都能展开/收起",
           " ".join(opened))
    # 面板是主窗口里的子控件 —— 越出客户区的那部分会被窗口**裁掉**（不像以前是独立
    # 窗口，能盖到窗口外面去），所以 4 个面板都必须整个落在窗口里。
    record(len(fits) == len(boxes) and all(fits),
           "每个下拉面板都完整落在主窗口里（子控件超出去会被裁掉）",
           f"{sum(fits)}/{len(boxes)}")

    if switches:
        before = switches[0].get()
        switches[0].toggle()
        win.root.update()
        record(switches[0].get() != before, "开关能切换",
               f"{before} -> {switches[0].get()}")
        try:
            image = switches[0]._knob.cget("image")
            box = tuple(image.cget("size"))
            base = image.cget("light_image").size
            record(box == (icons.TRACK_W, icons.TRACK_H)
                   and base == (icons.TRACK_W, icons.TRACK_H),
                   "开关底图与轨道常量一致",
                   f"CTkImage {box} / 底图 {base}"
                   f"（轨道 {icons.TRACK_W}×{icons.TRACK_H}、滑块 {icons.KNOB}）")
        except Exception as exc:
            record(False, "开关底图与轨道常量一致", f"取不到：{exc}")

    _check_fluent_controls(win)

    # ---- 检查更新：按钮在深浅色按钮左边，点下去是「后台比版本 + 系统通知」
    # 网络和通知都换成假的：不管机器连不连得上 GitHub、弹不弹得出 toast，这条都能跑。
    from mcrender.ui import window as window_mod

    button = getattr(win, "update_button", None)
    appearance = win.appearance_button
    if button is None:
        record(False, "标题区多了一个「检查更新」按钮", "没有 update_button")
    else:
        record(button.cget("text") == "检查更新"
               and button.winfo_rooty() < appearance.winfo_rooty() + appearance.winfo_height()
               and button.winfo_rootx() + button.winfo_width() <= appearance.winfo_rootx(),
               "「检查更新」挂在深浅色按钮左侧、同一行",
               f"检查更新 x={button.winfo_rootx()}.."
               f"{button.winfo_rootx() + button.winfo_width()}"
               f"，外观 x={appearance.winfo_rootx()}")

        toasts: list = []
        real_check, real_toast = window_mod.updater.check, window_mod.notify.toast
        window_mod.notify.toast = lambda *a, **kw: toasts.append((a, kw)) or True

        def pump(seconds: float = 0.6) -> None:
            end = time.time() + seconds
            while time.time() < end:
                win.root.update()
                time.sleep(0.01)

        try:
            cases = [
                ({"tag": "v9.9.9", "newer": True}, "新版本可用", "最新发布版：v9.9.9",
                 "前往下载", "发现新版本 v9.9.9"),
                ({"tag": "v1.1.0", "newer": False}, "检查更新完成", "已是最新版本",
                 None, "已是最新版本"),
            ]
            for result, want_title, want_body, want_button, want_hint in cases:
                toasts.clear()
                window_mod.updater.check = lambda current, **kw: dict(result)
                button.invoke()
                busy = button.cget("text")
                pump()
                got_title, got_body = toasts[-1][0] if toasts else ("", "")
                got_kw = toasts[-1][1] if toasts else {}
                hint = win.update_hint.cget("text")
                record(got_title == want_title and got_body == want_body
                       and got_kw.get("button") == want_button
                       and (want_button is None
                            or got_kw.get("url") == window_mod.updater.RELEASE_URL)
                       and button.cget("text") == "检查更新"
                       and hint == want_hint
                       and not win._update_busy,
                       f"「{want_title}」那条通知内容对、按钮也还原了",
                       f"点下去是「{busy}」，通知 ({got_title!r}, {got_body!r})"
                       f" 按钮={got_kw.get('button')!r}，标题区小字 {hint!r}")

            toasts.clear()
            def boom(current, **kw):
                raise window_mod.updater.UpdateError("网络不通：timed out")
            window_mod.updater.check = boom
            button.invoke()
            pump()
            got_title, got_body = toasts[-1][0] if toasts else ("", "")
            hint = win.update_hint.cget("text")
            record(got_title == "检查更新失败" and "网络不通" in got_body
                   and button.cget("text") == "检查更新" and not win._update_busy
                   and hint.startswith("检查失败"),
                   "检查更新失败也会通知（按钮不会卡在「检查中…」）",
                   f"通知 ({got_title!r}, {got_body!r})，按钮 {button.cget('text')!r}，"
                   f"小字 {hint!r}")

            # 长原因（限流那种）必须变省略号、且标签**不能被挤窄** —— 挤窄了
            # anchor="e" 会把左边那截推到徽标底下，看着像「从半个字开始」。
            label = win.update_hint
            win._hint("检查失败：GitHub API 限流（未登录 60 次/小时），00:39 后恢复", "error")
            win.root.update()
            long_text = label.cget("text")
            record(long_text.endswith("…") and label.winfo_reqwidth() <= label.winfo_width(),
                   "标题区小字太长会变省略号（不会被挤窄成半截字）",
                   f"{long_text!r}，需要 {label.winfo_reqwidth()}px / 实得 "
                   f"{label.winfo_width()}px")
        finally:
            window_mod.updater.check, window_mod.notify.toast = real_check, real_toast

    modes = [win.cfg.get("appearance")]
    for _ in range(len(theme.APPEARANCE_MODES)):
        win.cycle_appearance()
        win.root.update()
        modes.append(win.cfg.get("appearance"))
    # 循环一圈应回到起点，且中途不重复（说明三个模式都真的切到了）
    record(modes[0] == modes[-1] and len(set(modes[:-1])) == len(modes) - 1,
           "外观循环切换一轮回到起点", " -> ".join(str(m) for m in modes))

    win.close()
    record(win._closing and win._log_job is None, "close() 清掉了日志轮询")
    app.save_config(cfg_before)             # 还原外观等配置


def _quiet_destroy(root) -> None:
    """和 MainWindow.close() 相同的清尾：destroy 前取消所有待触发的 after。"""
    try:
        for job in root.tk.call("after", "info") or ():
            root.tk.call("after", "cancel", job)
    except Exception:
        pass
    try:
        root.destroy()
    except Exception:
        pass


CLOSE_PROBE = r'''
import sys
sys.path.insert(0, r"{root}")
from mcrender.ui.window import MainWindow
win = MainWindow([])
win.root.update()
win.write("关窗前的日志")
win.root.update()
win.close()
for _ in range(8):
    win.root.update()
print("CLOSE_OK")
'''


def check_close_is_quiet() -> None:
    """关窗后 stderr 必须干净（invalid command name 只会在 stderr 出现，拦不到）。"""
    print("\n[3] 关窗安静（子进程抓 stderr）")
    done = subprocess.run([sys.executable, "-c", CLOSE_PROBE.format(root=ROOT)],
                          capture_output=True, text=True, timeout=180)
    noise = done.stderr.strip()
    record("CLOSE_OK" in done.stdout, "关窗后仍能空转 update()")
    record(not noise, "关窗后 stderr 无噪音",
           "干净" if not noise else noise.splitlines()[0])


# ------------------------------------------------------------------ 入口
def main() -> int:
    ensure_gui_python()
    print(f"解释器：{sys.executable}")
    check_geometry()
    check_animations()
    check_main_window()
    check_close_is_quiet()

    failed = [name for ok, name, _ in RESULT if not ok]
    print(f"\n{'=' * 52}\n共 {len(RESULT)} 项，通过 {len(RESULT) - len(failed)}，失败 {len(failed)}")
    if failed:
        for name in failed:
            print(f"  ✗ {name}")
        return 1
    print("全部通过 ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())
