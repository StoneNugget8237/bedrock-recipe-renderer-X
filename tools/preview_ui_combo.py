"""预览下拉列表展开后的样子，并核对命中判定。

下拉列表在代码里是「一块 Canvas + 一堆多边形」，光看代码看不出圆角对不对、
悬停底色和选中底色分不分得开。而开发机不一定装了 tkinter（装了也未必想开
窗口），所以这个脚本用 Pillow 按**完全相同的坐标**把面板重画一遍。

    python tools/preview_ui_combo.py [输出路径] [--scale 1.75]

``--scale`` 就是系统 DPI 缩放（CustomTkinter 的 ``_get_widget_scaling()``）：
传 1.75 出来的图，几何和字号与 175% 屏幕上看到的**物理像素**一致。

几何常量不写死，直接从 ``mcrender/ui/widgets.py`` 的 ``FluentComboBox`` 里
解析出来，保证图和实际代码是同一套数字。脚本会做三件事：

1. 渲染「无悬停 / 悬停普通项 / 悬停另一项（当前值同时高亮）」三种状态，含四周那圈投影；
2. 量 ``menu_hover`` / ``menu_selected`` 相对面板底 ``layer`` 的实际色差，太小就报警，
   并确认两档用的是同一个色值（加深程度一致，靠蓝竖条区分谁被选中）；
3. 用 ``shape.row_index`` 把每行的中心、缝隙、边界、上下留白都断言一遍。
"""
from __future__ import annotations

import argparse
import ast
import importlib.util
import os
import re
import sys
import tempfile

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UI = os.path.join(ROOT, "mcrender", "ui")

SS = 4                  # 超采样倍数
GAP = 46                # 面板间距（要放得下两侧各一圈投影，见 draw_panel）
MARGIN = 26             # 边距（同理：底下那圈投影也得有地方铺）
FIELD_H = 30            # 字段高度（和 window.py 传的一致）
LABEL_H = 40            # 上方留白：主题名 + 状态名各占一行

VALUES = ["x1", "x2", "x4", "x8"]
CURRENT = "x2"

# **长度类**常量才跟着 DPI 缩放。别把 ``WATCH_MS`` / ``WATCH_MISSES`` 这类时间/计数
# 常量也乘进去 —— 它们和屏幕缩放无关，乘出来的数字（如 120→210）纯属误导。
GEOMETRIC = ("ROW_H", "ITEM_GAP", "PAD_V", "PAD_H", "RADIUS", "ITEM_RADIUS",
             "PILL_W", "PILL_H", "PILL_INSET", "GAP", "TEXT_X", "MENU_TEXT_GAP",
             "ARROW_PAD", "FIELD_RADIUS", "FIELD_BORDER", "FIELD_PAD_V",
             "SHADOW_SPREAD")


# ------------------------------------------------------------------ 读数
def parse_assignments(path, wanted):
    """从 py 源码里取出指定的模块级赋值（字面量）。"""
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    out = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                name = getattr(target, "id", "")
                if name in wanted:
                    out[name] = ast.literal_eval(node.value)
    return out


def class_constants(path, class_name):
    """取某个类的类级字面量常量，保证预览用的是代码里的真值。"""
    with open(path, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            out = {}
            for stmt in node.body:
                if isinstance(stmt, ast.Assign):
                    for target in stmt.targets:
                        name = getattr(target, "id", "")
                        if name:
                            try:
                                out[name] = ast.literal_eval(stmt.value)
                            except Exception:
                                pass
            return out
    raise SystemExit(f"没在 {path} 里找到类 {class_name}")


def load_shape():
    spec = importlib.util.spec_from_file_location("_shape", os.path.join(UI, "shape.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_font(size):
    for name in ("msyh.ttc", "msyhl.ttc", "simhei.ttf", "segoeui.ttf", "arial.ttf"):
        path = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts", name)
        if os.path.isfile(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


# ------------------------------------------------------------------ 绘图
class Painter:
    def __init__(self, size):
        self.img = Image.new("RGB", (size[0] * SS, size[1] * SS))
        self.draw = ImageDraw.Draw(self.img)

    def poly(self, points, fill, outline=None, width=1):
        pts = [(points[i] * SS, points[i + 1] * SS) for i in range(0, len(points), 2)]
        self.draw.polygon(pts, fill=fill, outline=outline,
                          width=max(1, round(width * SS)))

    def rect(self, x0, y0, x1, y1, fill, outline=None, width=1):
        self.draw.rectangle([x0 * SS, y0 * SS, x1 * SS, y1 * SS], fill=fill,
                            outline=outline, width=max(1, round(width * SS)))

    def text(self, x, y, s, font, fill, size):
        self.draw.text((x * SS, y * SS), s, font=font, fill=fill)

    def shadow(self, x, y, w, h, radius, spread, opacity, blur=2.0):
        """在 (x, y, w, h) 这块矩形四周铺一圈柔和投影。

        算式与 ``icons.soft_shadow`` 完全一样（实心圆角矩形 → 高斯模糊 → 按 opacity 变黑），
        只是这里画在预览自己的超采样画布上：那里是 Pillow 的实参，这边要跟着 SS 放大。
        真机上那圈光是**分两段**来的（展开播完才淡入，见 ``FluentComboBox._fade_shadow_in``），
        本预览画的是**稳定态**，所以一次铺满。

        真机四边留白一样宽、**没有「上沿只能探到某条线」那种裁剪** —— 面板是盖在字段上的
        （``_shadow_top == _shadow_pad``，见 ``FluentComboBox._anchor``），所以本预览也照
        四边对称画。（早先真机挂在字段下方时画布顶边只敢探到字段底沿，为此这里有过一个
        ``clip_top`` 参数，随定位改掉已删除。）
        """
        mask = Image.new("L", self.img.size, 0)
        ImageDraw.Draw(mask).rounded_rectangle(
            [(x - spread) * SS, (y - spread) * SS,
             (x + w - 1 + spread) * SS, (y + h - 1 + spread) * SS],
            radius=radius * SS, fill=255)
        mask = mask.filter(ImageFilter.GaussianBlur(spread * SS / blur))
        mask = mask.point(lambda v: round(v * opacity))
        # ``Image.composite`` 就是逐像素的 ``image1*mask + image2*(1-mask)`` ——
        # 正是「拿黑色按 alpha 压一层下去」，不需要自己拆通道。
        self.img = Image.composite(Image.new("RGB", self.img.size, (0, 0, 0)),
                                   self.img, mask)
        self.draw = ImageDraw.Draw(self.img)

    def result(self, size):
        return self.img.resize(size, Image.LANCZOS)


def draw_field(painter, x, y, width, colors, dark, value, font, field_h):
    """下拉框闭合时的样子（值 + 右侧箭头）。"""
    control = colors["control"][1 if dark else 0]
    stroke = colors["control_stroke"][1 if dark else 0]
    text = colors["text"][1 if dark else 0]
    faint = colors["text_muted"][1 if dark else 0]
    s = field_h / 30.0                      # 相对基准高度（30 逻辑像素）的比
    # 同样按「外描边 + 内填充」画：字段底板在界面上也是 Pillow 的圆角图
    border = max(1, round(s))
    painter.poly(shape_round(x, y, x + width, y + field_h, round(4 * s)),
                 fill=stroke)
    painter.poly(shape_round(x + border, y + border,
                             x + width - border, y + field_h - border,
                             max(0, round(4 * s) - border)),
                 fill=control)
    painter.text(x + 9 * s, y + (field_h - 14 * s) / 2, value, font, text, 14)
    cx, cy = x + width - 17 * s, y + field_h / 2
    painter.draw.line([(cx - 4 * s) * SS, (cy - 2 * s) * SS,
                       cx * SS, (cy + 2 * s) * SS], fill=faint, width=2 * SS)
    painter.draw.line([cx * SS, (cy + 2 * s) * SS,
                       (cx + 4 * s) * SS, (cy - 2 * s) * SS],
                      fill=faint, width=2 * SS)


def draw_panel(painter, x, y, width, colors, dark, hover, font, geom,
               shadow_opacity=0.22):
    """下拉列表展开后的样子。坐标与 widgets.FluentComboBox 完全一致。

    这里的 ``shadow_opacity`` 只是兜底默认值，真正用的那个是从 ``widgets.py`` 里解析出来的
    ``SHADOW_OPACITY``（见 ``class_constants``）。
    """
    row_h = geom["ROW_H"]
    item_gap = geom["ITEM_GAP"]
    pad_v = geom["PAD_V"]
    pad_h = geom["PAD_H"]
    radius = geom["RADIUS"]
    item_r = geom["ITEM_RADIUS"]
    pill_w = geom["PILL_W"]
    pill_h = geom["PILL_H"]
    pill_x = geom["PILL_INSET"]
    text_x = geom["TEXT_X"]

    card = colors["layer"][1 if dark else 0]
    stroke = colors["layer_stroke"][1 if dark else 0]
    text = colors["text"][1 if dark else 0]
    accent = colors["accent"][1 if dark else 0]
    hover_c = colors["menu_hover"][1 if dark else 0]
    selected = colors["menu_selected"][1 if dark else 0]

    # 行位置和总高都找 shape 要 —— 和 FluentComboBox 跑的是同一个函数，
    # 这样预览图不可能跟实际面板错位
    count = len(VALUES)
    bounds = _shape.row_bounds(count, pad_v, row_h, item_gap)
    height = _shape.list_height(count, pad_v, row_h, item_gap)
    # 面板底 = 描边色的圆角矩形，再盖上内缩一圈的填充色。
    # 界面上这块是 Pillow 画的图（icons.rounded_rect），而 Pillow 的描边画在
    # 边界**以内**，所以这里也用「外描边 + 内填充」两条来复现；
    # 早先用 create_polygon 的 outline（沿路径居中画）会在底边被裁掉半条，
    # 就是「下拉面板下端无框线」的来源。
    border = max(1, int(geom.get("FIELD_BORDER", 1)))
    # 投影（用户要求「下拉列表四周淡入阴影」）—— 必须画在面板**之前**，压在它底下。
    # 四边等宽、不裁上沿：真机的画布四边留白一样宽（``_shadow_top == _shadow_pad``），
    # 因为面板是**盖在字段上**的（选中那一行的中线对齐字段中线，见 ``_anchor``），
    # 上沿那圈阴影落在字段身上本来就该看得见。
    spread = int(geom.get("SHADOW_SPREAD", 0))
    if spread:
        painter.shadow(x, y, width, height, radius, spread, shadow_opacity)
    painter.poly(shape_round(x, y, x + width - 1, y + height - 1, radius),
                 fill=stroke)
    painter.poly(shape_round(x + border, y + border,
                             x + width - 1 - border, y + height - 1 - border,
                             max(0, radius - border)),
                 fill=card)

    for index, ((y0, y1), value) in enumerate(zip(bounds, VALUES)):
        cy = (y0 + y1) / 2
        picked = value == CURRENT
        if picked:
            fill = selected
        elif index == hover:
            fill = hover_c
        else:
            fill = None                     # 普通项不描不填，直接露出面板底
        if fill is not None:
            painter.poly(shape_round(x + pad_h, y + y0,
                                     x + width - 1 - pad_h, y + y1, item_r),
                         fill=fill)
        if picked:
            px = x + pad_h + pill_x
            painter.poly(shape_round(px, y + cy - pill_h / 2,
                                     px + pill_w, y + cy + pill_h / 2,
                                     pill_w / 2),
                         fill=accent)
        painter.text(x + text_x, y + cy - row_h * 8 / 32, value, font, text, 14)
    return height


_shape = None


def shape_round(x0, y0, x1, y1, radius):
    return _shape.round_rect_points(x0, y0, x1, y1, radius)


# ------------------------------------------------------------------ 自检
def check(shape, colors, geom):
    """断言命中判定与色差 —— 这两个是这次问题的根源。"""
    row_h, gap, pad_v = geom["ROW_H"], geom["ITEM_GAP"], geom["PAD_V"]
    pitch = shape.row_pitch(row_h, gap)
    count = len(VALUES)
    height = shape.list_height(count, pad_v, row_h, gap)

    print("=== 行距（shape.row_bounds / list_height）===")
    bounds = shape.row_bounds(count, pad_v, row_h, gap)
    ok = True
    for index, (y0, y1) in enumerate(bounds):
        print(f"  第 {index} 行  y {y0}..{y1}  高 {y1 - y0}")
    gaps = [bounds[i + 1][0] - bounds[i][1] for i in range(len(bounds) - 1)]
    gap_ok = all(g == gap for g in gaps)
    ok = ok and gap_ok
    print(f"  行间距 {gaps}（期望全是 {gap}）  {'✓' if gap_ok else '✗'}")
    print(f"  面板总高 {height} = 2*{pad_v} + {count}*{row_h} + {count - 1}*{gap}")

    print("\n命中判定（shape.row_index）：")
    for name, y, want in (
            ("面板顶部留白", 0, -1),
            ("留白下沿", pad_v - 1, -1),
            ("第 0 行上沿", pad_v, 0),
            ("第 0 行中心", pad_v + row_h // 2, 0),
            ("行缝隙里（归上一行）", pad_v + row_h + 1, 0),
            ("第 1 行中心", pad_v + pitch + row_h // 2, 1),
            ("第 2 行中心", pad_v + 2 * pitch + row_h // 2, 2),
            ("末行下沿", pad_v + (count - 1) * pitch + row_h - 1, count - 1),
            ("面板最底一像素", height - 1, count - 1),
            ("面板外（下方）", height + 5, -1)):
        got = shape.row_index(y, pad_v, pitch, count)
        flag = "✓" if got == want else "✗"
        ok = ok and got == want
        print(f"  {flag} {name:<18} y={y:<4} -> {got}  (期望 {want})")

    print("\n底色色差（悬停 / 当前值 相对面板底，至少差 8 才算肉眼可分；两者应同深浅）：")
    for label, dark in (("浅色", False), ("深色", True)):
        def rgb(token):
            v = colors[token][1 if dark else 0].lstrip("#")
            return tuple(int(v[i:i + 2], 16) for i in (0, 2, 4))

        pairs = (("面板-悬停", rgb("layer"), rgb("menu_hover")),
                 ("面板-当前值", rgb("layer"), rgb("menu_selected")))
        deltas = [(name, max(abs(a - b) for a, b in zip(p, q)))
                  for name, p, q in pairs]
        worst = min(d for _n, d in deltas)
        same = rgb("menu_hover") == rgb("menu_selected")
        verdict = "可分 ✓" if worst >= 8 else "太接近，分不清 ✗"
        tier = "同深浅 ✓" if same else "两档深浅不一致 ✗"
        ok = ok and worst >= 8 and same
        detail = "  ".join(f"Δ({name})={d}" for name, d in deltas)
        print(f"  {label}  {detail}  {verdict}  {tier}")
    print("  （悬停与当前值取同一个色值：加深程度一致，「谁是当前值」由蓝竖条说明）")

    print("\n色令牌引用（ui/*.py 里 theme.color(\"x\") 与 c(\"x\")）：")
    pattern = re.compile(r'(?:color|c)\(\s*"([a-z_]+)"')
    missing = {}
    for name in sorted(os.listdir(UI)):
        if not name.endswith(".py"):
            continue
        with open(os.path.join(UI, name), encoding="utf-8") as fh:
            used = set(pattern.findall(fh.read()))
        for token in sorted(used - set(colors)):
            missing.setdefault(token, []).append(name)
    if missing:
        ok = False
        for token, files in missing.items():
            print(f"  ✗ {token} 未定义（用于 {', '.join(files)}）")
    else:
        print("  全部存在 ✓")
    return ok


def main(argv) -> int:
    global _shape
    parser = argparse.ArgumentParser(description="预览下拉面板")
    parser.add_argument("out", nargs="?", help="输出 PNG 路径")
    parser.add_argument("--scale", type=float, default=1.0,
                        help="系统 DPI 缩放，例如 1.75（默认 1.0）")
    args = parser.parse_args(argv[1:])

    parsed = parse_assignments(os.path.join(UI, "theme.py"), {"COLORS", "FONT_SIZES"})
    colors = parsed["COLORS"]
    sizes = parsed["FONT_SIZES"]
    logical = class_constants(os.path.join(UI, "widgets.py"), "FluentComboBox")
    _shape = load_shape()

    print("=== 几何常量（从 widgets.py 解析，逻辑像素）===")
    for name in GEOMETRIC:
        if name in logical:
            print(f"  {name:<14} {logical[name]}")
    print(f"  选项字号     {sizes.get('menu')}px（字段是 {sizes.get('small')}px）")
    if args.scale != 1.0:
        print(f"  按 {args.scale}x 缩放后的物理像素: "
              + ", ".join(f"{k}={round(logical[k] * args.scale)}"
                          for k in GEOMETRIC if k in logical))
    print()

    ok = check(_shape, colors, logical)

    geom = {k: round(logical[k] * args.scale)
            for k in GEOMETRIC if k in logical}
    menu_px = abs(sizes.get("menu", -14))

    width = round(150 * args.scale)
    field_h = round(FIELD_H * args.scale)
    # 边距要把「面板四周那圈投影」铺得下：真机的 SHADOW_SPREAD 已经从 14 调到 20，
    # 按老值（26）留会被图片边裁掉一截，看着像阴影被切了个硬边。
    margin = max(round(MARGIN * args.scale), geom.get("SHADOW_SPREAD", 0) + 2)
    label_h = round(LABEL_H * args.scale)
    panel_h = _shape.list_height(len(VALUES), geom["PAD_V"], geom["ROW_H"],
                                 geom["ITEM_GAP"])
    # ⚠️ 面板**不是挂在字段下面**，而是「选中那一行的中线压在字段中线上」→ 它整个盖住
    # 字段、还会往上探出字段之外（真机见 ``FluentComboBox._anchor``，断言见
    # ``smoke_ui`` 的「选中那一行的中线压在字段中线上」）。预览图必须照这个摆 ——
    # 否则「面板盖住原显示区域」这个**视觉事实**在预览里根本看不到，
    # 而「字段四条描边」也仍然在一个面板没盖住的假场景里量，等于白量。
    pitch = _shape.row_pitch(geom["ROW_H"], geom["ITEM_GAP"])
    pick = VALUES.index(CURRENT) if CURRENT in VALUES else 0
    row_cy = geom["PAD_V"] + pick * pitch + geom["ROW_H"] // 2
    panel_dy = field_h // 2 - row_cy         # 面板顶相对字段顶的偏移（负数 = 往上探）
    up = max(0, -panel_dy)                   # 面板往上探出字段多少
    down = max(0, panel_dy + panel_h - field_h)   # 面板往下伸出字段多少
    states = [("无悬停", -1), ("悬停第 1 项", 0), ("悬停第 3 项", 2)]
    font = load_font(round(menu_px * args.scale) * SS)
    label_font = load_font(round(13 * args.scale) * SS)

    band_w = margin + len(states) * (width + round(GAP * args.scale))
    band_h = label_h + up + field_h + down + margin
    size = (band_w + margin, band_h * 2)
    painter = Painter(size)

    field_rects = []    # 字段：在**没有面板压着**的单体图上量（面板一盖就看不着了）
    solo_imgs = []
    panel_rects = []    # 面板：合成图上量（面板在最上层，四边都露着）
    for row, dark in enumerate((False, True)):
        top = row * band_h
        bg = colors["window"][1 if dark else 0]
        fg = colors["text"][1 if dark else 0]
        tone = "深色" if dark else "浅色"
        painter.rect(0, top, size[0], top + band_h, fill=bg)
        painter.text(margin, top + 2, tone, label_font, fg, 13)
        # 字段另画一张**只有它自己**的图：合成图里面板是盖在字段上的（新定位就是这样），
        # 那时再按「字段那块矩形」取样，量到的是面板的底色，等于没量。
        solo = Painter((width + 2, field_h + 2))
        solo.rect(0, 0, width + 2, field_h + 2, fill=bg)
        draw_field(solo, 1, 1, width, colors, dark, CURRENT, font, field_h)
        solo_imgs.append(solo.result((width + 2, field_h + 2)))
        field_rects.append((f"{tone} 字段", (1, 1), (width, field_h),
                            colors["control_stroke"][1 if dark else 0],
                            colors["control"][1 if dark else 0]))
        for col, (name, hover) in enumerate(states):
            x = margin + col * (width + round(GAP * args.scale))
            y = top + label_h + up
            # 行名跟主题名一样摆在**面板上方那条留白**里（``label_h`` 就是留给这两行的）。
            # 原来它是贴着字段上沿往上 18px —— 现在面板会盖到字段上方，那个位置正好被
            # 面板吃掉，字全不见了（面板顶边 = ``top + label_h``，见上面 ``panel_dy``）。
            painter.text(x, top + round(20 * args.scale), name, label_font, fg, 13)
            draw_field(painter, x, y, width, colors, dark, CURRENT, font, field_h)
            py = y + panel_dy             # 面板：选中行中线 = 字段中线（盖住字段）
            draw_panel(painter, x, py, width, colors, dark, hover, font, geom,
                       logical.get("SHADOW_OPACITY", 0.22))
            panel_rects.append((f"{tone} 面板", (x, py), (width, panel_h),
                                colors["layer_stroke"][1 if dark else 0],
                                colors["layer"][1 if dark else 0]))

    out = (args.out if args.out
           else os.path.join(tempfile.gettempdir(), "mcrender_ui_combo.png"))
    img = painter.result(size)
    ok = verify_borders(img, panel_rects, "四边描边（面板；上下左右各取边中点，往内试 3 像素）") and ok
    for solo, entry in zip(solo_imgs, field_rects):
        ok = verify_borders(solo, [entry],
                            f"四边描边（字段，单独一张图量的：{entry[0]}）") and ok
    img.save(out)
    print(f"\n预览图已写出：{out}  ({size[0]}×{size[1]})")
    return 0 if ok else 1


def verify_borders(img, rects, title="四边描边（上下左右各取边中点，往内试 3 像素）：") -> bool:
    """量每条边**最外那几像素**里有没有描边色，再量中心是不是填充色。

    用户报过「下拉列表选框下端无框线」「角落线条太粗/细」——病根都是贴着控件边沿
    画描边被裁掉，所以上下左右各取一条边来验，不放过任何一边。Pillow 的填充按像素
    中心判定，最外一列/一行有时落在边界外，所以每条边往里试 3 个像素，命中即可。

    ⚠️ 必须**在没东西压着它的图上量**：面板是盖在字段上的（新定位），按「字段那块矩形」
    在合成图里取样只会量到面板的底色 —— 字段得单独画一张自己量（见 ``main``）。
    """
    print(f"\n{title}")
    ok = True
    for name, (x, y), (w, h), stroke, fill in rects:
        want_s, want_f = _rgb(stroke), _rgb(fill)
        cx, cy = x + w // 2, y + h // 2
        edges = {
            "上": [(cx, y + i) for i in range(3)],
            "下": [(cx, y + h - 1 - i) for i in range(3)],
            "左": [(x + i, cy) for i in range(3)],
            "右": [(x + w - 1 - i, cy) for i in range(3)],
        }
        found, good = {}, True
        for side, points in edges.items():
            # 命中的条件：颜色够接近描边色，且**比接近填充色更接近描边色**
            # —— 否则「整条边都没有描边」时，露出的填充色也会被当成勉强合格
            hit = next((img.getpixel(p) for p in points
                        if _far(img.getpixel(p), want_s) <= 24
                        and _far(img.getpixel(p), want_s) < _far(img.getpixel(p), want_f)),
                       None)
            found[side] = hit
            good = good and hit is not None
        center = img.getpixel((cx, cy))
        good = good and _far(center, want_f) <= 24
        ok = ok and good
        detail = " ".join(f"{s}=#{c[0]:02X}{c[1]:02X}{c[2]:02X}" if c
                          else f"{s}=无" for s, c in found.items())
        print(f"  {'✓' if good else '✗'} {name:<10} {detail}"
              + f"  中=#{center[0]:02X}{center[1]:02X}{center[2]:02X}"
              + f"（期望边 {stroke}、中 {fill}）")
    return ok


def _rgb(color: str):
    v = color.lstrip("#")
    return tuple(int(v[i:i + 2], 16) for i in (0, 2, 4))


def _far(a, b) -> int:
    return max(abs(p - q) for p, q in zip(a, b))


if __name__ == "__main__":
    sys.exit(main(sys.argv))
