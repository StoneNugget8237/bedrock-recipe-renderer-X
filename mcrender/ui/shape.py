"""纯几何 / 纯数学辅助：Canvas 小图形的顶点、列表行距、动画缓动曲线。

刻意**不导入 tkinter**——这样在没有 tkinter 的开发机上也能直接算点集、
用 Pillow 渲染出来肉眼检查形状（见 ``tools/preview_ui_icons.py``），
并且能脱离窗口把命中判定断言一遍（见 ``tools/preview_ui_combo.py``）。
缓动曲线同理：它是纯函数，离线也能逐点核对（见 ``tools/preview_ui_icons.py``
里对 ``fast_out_slow_in`` 的单调性与端点断言）。

Tk 的 Canvas 没有圆角矩形图元，只能自己生成多边形顶点。
"""
from __future__ import annotations

import math


def round_rect_points(x0, y0, x1, y1, radius, steps: int = 6) -> list:
    """圆角矩形的顶点（顺时针），可直接喂给 ``Canvas.create_polygon``。

    ``radius`` 会被夹到不超过短边一半；``steps`` 是每个圆角的采样段数。
    Tk 的 Canvas 是硬边渲染（不抗锯齿），段数够用即可，不需要太密。
    """
    x0, x1 = (x0, x1) if x0 <= x1 else (x1, x0)
    y0, y1 = (y0, y1) if y0 <= y1 else (y1, y0)
    r = max(0.0, min(float(radius), (x1 - x0) / 2, (y1 - y0) / 2))
    if r <= 0:
        return [x0, y0, x1, y0, x1, y1, x0, y1]

    points = []
    # 四个圆角圆心 + 起始角度：右上 270° → 右下 0° → 左下 90° → 左上 180°
    corners = ((x1 - r, y0 + r, 270.0),
               (x1 - r, y1 - r, 0.0),
               (x0 + r, y1 - r, 90.0),
               (x0 + r, y0 + r, 180.0))
    for cx, cy, start in corners:
        for i in range(steps + 1):
            angle = math.radians(start + i * 90.0 / steps)
            points.append(cx + math.cos(angle) * r)
            points.append(cy + math.sin(angle) * r)
    return points


# ------------------------------------------------------------------ 列表行
def row_pitch(row_h, gap):
    """相邻两行**起点**之间的距离 = 行高 + 行间距。"""
    return row_h + gap


def row_bounds(count, pad_top, row_h, gap) -> list:
    """每一行高亮块的 ``(y0, y1)``：行高 ``row_h``，行与行之间留 ``gap``。

    ``widgets.FluentComboBox`` 里「画出来的高亮块」和「命中判定」都走这个函数，
    免得两边各算一遍、迟早错位。
    """
    bounds = []
    step = row_pitch(row_h, gap)
    for index in range(max(0, int(count))):
        y0 = pad_top + index * step
        bounds.append((y0, y0 + row_h))
    return bounds


def list_height(count, pad, row_h, gap):
    """整个下拉列表的高度：上下各留 ``pad``，中间是 count 行 + (count-1) 个缝。"""
    count = max(0, int(count))
    if count == 0:
        return 2 * pad
    return 2 * pad + count * row_h + (count - 1) * gap


def row_index(y, pad_top, pitch, count: int) -> int:
    """把列表里的纵坐标反推成行号；落在上下留白或界外返回 -1。

    ``pitch`` 是相邻两行起点之间的距离（``row_pitch`` 的结果）。指针落在两行之间
    的缝隙里会归到**上面那一行**——缝隙就是上一行的下沿，判成 -1 反而会让点击
    落在空处、把面板关掉。

    下拉的悬停高亮就靠这个函数定位——放到这里是为了能脱离 tkinter 单独验证。
    """
    if pitch <= 0 or count <= 0:
        return -1
    index = int((y - pad_top) // pitch)
    return index if 0 <= index < count else -1


# ------------------------------------------------------------------ 缓动 / 插值
# 下拉展开与开关滑块都按「真实时间 → 进度 0..1 → 缓动后的进度」驱动。曲线放这里
# 是因为它和几何一样是纯函数：没有 tkinter 也能跑，可以逐点对拍（`tools/
# preview_ui_icons.py` 就断言了 fast_out_slow_in 的端点与单调性）。
def clamp01(t) -> float:
    """把进度夹进 ``[0, 1]``（``None`` 之类的脏值一律当 0）。"""
    try:
        t = float(t)
    except (TypeError, ValueError):
        return 0.0
    if t != t:                      # NaN
        return 0.0
    return 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)


def lerp(a, b, t) -> float:
    """线性插值；``t`` 会先夹到 ``[0, 1]``。"""
    return a + (b - a) * clamp01(t)


def cubic_bezier(t, x1, y1, x2, y2) -> float:
    """三次贝塞尔缓动（CSS / WinUI 那条 ``cubic-bezier(x1,y1,x2,y2)``）。

    端点固定为 ``(0,0)`` 与 ``(1,1)``，两个控制点由调用方给。贝塞尔是参数方程
    ``(x(s), y(s))``，而我们要的是 ``y(x)`` —— 所以先解 ``x(s) = t`` 拿到 ``s``，
    再取 ``y(s)``。解用牛顿迭代，收敛不了（导数近零 / 越界）就退回二分，
    保证任何输入都单调、不抖、不炸。
    """
    t = clamp01(t)
    if t <= 0.0 or t >= 1.0:
        return t

    def axis(s, p1, p2):
        return 3.0 * (1.0 - s) ** 2 * s * p1 + 3.0 * (1.0 - s) * s ** 2 * p2 + s ** 3

    def slope(s, p1, p2):
        return (3.0 * (1.0 - s) ** 2 * p1
                + 6.0 * (1.0 - s) * s * (p2 - p1)
                + 3.0 * s ** 2 * (1.0 - p2))

    s, ok = t, True
    for _ in range(8):
        err = axis(s, x1, x2) - t
        if abs(err) < 1e-7:
            break
        d = slope(s, x1, x2)
        if abs(d) < 1e-7:
            ok = False
            break
        s -= err / d
        if not 0.0 <= s <= 1.0:
            ok = False
            break
    if not ok:
        lo, hi = 0.0, 1.0
        for _ in range(24):
            s = (lo + hi) / 2.0
            if axis(s, x1, x2) < t:
                lo = s
            else:
                hi = s
    return axis(s, y1, y2)


def fast_out_slow_in(t) -> float:
    """WinUI / Fluent 的标准加速曲线 ``ControlFastOutSlowInKeySpline``。

    等价于 ``cubic-bezier(0.1, 0.9, 0.2, 1.0)``：起步快、尾巴长，控件位移基本都用它。
    下拉面板展开、开关滑块滑动都用这条 —— 与系统控件的观感一致。
    """
    return cubic_bezier(t, 0.1, 0.9, 0.2, 1.0)


def ease_out_cubic(t) -> float:
    """三次缓出：起步猛（速度 3 倍）、末端极慢。

    下拉展开曾经用它，现在换成了 ``soft_in_out`` —— 起步那一冲在 16ms 一帧的
    Tk 里等于第一帧就窜掉一大截，看着是「弹开」。留在这里当通用曲线，
    ``tools/preview_ui_icons.py`` 仍会对它的端点做断言。
    """
    t = clamp01(t)
    return 1.0 - (1.0 - t) ** 3


def ease_in_cubic(t) -> float:
    """三次缓入，``ease_out_cubic`` 的镜像。"""
    t = clamp01(t)
    return t ** 3


def soft_in_out(t) -> float:
    """「柔和」曲线 —— Fluent 的 ``EasyEase``，等价 ``cubic-bezier(0.33, 0, 0.67, 1)``。

    和前面几条最大的区别是**两端速度都是 0**：起步不猛冲、落点不急刹，速度到中段
    才到峰值（约平均速度的 1.5 倍）。下拉面板展开 / 收起走的就是它 —— 换成它之后
    同样时长里每一帧的高度增量都均匀得多，观感从「弹开」变成「长出来」。

    曲线关于中心对称（``f(1 - t) == 1 - f(t)``），所以展开和收起共用一条：收起那一帧
    直接取 ``1 - soft_in_out(progress)``，就是镜像回来的同一段运动。
    """
    return cubic_bezier(t, 0.33, 0.0, 0.67, 1.0)


def travel_frame(s, start, target) -> tuple:
    """开关动画的一帧：把**线性时间进度**换算成 ``(颜色进度, 滑块行程)``。

    ``start`` / ``target`` 分别是出发和到达的端点（0 = 关、1 = 开），于是「开」和
    「关」两个方向各自动作一致。位置与颜色**必须分开**：

    - 位置过一遍 ``fast_out_slow_in``（Fluent 的观感：起步快、尾巴长）；
    - 颜色走线性，让蓝底和滑块几乎是匀速地淡过来。

    关键在于缓动作用在**行程**上而不是绝对进度上 —— 如果对绝对进度取 ``ease(t)``，
    那么「关」的时候 t 从 1 往回走，就变成「前 80% 几乎不动、最后猛跳」，很难看。

    抽在这里（而不是写在控件里）是为了让离线预览脚本能用**同一份算式**复现中间帧，
    不必抄一遍（见 ``tools/preview_ui_icons.py``）。
    """
    s = clamp01(s)
    return (lerp(start, target, s), lerp(start, target, fast_out_slow_in(s)))
