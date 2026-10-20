"""WinUI 3 风格的自绘控件：下拉框与开关。

CustomTkinter 自带的这两个控件形状是库写死的，改不成 Fluent：

- ``CTkOptionMenu`` 的下拉其实是原生 ``tkinter.Menu``（由 Tk 自己绘制，带边框和
  方角高亮）——颜色能改、形状改不了；
- ``CTkSwitch`` 是在 Canvas 上手画的大药丸加一个大圆点，比例和 WinUI 3 差得远，
  而且文字只能放滑块右边（WinUI 3 是文字在左、滑块在右）。

所以这两个在这里重做：下拉用**主窗口内的一块自绘 Canvas**，开关用 Pillow 画好的
轨道 / 滑块图片。其余控件继续用 CTk 原生实现。

下拉列表刻意做成**一块 Canvas 全自绘**（选项是 Canvas 图元，不是 Frame/Label）：
没有嵌套控件就没有 Enter/Leave 抖动，悬停高亮不会「有时不变色」；顺带还能画
圆角面板、圆角高亮块和圆角选中竖条。

面板是主窗口的**子控件**（不是独立窗口），位置、底图与这样做的理由见
``FluentComboBox`` 的类文档。
"""
from __future__ import annotations

import os
import time
import tkinter as tk
import weakref

import customtkinter as ctk
from PIL import Image, ImageTk

from . import icons, shape, theme, win32

# 已经建出来的所有下拉（弱引用，窗口销毁后自动消失）。用来在打开一块面板之前，
# 把**同一窗口里其它**还开着 / 正在收的面板立刻收掉 —— 抓背景快照时它们会被
# ``PrintWindow`` 一起拍进去，那张快照就是「露出带以外」看到的东西（见 _open）。
_LIVE_COMBOS: "weakref.WeakSet" = weakref.WeakSet()


# ------------------------------------------------------------------ 小工具
def walk(widget):
    """深度优先遍历控件及其全部子孙（含 CTk 内部的 canvas / label）。"""
    yield widget
    for child in widget.winfo_children():
        yield from walk(child)


def descends_from(widget, ancestor) -> bool:
    """widget 是不是 ancestor 的后代（或者就是它本身）。"""
    while widget is not None:
        if widget is ancestor:
            return True
        widget = getattr(widget, "master", None)
    return False


def make_clickable(widget, on_click, cursor="hand2") -> None:
    """把左键点击挂到整棵控件树上。

    CTk 控件内部还有一个 tkinter.Label / Canvas 盖在顶上，只绑外层收不到点击，
    所以必须递归绑到子孙。
    """
    for w in walk(widget):
        try:
            w.bind("<Button-1>", on_click)
        except Exception:
            pass
        try:
            w.configure(cursor=cursor)
        except Exception:
            pass


# ------------------------------------------------------------------ 帧动画
FRAME_MS = 6                    # 默认帧间隔 —— 见 _Motion.start 的实测表，别照着「60fps」拍
_ANIM_ALLOWED = None            # 系统「显示动画」总开关的缓存（见 system_animations）


def system_animations() -> bool:
    """系统的「显示动画」总开关（Windows：设置 → 辅助功能 → 视觉效果）。

    对应 Win32 的 ``SPI_GETCLIENTAREAANIMATION``（0x1042）。读一次就缓存 ——
    这个值在一次会话里不会变，没必要每建一个控件都去问系统。
    读不到（非 Windows / 接口失败）一律当**开**：宁可有动画，也不要因为一个探测
    失败就让界面变得死板。

    两个自绘控件在 ``animate=None`` 时都走这里，所以系统关掉动效它们会跟着安静下来。
    """
    global _ANIM_ALLOWED
    if _ANIM_ALLOWED is None:
        _ANIM_ALLOWED = True
        if os.name == "nt":
            try:
                import ctypes

                value = ctypes.c_int(1)
                if ctypes.windll.user32.SystemParametersInfoW(
                        0x1042, 0, ctypes.byref(value), 0):
                    _ANIM_ALLOWED = bool(value.value)
            except Exception:
                _ANIM_ALLOWED = True
    return _ANIM_ALLOWED


class _Motion:
    """按**真实时间**推进的帧动画，Tk 里最小可行的那一种。

    为什么要有它：Tk 没有内建的补间，只能自己 ``after()`` 排下一帧。踩过的坑
    都在这里一次解决：

    1. **按真实时间算进度**，不按「第几帧」—— ``after(16)`` 只是**希望**间隔，
       系统忙的时候会晚到（实测能到 60ms+）。按帧号推进的话，掉帧就变慢动作，
       一次卡顿会让 170ms 的动画拖成好几百毫秒；按 ``perf_counter`` 算则自动补偿。
    2. **可取消**。每个实例带一个自增 ``token``，``cancel()`` 之后旧回调即使被排进
       队列也会立刻退出 —— 展开动画没跑完就收起、或连点两次时不会互相打架。
    3. **控件销毁后自动停**。``winfo_exists()`` 一假就返回，不再 ``after`` 下一帧；
       否则关窗时残留的 ``after`` 会去碰已经没了的控件，刷出 ``invalid command name``
       （``tools/smoke_ui.py`` 的「关窗安静」一项专门盯这个）。
    4. **动画期间自己提计时器精度**（``win32.timer_resolution``）。默认那档 ~15.6ms 的
       粒度会把 ``after(8)`` 量化成 13ms 上下，80ms 的动画只画得出 5 帧。提精度是
       **有借有还**的：``__slots__`` 里那个 ``_held`` 保证「提过一次就归还一次」，
       销毁 / 取消 / 自然跑完三条路都收敛到 ``_release()``。
    """

    __slots__ = ("_widget", "_job", "_token", "_held")

    def __init__(self, widget) -> None:
        self._widget = widget
        self._job = None
        self._token = 0
        self._held = False      # 是否正提着计时器精度（见 _acquire / _release）

    def running(self) -> bool:
        return self._job is not None

    def cancel(self) -> None:
        """停掉当前动画（幂等）。"""
        self._token += 1
        job, self._job = self._job, None
        if job is not None:
            try:
                self._widget.after_cancel(job)
            except Exception:
                pass
        self._release()

    def _acquire(self) -> None:
        """动画开始：把系统计时器精度提上去（已经在提就不重复提）。"""
        if not self._held:
            self._held = win32.timer_resolution(True)

    def _release(self) -> None:
        """动画结束：归还计时器精度（幂等）。

        ⚠️ **每一条停下来的路都得走到这里**：自然跑完、``cancel()``、控件已经销毁
        三种情况都要归还。漏一条就是精度一直提着不放（费电），而且看不出来。
        """
        if self._held:
            self._held = False
            win32.timer_resolution(False)

    def start(self, duration_ms, on_frame, on_done=None, interval: int = FRAME_MS) -> None:
        """跑一段动画：``on_frame(进度 0..1)`` 逐帧回调，结束（进度到 1）调 ``on_done``。

        ``interval`` 是**帧间隔**（毫秒），默认 ``FRAME_MS``。它只是「不早于」的请求，
        而且有**两笔账**要分开算（都是真机实测，别按「60fps → 16ms」拍脑袋）：

        - 系统那一层：``after(6)`` 在空跑的主循环里就是 6.09ms、``after(8)`` 是 8.23ms
          —— 前提是精度已经提过（见 ``_acquire``）；不提的话全被量化到 13ms 以上
          （``tools/probe_timer.py`` 会把这两遍都打出来）。
        - 每一帧自己的开销：Pillow 混图 + 换图 + Tk 把这块面板重画出去，合计约 **2ms**。
          它**压在帧间隔之上**，所以真动画里请求 6ms 落 8.4ms、请求 8ms 落 10.2ms
          （80ms 的展开实测）：

        | 请求 ``after()`` | 空跑主循环 | 真动画里 | 80ms 的帧数 |
        |---|---|---|---|
        | 8ms | 8.2ms | 10.2ms | 9 帧 |
        | 6ms | 6.1ms | **8.4ms** | **11 帧** |
        | 4ms | 4.1ms | 7.0ms（忽 5.9 忽 8.5） | 12 帧但间隔不匀 |

        取 6ms：帧数翻得动，间隔又最匀。**均匀比多一帧更要紧**，这也是为什么
        ``_acquire`` 那件事不能省。

        ``on_frame`` / ``on_done`` 里的异常一律吞掉：动画不该把界面搞崩。
        """
        self.cancel()
        token = self._token
        span = max(1.0, float(duration_ms)) / 1000.0
        t0 = time.perf_counter()
        self._acquire()

        def tick():
            if token != self._token:
                return                                  # 被 cancel / 被新动画顶掉了
            try:
                if not self._widget.winfo_exists():
                    self._release()                     # 控件没了，精度也得还回去
                    return
            except Exception:
                self._release()
                return
            progress = (time.perf_counter() - t0) / span
            if progress >= 1.0:
                progress = 1.0
            try:
                on_frame(progress)
            except Exception:
                pass
            if progress >= 1.0:
                self._job = None
                self._release()                         # 自然跑完也要还
                if on_done is not None:
                    try:
                        on_done()
                    except Exception:
                        pass
                return
            try:
                self._job = self._widget.after(interval, tick)
            except Exception:
                self._job = None
                self._release()

        try:
            self._job = self._widget.after(0, tick)
        except Exception:
            self._job = None
            self._release()
            if on_done is not None:
                try:
                    on_done()
                except Exception:
                    pass


# ------------------------------------------------------------------ 下拉框
class FluentComboBox(ctk.CTkFrame):
    """扁平的 WinUI 3 下拉框：左边当前值、右边一个「∨」，点开是主窗口内的一块自绘面板。

    用法和 ``CTkOptionMenu`` 基本一致（``variable`` / ``values`` / ``command``），
    只是 ``command`` 收到的仍然是选中的字符串。

    展开的列表是**一块 Canvas**，面板底、选项块、选中竖条都是贴上去的
    Pillow 图（圆角抗锯齿、四条边描边粗细一致），文字用 Canvas 文字；
    指针位置靠 ``<Motion>`` 的 y 反推行号，没有嵌套控件的 Enter/Leave。

    ⚠️ 面板是**主窗口里的一个子 Canvas**（``place`` 到字段下方，``lift`` 压住兄弟控件），
    不是独立窗口。以前它是 ``overrideredirect`` + ``-topmost`` 的 ``Toplevel``，代价是：

    - 在任务栏 / 切换器里像第二个窗口，主窗口一动它就不跟手；
    - 点到面板上焦点会落在**弹窗**上，此后切到别的程序收不到 ``FocusOut``，
      只能再轮询前台窗口兜底（``_watch_foreground`` 那一套就是这么来的）；
    - 圆角得靠分层窗口（``UpdateLayeredWindow``）或 ``-transparentcolor`` 才做得出来。

    改成子控件之后这三件事一起消失：面板跟着窗口走、被窗口客户区裁剪，窗口一动就
    顺手收起（``_on_host_configure``），也不再需要任何窗口级技巧。

    代价是**子控件没有 alpha** —— 圆角那四小块必须自己填。做法是在摆面板**之前**
    抓一帧主窗口内容（``win32.grab``），把 Pillow 画的圆角面板叠在这张快照上当作画布
    底图：四角露出来的就是**面板底下真实的样子**（被盖住的按钮 / 卡片 / 窗口底）。
    抓不到图时退化成窗口底色填充，面板照常可用，只是四角有一小块色差。

    ⚠️ **收起面板要挂在「松手」上，不能挂在「按下」上**：面板展开时常常正压着一排
    按钮，而一次点击是两个独立事件（``<Button-1>`` → 用户还按着的那段时间 →
    ``<ButtonRelease-1>``）。按下就把面板收掉的话，松手那次 release 会落到**面板底下
    那个控件**上；``CTkButton`` 的 command 恰恰在 release 里触发 —— 于是「选一个下拉项」
    会顺带把下面的按钮也点一次。改为**值在按下时生效、面板等松手再收**（``_on_release``）。
    面板是子控件，跟按钮是同一个窗口里的兄弟，这层没有窗口隔离可依靠。
    回归测试：``tools/check_option_click.py``（合成真鼠标事件）。

    ⚠️ **展开 / 收起 = 带子把画布切成三段、各贴各的，画布窗口全程不动**：面板是原生
    ``tk.Canvas``，它的图元只按**自己的窗口矩形**裁。早先图省事，每帧把画布
    ``place`` 到露出带上、再把带内图元反向顶回去 —— 屏幕上看着对，但窗口每帧都被
    Windows 搬一次：旧像素整块 ``BitBlt`` 过去、等下一次重画才补齐，中间那一下就是
    **撕裂 / 上下跳**（用户报的「菜单渲染区域画面撕裂」），阴影也会跟着被搬歪。

    现在改成：画布**一次就按满尺寸摆好，动画期间位置和尺寸都不碰**。要裁的东西由
    三块「拼贴」负责（``_paint``）：带顶以上 / 带底以下各贴一条**背景快照**（压在内容
    图元**上面**，把文字色块盖回背景），带内贴那一段**底图**（压在内容图元**下面**，
    只是面板的底子）。三段正好铺满画布，一帧传的像素总量不多于一块画布。于是：

    - 屏幕坐标**逐帧恒定**（什么都没动过，只有拼贴的位置与内容在变），不可能撕裂；
    - 内容图元的位置就是最终位置，命中判定不用任何补偿（``_index_at``）；
    - 每帧开销 ≈ 混一条 + 换三张 RGB PhotoImage，与早先「混一张整图」同一量级。

    ⚠️ 两条弯路都试过，别再回去：
    ① 每帧 ``place`` 画布窗口来裁 —— Windows 补画面的时机不可控，撕裂；
    ② 一层全画布 **RGBA 盖帘**，一次 alpha 混合同时做掉裁剪与淡入 —— 代码最短，但
       每帧重传一张带 alpha 的整图、画布还要逐像素混合，真动画里帧数掉得明显；
       而且它不是「按坐标取对应的那一段」，挪锚点会让内容错位（同下面这条）。

    露出带的原点 = **选中那一行的中线**（``_reveal_origin``），上下两边同时向外长；
    上边撞到画布顶边、下边撞到画布底边就各自停下，所以选中的是第一项时看着仍然像
    「从字段往下一路铺开」。当前值不在 ``values`` 里（列表被换过）就退回第一行，
    一行都没有就以面板中线为原点。

    ⚠️ **光让带子围着那一行长还不够，面板自己也得跟着挪**：面板若还按老规矩挂在字段
    **下方**，那一行长开之后整份列表仍旧在字段底下，字段一个像素都没被盖住。所以竖直
    位置改成「**选中那一行的中线 == 字段中线**」（``_anchor`` 的 ``row_cy``）：面板从此
    上下两头都伸到字段外面，把它连同上下邻居一起盖住，展开时也正好从字段正中往两头长。
    这正是 Win11 ComboBox 浮出控件的行为 —— 拿系统设置里那个「缩放」下拉的前后两张
    截图配准后逐像素量过：选中项那一行的中线与字段中线重合，偏差 0。也就是用户说的
    「以选中的选项为中心覆盖原来的显示区域」。

    两个时长在 Fluent 的 ``Faster``（100ms）与 ``Normal``（200ms）之间（展开 ``EXPAND_MS``、
    收起 ``COLLAPSE_MS``），曲线是两端速度为 0 的 ``shape.soft_in_out`` —— 起步不猛冲，
    所以这个长度看着是「长出来」而不是「弹开」。时长压得短，**帧数就必须靠帧间隔和
    计时器精度去抢**：

    - 帧间隔走模块级的 ``FRAME_MS``（6ms），不是默认的 16ms —— 16ms 在百来毫秒里只够
      六七帧，一眼就看得出是「跳」过去的；6ms 实测能出十几帧（见 ``_Motion.start`` 的表）；
    - 动画期间 ``_Motion`` 会提系统计时器精度（``win32.timer_resolution``）。不提的话
      ``after()`` 被 ~15.6ms 的默认粒度量化成 13ms 上下，帧数直接砍半、还忽快忽慢。

    所以说「更长 / 更短」和「更顺」这两件事在这里是靠**两个不同的旋钮**分别拧的：时长管
    快慢，帧间隔 + 计时器精度管帧数。只拧时长会把自己的帧数拧没。

    ⚠️ **展开动画播完还有第二段：四周的投影淡入**（``_fade_shadow_in`` → ``SHADOW_MS``）。
    为什么不当成一段跑：投影只有在面板**已经整个露出来**之后才有意义 —— 挂在展开进度上，
    那圈柔光会跟着露出带一起被裁掉上下两条，看着像面板边缘糊了一圈被切开的脏东西。
    所以先长、再亮，两段各自有自己的时长与曲线（都是 ``soft_in_out``）。这段动画同样是
    ``_Motion`` 跑的，精度照旧有借有还；``_settle()`` 会把它一推到终点（截图要的是稳定态）。

    ⚠️ **投影是「画在画布里的」**，不是窗口级效果：画布四周因此要比面板各**多留
    ``_shadow_pad``** 的空白（见 ``_open``），四边一样宽（``_shadow_top`` 就等于
    ``_shadow_pad``），那圈空白里画的就是 ``icons.soft_shadow`` 出来的柔和阴影，合成在
    **展开之前抓的那张背景快照**上 —— 所以阴影下露出来的仍是被盖住的真实内容（同四角
    那套做法）。留白的**上方**原先另有讲究：面板挂在字段下方时，画布顶边只敢往上探
    ``min(_shadow_pad, GAP)``，再多那圈阴影就糊到字段自己身上、把它的底边描边啃掉一条。
    面板现在盖在字段上，那条例外自然作废 —— 四边对称才是 Win11 的样子。

    收起同理，露出带缩回一条线再销毁。但 **``_popup`` 仍然在收起的瞬间同步置 None** ——
    「松手那一下必须由面板吃掉」的时序约束一个字没变（见 ``_on_release``），
    动画只是让那块画布多活 ``COLLAPSE_MS`` 再消失，期间它不再接受任何交互。

    ``animate=None``（默认）跟随系统的「显示动画」开关（见 ``system_animations``）；
    ``animate=False`` 完全退化成加动画之前的同步行为，工具脚本靠它拿确定的稳定态。
    """

    # 下列几何都是**逻辑像素**（96 DPI 基准），展开时按系统 DPI 换算成物理像素
    ROW_H = 32          # 单个选项高亮块的高度
    ITEM_GAP = 6        # 相邻两个选项之间的缝隙（行距 = ROW_H + ITEM_GAP）
    PAD_V = 6           # 列表上下内边距
    PAD_H = 5           # 选项块左右内缩
    RADIUS = 10         # 面板圆角
    ITEM_RADIUS = 6     # 选项块圆角
    PILL_W = 3          # 选中竖条宽（WinUI 的选中指示条就是这个粗细）
    PILL_H = 16         # 选中竖条高（行高的一半，比行高矮才像「指示条」）
    PILL_INSET = 2      # 选中竖条相对选项块左沿的内缩
    # 字段与列表之间的缝。⚠️ 面板现在**盖在字段上**（选中那一行的中线对齐字段中线，
    # 见 ``_anchor``），定位已经不用它 —— 只剩 ``tools/preview_ui_combo.py`` 拿它当
    # 参考图里「字段与面板之间留多远」的排版缝。
    GAP = 4
    TEXT_X = 9          # 字段里文字的左边距（从字段内沿量）
    MENU_TEXT_GAP = 6   # 选项文字离「选中竖条」右沿的间距（见 _geometry 里的 menu_text_x）
    ARROW_PAD = 4       # 字段右沿到箭头的留白
    FIELD_RADIUS = 4    # 字段底板圆角
    FIELD_BORDER = 1    # 字段底板描边宽（描边细了转角就「发虚」，见 icons.rounded_rect）
    FIELD_PAD_V = 1     # 文字 / 箭头再离描边多少（逻辑像素）—— 见 _layout_children
    # 字段（选框）的**最小宽度**＝下拉面板的最小宽度。这两个必须共用同一个数：
    # 面板宽度取 ``max(字段宽, MIN_W)``，字段宽度也按 ``max(width, MIN_W)`` 兜底 ——
    # 于是面板右沿**永远**压着字段右沿（用户报的「下拉菜单比选框宽、两截对不齐」）。
    # 早先字段可以是 76 逻辑像素、面板却按 120 起，开出「缩放」时面板比字段整整宽
    # 44 逻辑像素，右边探出去一大截。
    MIN_W = 120
    SCREEN_MARGIN = 8   # 面板与主窗口客户区边界之间至少留这么多（逻辑像素，见 _anchor）
    EXPAND_MS = 110     # 展开动画时长（Fluent 的 Normal 与 Faster 之间，曲线柔和才敢压这么短）
    COLLAPSE_MS = 90    # 收起动画时长（比展开再短一档，收得干脆）
    SHADOW_MS = 150     # 面板四周那圈投影的淡入时长（展开动画播完才开始，见 _fade_shadow_in）
    SHADOW_SPREAD = 20  # 投影的**铺开距离**（逻辑像素）：σ = 它 / 2，见 icons.soft_shadow
    SHADOW_OPACITY = 0.22   # 投影中心的不透明度；面板边上一圈只有它的一半（见 icons.soft_shadow）
    # 上面这两个是「柔和 + 变浅」那次的取值（原来是 14 / 0.34）：
    # 铺开距离放大 → σ 跟着放大 → 同样的浓淡铺在更长的距离上，看着更柔；
    # opacity 直接压到 0.22 → 面板边上一圈约 11% 黑（原来约 17%），比底色只压暗一点点。
    # ⚠️ 画布四周的留白**不等于** SHADOW_SPREAD：尾巴要到 3σ 之外才看不见，留白得按
    # ``icons.shadow_pad`` 算（见 ``_open``），否则柔光的外沿会被画布边沿切出一条直边。
    # 面板里的「内容图元」（色块 / 选中竖条 / 文字）共用的 tag。叠放顺序是
    # 底图 → 内容图元 → 两条盖条（盖条建得最晚，天然压在最上面）。
    CONTENT_TAG = "menu_content"

    def __init__(self, master, variable, values, width=120, height=30,
                 font=None, menu_font=None, command=None, animate=None, **kwargs):
        # 字段宽度先按 ``MIN_W`` 兜底：面板宽度就是字段宽度（见 ``_open``），
        # 字段窄于 ``MIN_W`` 时面板会被撑宽 → 右沿对不齐（见 MIN_W 的注释）。
        width = max(int(round(width)), self.MIN_W)
        # 底板由自己用 Pillow 画（见 icons.rounded_rect）——所以这里刻意不要
        # CTk 的圆角与描边，只要它当个 «能放子控件、能被 DPI 缩放» 的容器。
        super().__init__(master, width=width, height=height, corner_radius=0,
                         fg_color="transparent", **kwargs)
        self.pack_propagate(False)      # 固定成设定尺寸，不随子控件涨缩
        self.grid_propagate(False)

        self._var = variable
        self._values = [str(v) for v in values]
        self._command = command
        self._font = font
        self._menu_font = menu_font or font
        _LIVE_COMBOS.add(self)           # 见 _dismiss_other_panels
        self._popup = None               # 展开时的面板：主窗口里的一块 tk.Canvas
        self._menu_canvas = None         # 同上（名字留着，工具脚本按它取画布）
        self._host = None                # 承载面板的顶层窗口（主窗口），收起时清空
        self._rows: list = []            # 每个选项的高亮块
        self._pills: list = []           # 每个选项的选中竖条
        self._picked: list = []          # 每个选项是否就是当前值
        self._hover = -1                 # 指针停在第几项，-1 = 不在任何项上
        self._bindings: list = []
        self._focus_timer = None
        self._images: dict = {}
        self._palette: dict = {}
        self._geom: dict | None = None   # 本次展开的物理像素几何（跟 DPI 走）
        self._photos: dict = {}          # 展开期的 Pillow 图引用（不留就被 GC）
        self._field_photo = None
        self._field_img = None
        self._field_key = None
        self._field_job = None
        self._panel_img = None           # Pillow 画的圆角面板（RGBA，直通 alpha）
        self._panel_photo = None         # 面板底图（面板叠在背景快照上）的 Tk 引用
        self._block_img = None
        self._pill_img = None
        self._bounds: list = []
        # 展开 / 收起动画（见类文档）。``animate=None`` = 跟随系统的「显示动画」开关；
        # 显式给 False 时一切走原来的同步路径 —— 做像素级断言的工具脚本靠它拿确定结果。
        self._animate = system_animations() if animate is None else bool(animate)
        self._motion = _Motion(self)
        self._closing = None             # 正在播收起动画、还没销毁的那块画布
        self._closing_photos: dict = {}  # 收起途中的图引用，不留就被 GC 掉成白板
        self._fade = None                # 动画用的状态：三张源图 + 底图/三段拼贴，见 _paint
        self._closing_fade = None        # 同上，收起动画接管的那一份
        self._fade_alpha = 1.0           # 面板当前透明度（真机脚本按它断言）
        self._shadow_alpha = 1.0         # 四周投影当前的不透明度（同上）
        self._reveal_origin = 0          # 露出带的原点 = 选中那一行的中线（画布坐标）
        self._row_cy = 0                 # 同一行的中线，但用**面板坐标**（定位要用，见 _anchor）
        self._reveal = None              # 当前露出带 (顶边, 底边)，None = 整块画布都露出
        # 收起动画的起点：从「按下收起那一刻的画面」接着走，而不是先跳成全开。
        # 见 _close / _frame_collapse —— 展开还没播完就关掉时，后者就是「菜单又冒出来」。
        self._collapse_from = 1.0        # 起点的露出进度 s（带高 = s × 满高）
        self._collapse_mix = 0.0         # 起点带内混进背景的比例（0 = 面板全实）
        # 刚拆掉的那块面板留下的**干净背景**：``(矩形, RGB 图)``。抓背景快照之前
        # 用它把旧像素补掉（见 _patch_backdrop）—— ``PrintWindow`` 拿到的是窗口上一次
        # 合成的表面，拆掉子窗口后要等事件循环跑一轮才会刷新。
        self._stale_patches: list = []
        self._box = (0, 0, 0, 0)         # 画布 (x, y, 宽, 满高) —— **含四周投影留白**
        self._panel_box = (0, 0, 0, 0)   # 面板本身在客户区里的矩形（画布内缩 _shadow_pad）
        self._shadow_pad = 0             # 画布比面板四周大出来的物理像素（投影留白）
        self._shadow_top = 0             # 画布**顶边**往上探多少（= _shadow_pad，四边对称）

        # 字段底板铺在最底下（Tk 里先建的在下），文字与箭头压在它上面
        self._field = tk.Label(self, bd=0, highlightthickness=0)
        self._field.place(x=0, y=0, relwidth=1, relheight=1)

        # 文字与箭头的底色必须**显式**写成字段填充色：CTkLabel 的
        # ``fg_color="transparent"`` 会去取父控件的颜色，而字段壳子是透明的，
        # 于是它们会拿窗口底色在字段上糊两块灰（实测能看见箭头后面一块灰底）。
        #
        # ``bg_color`` 也要一起给：CTkLabel 的底色其实是画在 canvas 上的一块
        # **圆角矩形**，尺寸取自 ``_current_width/_current_height``，实测会比控件本身
        # 小两三像素，露出来的是 canvas 自己的底色 —— 而 canvas 底色正是 ``bg_color``
        # （见 customtkinter 的 ``ctk_label._draw``）。不设的话就会在字段里看到
        # 一条**窗口底色的竖线**（用户报的「选框存在不必要竖线」）。
        fill = theme.color("control")       # (浅, 深) 两色，CTk 自己挑
        self._text = ctk.CTkLabel(self, text="", font=font, anchor="w",
                                  text_color=theme.color("text"),
                                  fg_color=fill, bg_color=fill, corner_radius=0)
        self._arrow = ctk.CTkLabel(self, text="", image=self._chevron(),
                                   fg_color=fill, bg_color=fill, corner_radius=0)
        # 位置在 _layout_children 里按实际尺寸 place（**不用 pack**，见那里的注释）
        self._layout_children(self.winfo_width(), self.winfo_height())

        self._sync()
        make_clickable(self, self._on_click)
        self._var.trace_add("write", lambda *_: self._sync())
        self.bind("<Configure>", lambda _e: self._schedule_field(), add="+")
        self._schedule_field()

    # ---------------------------------------------------------- 基本取值
    def get(self) -> str:
        return self._var.get()

    def set(self, value) -> None:
        self._var.set(str(value))

    def set_values(self, values) -> None:
        self._values = [str(v) for v in values]

    def _chevron(self):
        if "chevron" not in self._images:
            self._images["chevron"] = ctk.CTkImage(
                *icons.chevron_pair(), size=(icons.CHEVRON, icons.CHEVRON))
        return self._images["chevron"]

    def _sync(self) -> None:
        try:
            self._text.configure(text=str(self._var.get()))
        except Exception:
            pass

    # ---------------------------------------------------------- 字段底板
    # 为什么不用 CTkFrame 自己的 corner_radius/border_width 画这层：
    # ① 圆角质量 —— Tk 的 Canvas 没有抗锯齿，CTk 的圆角是**字形拼的**（font_shapes），
    #    半径 4 逻辑像素时角上一圈只有 5~8 级过渡色，放大看是硬阶梯（实测：
    #    font_shapes 5~8 级 > polygon_shapes 2 级 ≈ circle_shapes 2~3 级，
    #    而 Pillow 4× 超采样 + LANCZOS 有十几级）。
    # ② 四边一致 —— Pillow 的 rounded_rectangle 描边骑在路径上，盒子取满 0..W-1/0..H-1
    #    就变成**往内**画，四条边粗细天然一致（盒子内缩 border/2 的话，最外半行会被裁掉，
    #    看着就是「上/左/右有边、下边没有」）。
    # 注：CTkFrame 自己的 border_width 实测**不会**缺底边，别冤枉它。
    def _schedule_field(self) -> None:
        if self._field_job is not None:
            return
        try:
            self._field_job = self.after_idle(self._draw_field)
        except Exception:
            self._field_job = None

    def _draw_field(self) -> None:
        self._field_job = None
        try:
            w, h = self.winfo_width(), self.winfo_height()
        except Exception:
            return
        if w <= 1 or h <= 1:
            return
        self._layout_children(w, h)
        dark = ctk.get_appearance_mode() == "Dark"
        key = (w, h, dark)
        if key == self._field_key:
            return
        scale = self._get_widget_scaling()
        window = theme.color("window", dark)
        img = icons.rounded_rect(
            (w, h), radius=self.FIELD_RADIUS * scale,
            fill=theme.color("control", dark),
            stroke=theme.color("control_stroke", dark),
            border=max(1, round(self.FIELD_BORDER * scale)),
            bg=window)                      # 圆角外铺窗口底色，别露出 Tk 的灰
        try:
            photo = ImageTk.PhotoImage(img)
        except Exception:
            return
        try:
            self._field.configure(image=photo, bg=window)
        except Exception:
            return
        self._field_photo = photo           # 必须留引用，否则被 GC 掉字段就成白板
        self._field_img = img               # 原始 PIL 图：留给自检量像素
        self._field_key = key

    def _set_appearance_mode(self, mode_string):
        super()._set_appearance_mode(mode_string)
        self._field_key = None              # 换了深浅色，底板要重画
        self._schedule_field()

    # ---------------------------------------------------------- 字段里的文字与箭头
    def _layout_children(self, w: int, h: int) -> None:
        """把文字与箭头摆进**描边以内**那块矩形里（用 place，不用 pack）。

        以前用 pack：两个 CTkLabel 的自然高度是 49、字段高 52，pack 会在竖直方向
        居中 → 它们各压住一行描边；两个 label 之间还留着箭头那个 ``padx`` 的缝，
        缝里透出别的东西，看着就是「轮廓不均匀 + 多余的竖线」。

        ⚠️ **竖直方向还要再让出 ``FIELD_PAD_V``**，只让 ``border`` 是不够的：
        label 是不透明的底色块，它压在描边上就是**把描边啃掉**。实测（175%、字段
        182×52 物理像素、描边占第 50/51 行）文字 label 落在第 2..50 行 →
        **底边上半行被啃掉一条**，量出来是「底边深浅不一、断断续续」（用户报的
        「选择区域下方框线不均匀」）。多让 1 逻辑像素（≈1.75 物理像素）就空出
        一整行，四边都不会被啃。``tools/smoke_ui.py`` 会对真实窗口的像素做断言。

        坐标与尺寸一律用**逻辑像素**：CustomTkinter 的 ``place()`` 会**自己**按
        DPI 缩放 ``x`` / ``y``，``configure(width=/height=)`` 也会自己缩放尺寸 ——
        我们再乘一遍就成了双倍缩放。所以先把 ``winfo_width()`` 给的物理值除回去。

        ⚠️ 两个 CTk 的坑，踩过一次就难忘：
        ① ``place(width=...)`` / ``place(height=...)`` **没有任何提示地不接受**，
           直接抛 ``ValueError``（"must be passed to the constructor"）——
           尺寸只能走 ``configure(width=, height=)``；
        ② 上面这个异常会被下面的 ``except`` 吞掉，表现成「文字和箭头**整个不显示**」，
           字段变成一块空白底 —— 而不是报错。``tools/smoke_ui.py`` 有个断言盯着它。
        """
        scale = self._get_widget_scaling() or 1.0
        lw, lh = w / scale, h / scale
        if lw <= 2 or lh <= 2:
            return
        border = float(self.FIELD_BORDER)               # 逻辑像素
        inset_y = border + self.FIELD_PAD_V             # 上下都让开这么多
        inner_h = max(1.0, lh - 2 * inset_y)            # 只占描边以内，四条边都不盖
        slot = icons.CHEVRON + 4                        # 箭头格：图标 + 与文字之间的缝
        slot_x = max(border, lw - self.ARROW_PAD - slot)
        arrow_x = slot_x + (slot - icons.CHEVRON) / 2.0  # 图标在格子里居中
        text_x = min(float(self.TEXT_X), max(border, slot_x - 1.0))
        text_w = max(1.0, slot_x - text_x)
        for widget, x, width in ((self._text, text_x, text_w),
                                 (self._arrow, arrow_x, float(icons.CHEVRON))):
            try:
                widget.configure(width=width, height=inner_h)
                widget.place(x=x, y=inset_y)
            except Exception:
                pass

    # ---------------------------------------------------------- 展开
    def _on_click(self, _event=None) -> None:
        """再点一次字段就是收起（和 WinUI 3 的下拉行为一致）。"""
        if self._popup is not None:
            self._close()
        else:
            self._open()

    def _geometry(self) -> dict:
        """把逻辑像素几何换算成物理像素。

        CustomTkinter 会按系统 DPI 缩放**它自己的**控件（实测这台机器是 175%），
        而这里画的是原生 ``tk.Canvas`` 与 Canvas 文字 —— 不过一遍换算就不会跟着
        放大：面板只有字段的一半高、选项文字缩成一小点。所以几何与字体都走
        CTk 的官方换算（``1.75`` 比例下 32 → 56、字号 -14 → -24）。
        """
        scale = self._get_widget_scaling()

        def px(value):
            return max(1, round(value * scale))

        font = self._menu_font
        if font is None:
            font = (theme.FONT_FAMILY, theme.FONT_SIZES["menu"])
        return {
            "row_h": px(self.ROW_H),
            "item_gap": px(self.ITEM_GAP),
            "pitch": px(shape.row_pitch(self.ROW_H, self.ITEM_GAP)),
            "pad_v": px(self.PAD_V),
            "pad_h": px(self.PAD_H),
            "radius": px(self.RADIUS),
            "item_r": px(self.ITEM_RADIUS),
            "pill_w": px(self.PILL_W),
            "pill_h": px(self.PILL_H),
            "pill_r": px(self.PILL_W / 2),
            "pill_x": px(self.PILL_INSET),
            "text_x": px(self.TEXT_X),
            # 选项文字的左沿：**从竖条右沿再让出 MENU_TEXT_GAP**。
            #
            # 以前选项文字和字段文字共用 ``text_x``（9 逻辑像素），而竖条占的是
            # ``pad_h + pill_x .. + pill_w`` = 7..10 逻辑像素 —— 文字正好从竖条
            # **里面**起步（用户报的「竖线与文字过近，没有留出一定距离」；量真机
            # 截图：竖条 x=37..41，文字第 40 列就开始了，压在一起）。
            # 竖条是在「选项块内缩」之后再内缩的，所以这里必须按竖条算，不能拍一个
            # 固定的左边距。
            "menu_text_x": (px(self.PAD_H) + px(self.PILL_INSET) + px(self.PILL_W)
                            + px(self.MENU_TEXT_GAP)),
            "gap": px(self.GAP),
            # 面板的最小宽度：跟字段的最小宽度共用 ``MIN_W``（见 ``__init__``），
            # 否则会出现「面板比字段宽、右边探出一截」—— 用户报的对不齐。
            "min_w": px(self.MIN_W),
            "font": self._apply_font_scaling(font),
        }

    def _open(self) -> None:
        self._kill_closing()            # 上次收起的画布可能还在播动画，先让它立刻消失
        self._close(animate=False)      # 已经开着的面板直接收掉，不播动画
        # 别的下拉还开着 / 正在收的话，也立刻收掉 —— **必须赶在抓背景快照之前**。
        # 面板是主窗口里的子画布，``PrintWindow`` 抓图会把它们一起拍进去；而那张
        # 快照就是「露出带以外」看到的东西，于是别人的菜单会在这一块的展开/收起里
        # **原样重播一遍**（用户报的「刚关闭的菜单一部分又显示出来了」）。
        # 真机上点另一个下拉时正好撞上：Tk 先派发控件自己的绑定（打开新的）、再派发
        # toplevel 的绑定（收起旧的），抓快照时旧面板还明晃晃摆在那儿。
        self._dismiss_other_panels()
        dark = ctk.get_appearance_mode() == "Dark"
        self._palette = {
            "card": theme.color("layer", dark),
            "stroke": theme.color("layer_stroke", dark),
            "text": theme.color("text", dark),
            "accent": theme.color("accent", dark),
            "hover": theme.color("menu_hover", dark),
            "selected": theme.color("menu_selected", dark),
        }
        palette = self._palette
        geom = self._geom = self._geometry()
        # 面板宽度 = **字段自身的宽度**（物理像素）：这样面板与选框左右两条边严丝合缝，
        # 展开时看着就是「选框自己长开了」，而不是从旁边冒出一块更宽的菜单。
        # 字段在构造时已经按 ``MIN_W`` 兜过底（见 ``__init__``），所以 ``geom["min_w"]``
        # 在这里只是个兜底 —— 正常路径上 ``max`` 两边相等。
        width = max(self.winfo_width(), geom["min_w"])
        count = len(self._values)
        height = shape.list_height(count, geom["pad_v"], geom["row_h"],
                                   geom["item_gap"])

        host = self._host = self.winfo_toplevel()
        # 画布四周比面板各**多留一圈投影**（见类文档）。四边一样宽 —— 面板现在是「盖在
        # 字段上」的（选中那一行对齐字段中线，见 ``_anchor``），上下两边都可能落在面板外面，
        # 投影得照铺；早先面板挂在字段下方、上边只能探到字段底沿（再多就啃掉字段的底边
        # 描边），那条例外随定位方式一起作废。
        scale = self._get_widget_scaling()
        spread = max(0, round(self.SHADOW_SPREAD * scale))
        # 画布四周的留白要**装得下投影的尾巴**：σ = spread / SHADOW_BLUR，尾巴要到
        # 3σ 之外才看不见，留白只给 spread 的话尾巴会在画布边沿被硬切一刀 —— 面板
        # 四周那圈柔光的外沿变成一条直边（背景稍暗就看得出来，用户报的「阴影边缘
        # 不自然」）。所以留白单独按 ``icons.shadow_pad`` 算，比 spread 宽一圈。
        pad = self._shadow_pad = icons.shadow_pad(spread) if spread else 0
        top = self._shadow_top = pad

        # 行几何要**先于定位**算出来：面板按「选中那一行的中线 = 字段中线」摆，所以得先把
        # 那一行的中线在**面板坐标**里算好交给 ``_anchor``。
        current = str(self._var.get())
        bounds = shape.row_bounds(count, geom["pad_v"], geom["row_h"],
                                  geom["item_gap"])
        self._bounds = bounds
        index = self._values.index(current) if current in self._values else 0
        if bounds:
            pick = min(index, len(bounds) - 1)
            row_cy = bounds[pick][0] + geom["row_h"] // 2
        else:                                   # 一个选项都没有：拿面板中线兜底
            row_cy = height // 2
        self._row_cy = row_cy

        self._box, self._panel_box = self._anchor(host, width, height, geom, row_cy)
        box_x, box_y, box_w, box_h = self._box

        # 「露出带的原点」= 选中那一行在**画布坐标**里的中线（= 面板坐标 + ``top``）。
        # 面板按它摆过之后，这条线正好落在字段中线上 —— 第一帧那条退化的带子就压在字段
        # 正中间，往上下同时长开，把字段连同它上下那一片一起盖住。
        self._reveal_origin = top + row_cy
        # ``animate=False`` 不走动画，也就没有「中途」—— 底图一次贴满，不裁。
        self._reveal = self._reveal_band(0.0) if self._animate else None

        # 面板底图：先抓一帧主窗口内容，再把 Pillow 画的圆角面板（连同它四周的投影）
        # 叠上去 —— 圆角与投影露出来的就是**面板底下真实的样子**。Tk 的子控件没有
        # alpha，这些半透明只能预先合进底图里。
        #
        # ⚠️ 必须在**摆上面板之前**抓。面板是主窗口里的子控件，摆上去之后 ``PrintWindow``
        # 就会把面板自己一起画进来 —— 抓到「面板 + 面板」，叠完四角永远是一块方色的底
        # （和以前「烘焙抓图画布时抓到了上一帧的竖条」是同一类错误：抓图里不能有自己）。
        #
        # ⚠️ 抓之前还得把**刚拆掉的画布**那块补干净：``PrintWindow`` 给的是窗口
        # **上一次合成**的表面，``destroy()`` 之后要等事件循环跑一轮才刷新 —— 直接抓
        # 会把上一块菜单拍进「空背景」，之后每次动画都把它重播一遍（用户报的「刚关闭
        # 的菜单一部分又显示出来」）。用的是那块面板打开时存的 ``empty``（见 _note_patch）。
        backdrop = self._backdrop(host, box_x, box_y, box_w, box_h)
        backdrop = self._patch_backdrop(backdrop, box_x, box_y, box_w, box_h)

        canvas = tk.Canvas(host, width=box_w, height=box_h, bd=0,
                           highlightthickness=0, takefocus=0, cursor="hand2",
                           bg=theme.color("window", dark))
        self._popup = canvas
        self._menu_canvas = canvas

        # 面板底板用 Pillow 画（原因同字段底板）：Canvas 的圆角是折线拼的、
        # 描边沿路径居中画会被画布边界裁掉半条，四条边粗细不一
        border = max(1, round(self.FIELD_BORDER * scale))
        photos = self._photos = {}
        self._panel_img = panel = icons.rounded_rect(
            (width, height), radius=geom["radius"],
            fill=palette["card"], stroke=palette["stroke"], border=border)
        # 圆角外面 / 投影底下那几块用背景快照填；拿不到快照就填窗口底色（顶多四周有一圈
        # 色差）。这里**可以**用 ``Image.alpha_composite``：``rounded_rect`` 与
        # ``soft_shadow`` 出的都是直通 alpha，正是它的公式（预乘那套才对不上，见 win32 模块）。
        if backdrop is None:
            # ``Image.new`` 本身就吃 "#RRGGBB"（alpha 补 255），别再手工拼元组 ——
            # 以前那行写的是 ``color + (255,)``，字符串加元组，一走到这条分支就崩。
            empty = Image.new("RGBA", (box_w, box_h), theme.color("window", dark))
        else:
            empty = backdrop.convert("RGBA")
        plain = empty.copy()                    # 只有面板，四周还是干净的背景
        plain.alpha_composite(panel, (pad, top))
        full = plain                            # 面板 + 四周投影（没有投影时两者是同一张）
        if pad:
            # 投影按「面板居中、四周留足 pad」生成 —— 尾巴在画布边沿已经衰减到 0
            # （见 icons.shadow_pad），所以整张直接合，不用再裁。
            shadow = icons.soft_shadow((box_w, box_h), radius=geom["radius"],
                                       spread=spread, pad=pad,
                                       opacity=self.SHADOW_OPACITY)
            full = empty.copy()
            full.alpha_composite(shadow)
            full.alpha_composite(panel, (pad, top))
        self._panel_photo = ImageTk.PhotoImage(full.convert("RGB"))
        # 底图图元：整块画布一张图，**动画全程只换内容、不换位置也不换大小**
        # （换的只有 body_key 指向的那张，见 _paint）。
        body_item = canvas.create_image(0, 0, anchor="nw", image=self._panel_photo)

        # 动画要用的三张源图：**还没有面板的世界** / **只有面板** / **面板 + 投影**。
        # 带子把画布切成三段：带外两段贴背景快照、带内那一段贴「按进度混过」的底图 ——
        # 三块正好铺满画布，一帧只上传画布那么大的一片像素（见 _paint）。
        # ``animate=False`` 时不建这些 —— 那就一张静态图到底，一帧都不多混。
        self._fade = None
        if self._animate:
            self._fade = {
                "size": (box_w, box_h),     # 露出带按它夹边界（见 _paint）
                "empty": empty.convert("RGB"),
                "plain": plain.convert("RGB"),
                "full": full.convert("RGB"),
                "body": body_item,          # 底图图元（位置与尺寸全程不动）
                "body_key": "full",         # 它现在贴的是哪张
                "base": "empty",            # 底图按哪个 key 掺（投影那段掺 plain，见 _paint）
                "body_photo": None,         # 这一帧底图的 Tk 引用（不留就被 GC 成白板）
                "body_shown": None,         # 底图图元当前贴的是哪张 PhotoImage（省重复 itemconfigure）
                "tiles": [None, None, None],      # 带外 / 带内 / 带外 三块（建完内容图元再补）
                "tile_photos": [None, None, None],
                "band": None,               # 这一帧的露出带（None = 不裁）
                "mix": 0.0,                 # 带内混进多少 ``empty``（1 = 一点面板都看不见）
                "photos": {
                    "empty": ImageTk.PhotoImage(empty.convert("RGB")),
                    "plain": ImageTk.PhotoImage(plain.convert("RGB")),
                    "full": self._panel_photo,
                },
            }
            # 先摆成「完全看不见」，免得展开前闪一下满不透明面板
            self._fade_alpha = 0.0
            self._shadow_alpha = 0.0
        else:
            # 不走动画：底图贴的本来就是最终那张（面板 + 投影），两个透明度直接给满
            self._fade_alpha = 1.0
            self._shadow_alpha = 1.0

        current = str(self._var.get())
        self._rows, self._pills, self._picked = [], [], []
        self._hover = -1
        # 选项块与选中竖条也出成图：Canvas 多边形没有抗锯齿，圆角会发毛。
        # 悬停与当前值是**同一个色值**，所以整份列表只需要一张块图、一张竖条图。
        self._block_img = icons.rounded_rect(
            (width - 2 * geom["pad_h"], geom["row_h"]),
            radius=geom["item_r"], fill=palette["hover"])
        photos["block"] = ImageTk.PhotoImage(self._block_img)
        self._pill_img = icons.rounded_rect(
            (geom["pill_w"], geom["pill_h"]), radius=geom["pill_r"],
            fill=palette["accent"])
        photos["pill"] = ImageTk.PhotoImage(self._pill_img)

        # ⚠️ 图元顺序就是叠放顺序：**色块在下、文字在上**。反过来的话「当前值」和
        # 指针划过的那一项，整条文字都会被色块盖住（用户报的「选中选项无法显示文字」）。
        # 所以先建完所有色块与竖条，最后再单独过一遍建文字。
        #
        # ⚠️ 图元的坐标是**画布坐标**（= 面板坐标 + ``(pad, top)``）—— 面板四周还围着
        # 一圈投影留白，画布原点在面板左上角之外。``self._bounds`` / ``geom`` 一律仍是
        # **面板坐标**（它们是「面板里面怎么摆」），两边差这一个偏移，别混。
        pill_x0 = geom["pad_h"] + geom["pill_x"]
        for index, (y0, _y1) in enumerate(bounds):
            value = self._values[index]
            cy = top + y0 + geom["row_h"] // 2
            self._picked.append(value == current)
            self._rows.append(canvas.create_image(
                pad + geom["pad_h"], cy, anchor="w", image=photos["block"],
                tags=self.CONTENT_TAG))
            # 选中竖条只属于「当前值」——悬停时不会冒出来（那会看着像被选中）
            self._pills.append(canvas.create_image(
                pad + pill_x0, cy, anchor="w", image=photos["pill"],
                tags=self.CONTENT_TAG))
        for index, (y0, _y1) in enumerate(bounds):
            canvas.create_text(pad + geom["menu_text_x"], top + y0 + geom["row_h"] // 2,
                               text=self._values[index], anchor="w",
                               font=geom["font"], fill=palette["text"],
                               tags=self.CONTENT_TAG)

        # 三块「拼贴」：带子把画布切成三段，带外两段贴**背景快照**（连同压在上面的
        # 文字 / 色块 / 竖条一起盖回背景），带内那一段贴底图。三块正好铺满画布，
        # 于是每帧要传的像素总量**不多于一块画布** —— 这就是「画布窗口不动也能裁」。
        #
        # ⚠️ 三段必须是**裁好的图**，不能靠「一张整图挪锚点 + 让画布边沿去裁」：图元
        # 显示的是整张图，挪锚点只是让它错位，露出来的那截内容是**对不上号**的（试过：
        # 底部那条把背景图的前 211 行搬到了下面，屏幕上是一块错位的白框）。
        #
        # 为什么不能每帧 ``place`` 那个窗口来裁（更早的做法）：Windows 会把旧像素整块
        # 搬过去、等下一次重画才补，屏幕上看就是**撕裂 / 上下跳**，投影也跟着被搬歪。
        tile_items = [canvas.create_image(0, 0, anchor="nw", state="hidden")
                      for _ in range(3)]
        # ⚠️ 三块的下标有讲究：``[0]`` 带顶以上、``[1]`` 带内、``[2]`` 带底以下。
        # **带内那块是「面板底子」，必须压在内容图元下面**（不然它会把文字整个盖掉 ——
        # 屏幕上看着就是「面板长出来了、里面一个字都没有」，几何断言全绿也照样错）；
        # 带外那两块是「背景遮盖」，要压在内容图元**上面**才盖得住文字。
        canvas.tag_lower(tile_items[1], self.CONTENT_TAG)
        if self._fade is not None:
            self._fade["tiles"] = tile_items
            # 第一帧：底图是实心的面板，带子还只是一条线（裁得只剩 1px，屏幕上看不见）
            self._paint("plain", self._reveal, 0.0)
        # ⚠️ 画布**一次就摆满**（位置、尺寸都是最终的），整段动画都不再动它 —— 每帧
        # 去 `place` 那个窗口，Windows 会把旧像素整块搬走、等下一次重画才补，屏幕上就是
        # 撕裂 / 上下跳（见 ``_paint``）。放在最后摆：这样第一次上屏就带着底图与拼贴，
        # 不会先闪一下画布底色。
        canvas.place(x=box_x, y=box_y, width=box_w, height=box_h)
        # ⚠️ 把画布抬到兄弟控件上面，只能**显式走 `tkinter.Misc`**：
        # ``tk.Canvas`` 把 ``lift`` / ``tkraise`` 两个名字**都**重定向成了 ``tag_raise``
        # （画布图元的叠放顺序），直接写 ``canvas.lift()`` / ``canvas.tkraise()`` 都会抛
        # ``wrong # args: should be "raise tagOrId ?aboveThis?"`` —— 库注释里也写着
        # 「the preferred name for them is tag_lower / tag_raise」，就是别再碰这两个别名。
        tk.Misc.tkraise(canvas)
        self._repaint()                     # 按「当前值 / 悬停」定一次色块与竖条的显隐

        canvas.bind("<Motion>", self._on_motion)
        canvas.bind("<Leave>", lambda _e: self._hover_to(-1))
        canvas.bind("<Button-1>", self._on_press)
        # ⚠️ 收面板必须挂在**松手**上，不能挂在按下上（原因见 ``_on_release``）。
        canvas.bind("<ButtonRelease-1>", self._on_release)

        root = host
        self._add_binding(root, "<Button-1>", self._maybe_close)
        self._add_binding(root, "<Escape>", lambda _e: self._close())
        self._add_binding(root, "<FocusOut>", self._on_root_focus_out)
        self._add_binding(root, "<Unmap>", self._on_host_unmap)
        self._add_binding(root, "<Configure>", self._on_host_configure)

        # 展开动画**放在最后**：前面的图元与绑定都得先生效 —— 展开途中指针划过去
        # 依然要能正常高亮（内容图元位置一直没动过，见 ``_paint`` 与类文档）。
        # 投影的淡入挂在 ``on_done`` 上，是紧接着的第二段（见 ``_fade_shadow_in``）。
        if self._animate:
            self._motion.start(self.EXPAND_MS, self._frame_expand,
                               self._fade_shadow_in)

    # ---------------------------------------------------------------- 动画
    def _reveal_band(self, s: float) -> tuple:
        """露出带：进度 ``s`` 时画布上「已经看得见」的那一段 ``(顶边, 底边)``。

        原点 ``_reveal_origin`` 是**选中那一行的中线**（画布坐标）。面板是按「这一行的
        中线 == 字段中线」摆的（见 ``_anchor``），所以原点在屏幕上也正落在字段中线 ——
        带子从那里向上、下**两侧同时**长出去：``s=0`` 是一条退化的线、``s=1`` 就是整块
        画布，也就是 Win11 浮出控件那种「以选中项为中心向两侧延展、顺手盖住字段」。

        两个端点其实是**画布的两条边**而不是原点自身（原点只有在上下都有富余时才是
        对称的）：上沿 ``lerp(原点, 0, s)``、下沿 ``lerp(原点, 满高, s)``，于是带子
        高度恰好是 ``s × 满高``（选中项靠上时看着仍然像「往下长」）。

        为什么把算法放在这一个函数里：展开与收起必须**逐帧镜像**（收起喂
        ``1 - soft_in_out(p)``），两边共用一份算式就不可能画歪。

        它自己只算数字，落地由 ``_paint`` 的三段拼贴做；``_bounds``
        与命中判定按**面板坐标**算，图元坐标则一律是画布坐标（面板坐标 + ``_shadow_top``）。
        """
        full = self._box[3]
        if full <= 0:
            return (0, 0)
        s = shape.clamp01(s)
        origin = min(max(int(self._reveal_origin), 0), full)
        top = round(shape.lerp(origin, 0.0, s))
        bottom = round(shape.lerp(float(origin), float(full), s))
        if bottom - top < 1:            # s≈0 时留一行：crop / paste 不接受空矩形
            bottom = top + 1
        return (max(0, top), min(full, bottom))

    def _reveal_span(self) -> tuple:
        """当前露出带的 ``(顶边, 底边)``（画布坐标；没裁剪时就是整块画布）。

        给真机脚本量动画的中间态用（``_reveal`` 用 ``None`` 表示「全都露出来了」，
        这里统一成一个矩形）。
        """
        if self._reveal is None:
            return (0, self._box[3])
        return self._reveal

    def _target(self):
        """当前该往哪块画布上画：面板自己的那块，或者正在播收起动画的那块。"""
        if self._menu_canvas is not None and self._fade is not None:
            return self._menu_canvas, self._fade
        if self._closing is not None and self._closing_fade is not None:
            return self._closing, self._closing_fade
        return None, None

    def _paint(self, body_key: str, band: tuple | None = None,
               mix: float = 0.0, base_key: str = "empty") -> None:
        """把画布画成「``body_key`` 那张底图（按 ``mix`` 掺 ``base_key``）+ 带子三段拼贴」
        —— 一帧的全部绘制都在这里。

        底图有三张：``plain``（只有面板）/ ``full``（面板 + 投影），外加背景快照
        ``empty``（还没有面板）。带子把画布竖着切成三段，三段各贴各的、正好铺满：

        | 段 | 内容 | 为什么 |
        |---|---|---|
        | ``[0, 带顶)`` / ``[带底, 高)`` | 背景快照 ``empty`` | 把底图和内容图元一起盖回背景 |
        | ``[带顶, 带底)`` | ``base_key`` 与 ``body_key`` 按 ``mix`` 混过的那一条 | 面板「长出来」+ 淡入 |

        ``mix`` = 混进多少 ``base_key``：``1`` 是 ``base_key`` 那张（起步时 ``base`` 给
        ``empty``，于是面板一点也看不见），``0`` 是纯 ``body``（落地）。所以淡入与裁剪
        在同一个算式里，不用第二层图元。

        ⚠️ ``base_key`` 是给**投影淡入**用的：那一段要让「面板不动、只有四周的柔光由
        无到有」，所以混的是 ``plain``（面板、没投影）→ ``full``（面板 + 投影）。早先
        这里一律混 ``empty``，于是投影段的第一帧把 ``mix`` 设成 1 —— 整块面板（连底带字
        的底子）**瞬间变成背景**，再花 150ms 淡回来。屏幕上就是「菜单先整个闪没、再浮
        出来」一下，用户报的「打开频闪」就是它（实测：105ms 处面板色 251，113ms 处
        直接掉成 26 = 底下的窗口文字）。

        ⚠️ **全程不动画布窗口**（位置、尺寸一次摆好就不再碰）。早先靠每帧 ``place``
        把窗口夹到带子上，Windows 会把旧像素整块搬过去、等下一次重画才补上 —— 屏幕
        上就是**撕裂 / 上下跳**，而且阴影跟着被搬歪。现在裁的是这三段拼贴。

        ``band=None`` 表示「整块都露出来、不裁」：拼贴全部隐藏，底图整张换一次 ——
        投影淡入那段走的就是它（内容图元的浓淡一点不受影响）。

        为什么不上「一层全画布 RGBA 盖帘」：它一次 alpha 混合就能同时做掉裁剪与淡入、
        代码最短，但每帧要重传一张**带 alpha** 的整图，真动画里实测比这里慢一截
        （alpha 图在画布上还要逐像素混合一次），展开从 15 帧掉到 7 帧的样子。
        """
        canvas, fade = self._target()
        if fade is None:
            return
        fade["body_key"], fade["band"], fade["mix"] = body_key, band, mix
        fade["base"] = base_key            # frame_image() 要按同一套算式重放
        w, h = fade["size"]
        tiles, photos = fade["tiles"], fade["tile_photos"]
        if tiles[0] is None:                # 还没建（animate=False）
            return
        if band is None:
            # 不裁：一张整图，把拼贴全收掉（稳定态必须与 panel_image() 逐像素一致）
            base = fade[body_key]
            amount = shape.clamp01(mix)
            img = (Image.blend(fade[base_key], base, 1.0 - amount)
                   if amount > 0.0 else None)
            if img is None:
                photo = fade["photos"][body_key]
            else:
                # ⚠️ PhotoImage 必须存回字典**拎住引用** —— 就地丢掉的话下一帧就被
                # 回收，画布上那块会闪成白板（与收起动画留 ``photos`` 同因）。
                photo = fade["body_photo"] = ImageTk.PhotoImage(img)
            if fade.get("body_shown") is not photo:
                self._set_item(canvas, fade["body"], image=photo)
                fade["body_shown"] = photo
            self._set_item(canvas, fade["body"], state="normal")
            for i, item in enumerate(tiles):
                self._set_item(canvas, item, state="hidden")
                photos[i] = None
            return

        top = max(0, min(int(band[0]), h))
        bottom = max(top, min(int(band[1]), h))
        # 底图留在下面当兜底（拼贴是它上面的一层），不收上传
        want = fade["photos"][body_key]
        if fade.get("body_shown") is not want:
            # 一帧里重复 ``itemconfigure`` 成同一张图是白花的 Tk 调用，记一下当前贴的是谁
            self._set_item(canvas, fade["body"], image=want)
            fade["body_shown"] = want
        self._set_item(canvas, fade["body"], state="normal")
        # ⚠️ 只混「带子那一条」，不是整块画布：``Image.blend`` 是按像素数算的，
        # 展开前半程带子只有小半块，混整张等于白算一半以上（每帧实测省 0.4~0.9ms，
        # 而帧间隔只有 6ms，省下来的直接变成更匀的帧）。
        amount = 1.0 - shape.clamp01(mix)
        inner = None
        if bottom > top:
            box = (0, top, w, bottom)
            # 纯裁剪的那条路（展开时 ``mix`` 恒为 0）直接裁，不做恒等混合 ——
            # ``Image.blend(a, b, 1.0)`` 也是一次全量拷贝，每帧白花 0.3ms 左右。
            inner = (fade[body_key].crop(box) if amount >= 1.0 else
                     Image.blend(fade[base_key].crop(box), fade[body_key].crop(box),
                                 amount))
        pieces = (
            (0, 0, top, 0),
            (0, top, bottom, top),
            (0, bottom, h, bottom),
        )
        for i, (x0, y0, y1, y) in enumerate(pieces):
            if y1 <= y0:
                self._set_item(canvas, tiles[i], state="hidden")
                photos[i] = None
                continue
            src = inner if i == 1 else fade["empty"].crop((x0, y0, w, y1))
            photos[i] = ImageTk.PhotoImage(src)
            self._set_item(canvas, tiles[i], image=photos[i])
            try:
                canvas.coords(tiles[i], x0, y)
            except Exception:
                return
            self._set_item(canvas, tiles[i], state="normal")

    @staticmethod
    def _set_item(canvas, item, **config) -> None:
        """``itemconfigure`` 的静默版：收起动画末尾画布可能已经没了，不该炸。"""
        try:
            canvas.itemconfigure(item, **config)
        except Exception:
            pass

    def _frame_expand(self, progress: float) -> None:
        """展开中的一帧：露出带从「当前值那一行」长到整块画布 —— **只裁剪，不淡入**。

        曲线用 ``soft_in_out``（两端速度为 0）：起步不猛冲，所以每一帧的增量都均匀。
        早先那条 ``ease_out_cubic`` 一上来就是 3 倍速度，16ms 一帧的话第一帧就窜掉
        一大截，看着是「弹开」而不是「长出来」；它和开关那条 ``fast_out_slow_in``
        （半程走完 97%）都有这个毛病，所以这里两条都没用。

        ⚠️ **面板全程是实心的**（``mix`` 恒为 0）。早先这一帧还给底图配了个「由透明
        淡入」（``1 - eased``），想法是让带子边缘不那么硬 —— 但底图淡、文字却是画布
        图元（全不透明），于是展开前半程屏幕上就是「几个数字浮在窗口按钮上、面板只有
        一层薄雾」，用户报的「不平滑 / 频闪」拍到的正是这个（实测 40ms 时面板只有
        16% 不透明、40~90ms 之间还能读出底下的按钮文字）。Win11 的浮出控件本身也是
        **纯裁剪**（clip reveal），不带这一层淡入。
        """
        eased = shape.soft_in_out(progress)
        self._reveal = self._reveal_band(eased)
        self._fade_alpha = 1.0          # 面板一直实心，只被带子裁（见上面那段）
        self._paint("plain", self._reveal, 0.0)

    def _fade_shadow_in(self) -> None:
        """展开动画的收尾：露出带铺满（不再裁剪），再让四周的投影淡入。

        两件事必须按这个顺序：投影是**整张**画在底图上的，带子还没铺满时那圈柔光会被
        裁掉上下两条、贴在带的边缘上像一道切口。挂在 ``_Motion`` 的 ``on_done`` 上
        （而不是展开进度里），见类文档：投影只有在面板整个露出来之后才有意义。
        """
        self._reveal = None
        if not self._shadow_pad:
            self._shadow_alpha = 1.0
            self._paint("full")
            return
        self._motion.start(self.SHADOW_MS, self._frame_shadow)

    def _frame_shadow(self, progress: float) -> None:
        """投影淡入的一帧：面板保持不变，四周那圈柔光由透明加到满。

        混的是 ``plain``（面板、没投影）→ ``full``（面板 + 投影），**不是**混背景快照 ——
        面板本身在这 150ms 里一个像素都不该变（早先混 ``empty``，第一帧整块面板直接
        变透明，屏幕上就是「打开时闪一下」，见 ``_paint`` 的 ``base_key``）。
        """
        eased = shape.soft_in_out(progress)
        self._shadow_alpha = eased
        # 投影只有一段「从无到有」，不需要裁内容 —— 不带带子，字一个像素都不动。
        self._paint("full", None, 1.0 - eased, base_key="plain")

    def frame_image(self):
        """画布上**当前这一帧**的 PIL 图（RGB），给自检 / 抽帧工具用。

        和 ``panel_image()`` 的区别：那个抓的是**屏幕**（``PrintWindow``），会带上窗口
        非客户区的偏移、也要求窗口已经画出来；这个是按 ``_paint`` 记下的那几个参数
        **现算**出来的一张（0,0 = 画布左上角），动画中途也拿得到 —— 验「露出带以外
        是不是真的换回背景」这类逐像素断言只能靠它。没开动画（``animate=False``）
        或还没铺底图时返回 ``None``。

        为什么现算而不是每帧存一张：屏幕上那张其实是「一张混过的底图 + 两条盖条」，
        真要一张合成图得再拷一次（白花的）。工具要的时候按同样的参数重放一遍就行 ——
        重放的算式与 ``_paint`` 逐字一致：先按 ``mix`` 混底图，再把带外两段换回背景。
        """
        _canvas, fade = self._target()
        if fade is None:
            return None
        w, h = fade["size"]
        base = fade[fade["body_key"]]
        band, mix = fade["band"], shape.clamp01(fade["mix"])
        under = fade.get("base", "empty")
        out = (Image.blend(fade[under], base, 1.0 - mix) if mix > 0.0 else base)
        if band is None:
            return out
        top = max(0, min(int(band[0]), h))
        bottom = max(top, min(int(band[1]), h))
        if top == 0 and bottom == h:
            return out
        out = out.copy()
        if top > 0:
            out.paste(fade["empty"].crop((0, 0, w, top)), (0, 0))
        if bottom < h:
            out.paste(fade["empty"].crop((0, bottom, w, h)), (0, bottom))
        return out

    def _settle(self) -> None:
        """把动画**立刻**推到终点（露出带铺满 + 投影全亮 / 收起直接结束）。

        给工具脚本用：截图和像素断言要的是「稳定态」，不能抓在展开中途。
        真人用不到它 —— 动画会自己跑完（包括投影那一段）。
        """
        self._motion.cancel()
        if self._popup is not None:
            self._reveal = None             # 别再裁了：整块画布都露出来
            self._fade_alpha = 1.0
            self._shadow_alpha = 1.0
            self._paint("full")             # 盖条收掉，只剩「面板 + 投影」那张静态图
            return
        self._kill_closing()

    def _kill_closing(self) -> None:
        """让「正在播收起动画」的那块画布立刻消失（幂等）。"""
        canvas, self._closing = self._closing, None
        fade = self._closing_fade
        self._closing_photos = {}
        self._closing_fade = None
        if canvas is None:
            return
        self._note_patch(self._box, fade)
        try:
            canvas.place_forget()
        except Exception:
            pass
        try:
            canvas.destroy()
        except Exception:
            pass

    def _note_patch(self, box, fade) -> None:
        """记下「这块面板底下原本长什么样」——就是它打开时存的 ``empty``。

        这张图是**面板出现之前**抓的，天然干净；而 ``PrintWindow`` 在子窗口被拆掉之后
        还会返回**上一次合成**的表面（实测：``destroy()`` 之后立刻抓，菜单原样在图上，
        要等事件循环跑一轮才刷新，``update_idletasks`` / ``RedrawWindow`` / ``DwmFlush``
        都不行）。所以与其去逼系统重画，不如直接用这张存好的干净背景把快照补回来。
        """
        if fade is None or box is None or box[2] <= 0 or box[3] <= 0:
            return
        try:
            self._stale_patches.append((tuple(box), fade["empty"].convert("RGB")))
        except Exception:
            return

    def _patch_backdrop(self, backdrop, x, y, w, h):
        """用 ``_stale_patches`` 里存的干净背景，补掉快照中被拆掉的旧面板。

        ``backdrop`` 与补丁都是**主窗口客户区坐标**、同一套物理像素，所以只需按矩形
        求交、对齐裁剪。用完即弃：补丁只对「这一次抓图」有意义。
        """
        patches, self._stale_patches = self._stale_patches, []
        if backdrop is None:
            return None
        for (px, py, pw, ph), img in patches:
            x0, y0 = max(x, px), max(y, py)
            x1, y1 = min(x + w, px + pw), min(y + h, py + ph)
            if x1 <= x0 or y1 <= y0:
                continue
            try:
                piece = img.crop((x0 - px, y0 - py, x1 - px, y1 - py))
                backdrop.paste(piece, (x0 - x, y0 - y))
            except Exception:
                continue
        return backdrop

    def _dismiss_other_panels(self) -> None:
        """把**同一个窗口里**其它下拉的面板立刻收掉（不播动画）。

        为什么非要赶在抓背景快照之前：面板是主窗口里的子 ``tk.Canvas``，而底图是
        用 ``PrintWindow`` 抓的 —— 子窗口会一起进画面。那张「空背景」快照会被当作
        露出带以外的遮挡层反复显示，于是**别人刚关掉的菜单会被一字不差地重播**，
        看着就是「菜单关掉之后又冒出来一块」。

        只处理同一个 toplevel 的：不同顶层窗口各有各的画布，拍不到一起。
        """
        mine = self.winfo_toplevel()
        for other in list(_LIVE_COMBOS):
            if other is self:
                continue
            if other._popup is None and other._closing is None:
                continue
            try:
                if other.winfo_toplevel() is not mine:
                    continue
                # 它打开时存的干净背景先收过来，等会儿补快照用（见 _patch_backdrop）
                self._stale_patches.extend(other._stale_patches)
                other._stale_patches = []
                self._note_patch(other._box, other._fade or other._closing_fade)
                other._close(animate=False)
            except Exception:
                continue

    # ---------------------------------------------------------- 摆在哪儿
    def _anchor(self, host, width: int, height: int, geom: dict, row_cy: int) -> tuple:
        """算出面板（以及包着它的画布）在主窗口**客户区**里的位置，返回两个矩形。

        - 第一个是**画布**矩形 ``(x, y, w, h)`` —— 面板四周还多一圈投影留白；
        - 第二个是**面板**矩形 ``(x, y, w, h)`` —— 用户看到的那块列表。

        面板是主窗口里的子控件，所以这里算的是「相对客户区」的坐标 —— 与屏幕、与任务栏
        都无关（以前那版是 ``Toplevel``，得按屏幕摆，还得把 DPI 虚拟化过的屏高还原成物理值）。

        坐标一律**物理像素**：画布是原生 ``tk.Canvas``，它的 ``place()`` **不走**
        CustomTkinter 的 DPI 换算（CTk 只缩放它自己创建的控件），而
        ``winfo_rootx/rooty/width/height`` 给的都是物理值，正好对得上。

        **竖直位置 = 「选中那一行对齐字段」**（``row_cy`` 是那一行在面板坐标里的中线）：
        面板往下挪到「字段的中线 == 那一行的中线」为止。于是面板**上下两头都伸到字段
        外面**，把字段连它上下的邻居一起盖住；展开时那条露出带正好从字段正中往两头长。
        这是 Win11 的 ComboBox 浮出控件的行为（拿系统设置那个「缩放」下拉逐像素量过：
        选中项那一行的中线与字段中线重合，误差 0）。

        横向仍旧对齐字段左沿；超出客户区时先往左收。竖直方向两头都夹进客户区 ——
        面板本来就在窗口里（子控件超出客户区的部分会被窗口裁掉），夹完就不再保证
        「选中项压在字段上」了，那属于窗口太矮，只能这样。越界是用**画布**判的：它比
        面板大一圈，按面板判的话那圈投影会被窗口切掉。
        """
        pad, top = self._shadow_pad, self._shadow_top
        # 边距按**面板**算（用户看到的是面板离窗口边有多远），画布那圈留白不占额度，
        # 所以这里先减掉 pad；减到负数就归零 —— 宁可切掉一点投影，也不让面板贴边。
        margin = max(0, round(self.SCREEN_MARGIN * (self._get_widget_scaling() or 1.0)) - pad)
        box_w, box_h = width + 2 * pad, top + height + pad

        hx, hy = host.winfo_rootx(), host.winfo_rooty()
        hw, hh = host.winfo_width(), host.winfo_height()
        fx = self.winfo_rootx() - hx
        # 字段的中线（客户区坐标）—— 选中那一行要压在它上面
        field_cy = self.winfo_rooty() - hy + self.winfo_height() // 2
        fy = field_cy - row_cy
        bx, by = fx - pad, fy - top
        if bx + box_w > hw - margin:
            bx = max(margin, hw - margin - box_w)
        lo, hi = margin, hh - margin - box_h
        by = lo if hi < lo else max(lo, min(by, hi))
        return (bx, by, box_w, box_h), (bx + pad, by + top, width, height)

    def _backdrop(self, host, x: int, y: int, width: int, height: int):
        """抓一帧主窗口内容，裁出面板要盖住的那块背景（拿不到返回 ``None``）。

        用途见 ``_open``：它当画布底图，四角就露出被盖住的东西而不是一块方色。

        全黑一律按失败处理：面板底（浅色 ``#FFFFFF`` / 深色 ``#2B2B2B``）和窗口底
        （``#F3F3F3`` / ``#202020``）都不是纯黑，所以「整块 (0,0,0)」只可能是窗口还没
        画出来时 ``PrintWindow`` 返回的空位图。
        """
        if not win32.available():
            return None
        if width <= 0 or height <= 0:
            return None
        try:
            # 只抓要盖住的那一块（客户区坐标）—— 整窗抓回来再裁，等于白白读回五倍大的
            # 位图：实测整窗 6ms / 这一块 1ms，而这一步是**点开下拉时的同步等待**
            # （见 _open），省下来的都是「点下去到面板开始动」之间的手感。
            crop = win32.grab(host, (x, y, width, height))
        except Exception:
            return None
        if crop is None or crop.size != (width, height):
            return None
        if crop.getextrema() == ((0, 0), (0, 0), (0, 0)):
            return None
        return crop

    def panel_image(self, with_shadow: bool = False):
        """面板**当前显示出来的那张图**（RGB，含它盖住的背景），给截图 / 自检工具用。

        面板是主窗口里的子控件，``PrintWindow`` 抓主窗口时本来就带上了它 —— 按相对
        客户区的位置裁一块就行。收起状态或抓不到时返回 ``None``。

        ⚠️ 默认只裁**面板本身**那一块。画布比面板四周大一圈（投影留白），按画布裁的话
        所有用面板坐标写的像素断言（四角、色块、文字、竖条）会整整齐齐地错位一个
        ``_shadow_pad``。要连投影一起看就传 ``with_shadow=True``——「四周真的亮了那圈
        柔光」这类断言用得上。
        """
        canvas, host = self._menu_canvas, self._host
        if canvas is None or host is None:
            return None
        try:
            shot = win32.grab(host)
        except Exception:
            return None
        if shot is None:
            return None
        x = canvas.winfo_rootx() - host.winfo_rootx()
        y = canvas.winfo_rooty() - host.winfo_rooty()
        w, h = canvas.winfo_width(), canvas.winfo_height()
        if not with_shadow:
            pad, top = self._shadow_pad, self._shadow_top
            x, y = x + pad, y + top
            w, h = w - 2 * pad, h - top - pad
        if (w <= 0 or h <= 0 or x < 0 or y < 0
                or x + w > shot.size[0] or y + h > shot.size[1]):
            return None
        return shot.crop((x, y, x + w, y + h))

    # ---------------------------------------------------------- 主窗口动了就收起
    def _on_host_configure(self, event) -> None:
        """主窗口被缩放（或挪动）就把面板收起来。

        面板的位置是「按字段算出来再 ``place`` 的」，不会自己跟着字段走 —— 主窗口一变，
        它就浮在错误的位置上。子控件还有一个更麻烦的地方：窗口变小的话面板会被裁掉一截。

        ⚠️ **必须判 ``event.widget is self._host``**：Tk 的 bindtag 里有 toplevel 那一档，
        所以**子控件**的 ``<Configure>`` 也会调到这里绑在顶层窗口上的处理函数 ——
        不判的话，面板自己 ``place`` 上去时那一下 ``<Configure>`` 就会把它立刻收掉。
        """
        if self._popup is not None and event.widget is self._host:
            self._close()

    def _on_host_unmap(self, event) -> None:
        """主窗口最小化后收起（同样是子控件无关的 ``<Unmap>`` 会带过来，要判来源）。"""
        if self._popup is not None and event.widget is self._host:
            self._close()

    # ---------------------------------------------------------- 指针
    def _index_at(self, y: int) -> int:
        """画布坐标 → 行号。⚠️ 指针给的是**画布**坐标，行几何是**面板**坐标 ——
        面板上面还压着 ``_shadow_top`` 那么高的投影留白，先减掉才对得上。

        画布窗口动画全程不动（见 ``_paint``），所以这里的坐标不需要任何补偿 ——
        展开途中指针划过，命中的就是屏幕上那一行。

        但**带子外的那几行还没露出来**：那时它们被背景盖条压着，看着根本不存在，点中它们
        会很突兀（用户按着字段往下一划就选中了还没长出来的项）。所以带子外的坐标直接
        不认。``_reveal is None``（动画收尾 / 关着）时与静态态完全一致。
        """
        geom = self._geom
        if geom is None:
            return -1
        if self._reveal is not None and not (self._reveal[0] <= y < self._reveal[1]):
            return -1
        return shape.row_index(y - self._shadow_top, geom["pad_v"],
                               geom["pitch"], len(self._values))

    def _on_motion(self, event) -> None:
        self._hover_to(self._index_at(event.y))

    def _hover_to(self, index: int) -> None:
        if index == self._hover or self._menu_canvas is None:
            return
        self._hover = index
        self._repaint()

    def _repaint(self) -> None:
        """切色块 / 竖条的显隐：悬停和当前值本来就是同一个底色，不需要换颜色。

        面板是主窗口里的普通画布图元，改显隐只是一次 ``itemconfigure``，不用碰窗口
        （以前分层窗口那条路每动一下都要 ``UpdateLayeredWindow`` 重发整张位图）。
        """
        canvas = self._menu_canvas
        if canvas is None:
            return
        for index, picked in enumerate(self._picked):
            visible = picked or index == self._hover
            canvas.itemconfigure(self._rows[index],
                                 state="normal" if visible else "hidden")
            # 竖条只跟「当前值」走，悬停不给（不然看着像被选中）
            canvas.itemconfigure(self._pills[index],
                                 state="normal" if picked else "hidden")

    def _on_press(self, event) -> None:
        """按下：只负责「选中哪一项」，**绝不在这里收面板**（原因见 ``_on_release``）。"""
        index = self._index_at(event.y)
        if index >= 0:
            self._choose(self._values[index])
        # 点在列表留白处：什么都不做，等松手那一下统一收（按下就收会把 release 漏下去）

    def _on_release(self, event) -> None:
        """松手：**这一下必须由面板自己接住**，接住了才收。

        用户反馈：「当下拉菜单的选项下方覆盖着按钮后，点击选项会顺带点击下方按钮」。

        真因不是"收得太早"，而是**收在了按下的那一刻**：一次点击 = 两个独立事件，
        ``<Button-1>`` 和 ``<ButtonRelease-1>`` 之间隔着「用户还按着」的那段时间。
        只要在按下时就把面板 ``destroy()``（哪怕是排到 ``after_idle`` —— 事件队列一空
        它就跑了，物理松手还没到），松手那一次的 release 就落到**面板底下那个控件**上。
        而 ``CTkButton`` 的 command 恰恰是在 release 里触发的
        （customtkinter ``_on_release``：``if self._mouse_inside ... self._command()``），
        面板展开正好压着按钮行 → 选一次下拉就把下面的按钮点了一次。

        实测（``tools/check_option_click.py`` 合成真鼠标：按下 → 跑 120ms 事件循环 → 松开）：
        旧实现里「按住」的那 120ms 内面板已经没了（``panel alive while held = False``），
        松手直接落在按钮上，command 被触发。

        ⚠️ 面板现在是主窗口里的**子控件**，跟按钮是同一个窗口里的兄弟 —— 以前那个
        ``Toplevel`` 版本至少还有「另一个窗口」这层隔离，现在是**一层都没有了**，
        所以只能靠「松手时面板还活着、由它吃掉这次 release」来挡。
        """
        self._close_soon()

    # ---------------------------------------------------------- 收起
    def _choose(self, value) -> None:
        """改值 + 回调。**不收面板** —— 收起是 ``_on_release`` 的事。

        放在按下时改值是故意的：点下去立刻看到效果，手感跟系统下拉一致。
        但面板必须活到松手，所以这里只是"值变了"，面板还开着，等 release 上来再收。
        """
        self._var.set(str(value))
        if self._command is not None:
            try:
                self._command(str(value))
            except Exception:
                pass

    def _close_soon(self) -> None:
        """把收起推迟到**当前这个事件处理完之后**再执行。

        现在是**从 ``<ButtonRelease-1>`` 里调**的，所以排到 idle 只是为了让
        ``destroy()`` 别发生在事件派发过程中（Tk 里在自己的绑定里销毁自己容易引出
        ``invalid command name`` 那类 bgerror），跟"防漏点击"已经没关系了 ——
        防漏点击靠的是「松手时面板还活着」（见 ``_on_release``）。
        """
        if self._popup is None:
            return
        try:
            self.after_idle(self._close)
        except Exception:
            self._close()

    def _add_binding(self, widget, sequence: str, handler) -> None:
        """加一条绑定并记下 funcid，收起时好精确解绑。

        名字**不能**叫 ``_bind``：那是 ``tkinter.Misc`` 的内部方法，
        覆盖掉它会让 ``super().__init__()`` 里的 ``self._bind(...)`` 直接报错。
        """
        try:
            funcid = widget.bind(sequence, handler, add="+")
        except Exception:
            return
        self._bindings.append((widget, sequence, funcid))

    def _maybe_close(self, event) -> None:
        if self._popup is None:
            return
        widget = getattr(event, "widget", None)
        # 点在列表里、或者点在字段本身上，都不收起
        if descends_from(widget, self._popup) or descends_from(widget, self):
            return
        self._close()

    def _on_root_focus_out(self, _event=None) -> None:
        """主窗口失焦后**稍等再判断**：确实跑到别的窗口去了才收起。

        直接收会误伤——点列表项本身也会让焦点变动。
        """
        self._cancel_focus_timer()
        try:
            self._focus_timer = self.after(60, self._focus_settled)
        except Exception:
            self._focus_timer = None

    def _focus_settled(self) -> None:
        self._focus_timer = None
        if self._popup is not None and not self._host_is_foreground():
            self._close()

    def _host_is_foreground(self) -> bool:
        """主窗口是不是当前的前台窗口（非 Windows 一律当作「是」）。

        面板是主窗口的子控件，所以这里只要比**一个**句柄 —— 以前弹窗是独立顶层窗口，
        还得把它自己那一份也算进来（焦点落在弹窗上时，前台是弹窗而不是主窗口）。
        """
        if os.name != "nt":
            return True
        front = win32.foreground()
        if not front:
            return False
        try:
            return front == win32.top_hwnd(self.winfo_toplevel())
        except Exception:
            return True

    def _cancel_focus_timer(self) -> None:
        if self._focus_timer is None:
            return
        try:
            self.after_cancel(self._focus_timer)
        except Exception:
            pass
        self._focus_timer = None

    def _close(self, animate=None) -> None:
        """收起面板。``animate=False`` 直接销毁（重新展开、关窗等场合用）。

        ⚠️ ``_popup`` 在这里**同步**置 None —— 有没有动画都一样。「松手那一下必须由
        面板吃掉」的时序约束（见 ``_on_release``）一个字没动，动画只是让那块画布
        多活 ``COLLAPSE_MS`` 再消失，期间它已经不再接受任何交互。
        """
        if animate is None:
            animate = self._animate
        self._kill_closing()            # 上一轮收起还没播完的，直接掐掉
        self._motion.cancel()           # 展开动画没跑完就收起，也要停
        popup, self._popup = self._popup, None
        self._cancel_focus_timer()
        for widget, sequence, funcid in self._bindings:
            try:
                widget.unbind(sequence, funcid)
            except Exception:
                pass
        self._bindings.clear()
        photos = self._photos
        self._menu_canvas = None
        self._host = None
        self._geom = None
        self._photos = {}
        self._panel_photo = None
        # 收起必须**从当前这一帧接着走**：展开还没播完就关掉时（点得快、或者连点字段），
        # 旧实现第一帧直接 `_reveal_band(1.0)` + `mix=0`，面板会先跳成**全开**再往回收
        # —— 屏幕上就是「刚关掉的菜单又整个冒了一下」。这里把当时的曝光进度 s 与带内
        # 混合比记下来，_frame_collapse 从它们出发收到 0。带高恰好 = s × 满高（见
        # _reveal_band），所以 s 可以直接由带高反解。
        fade = self._fade
        full = float(self._box[3]) if self._box else 0.0
        span = self._reveal_span()
        self._collapse_from = (min(1.0, max(0.0, (span[1] - span[0]) / full))
                               if full > 0 else 1.0)
        self._collapse_mix = (min(1.0, max(0.0, float(fade["mix"])))
                              if fade is not None else 0.0)
        # 淡入用的那三张源图交给正在播的收起动画（它要逐帧混出淡出）——就地丢掉的话
        # 收起途里面板会闪成白板。真正释放的地方是 ``_kill_closing``。
        self._closing_fade, self._fade = self._fade, None
        self._bounds = []
        self._rows, self._pills, self._picked, self._hover = [], [], [], -1
        if popup is None:
            return
        if not animate:
            self._note_patch(self._box, self._closing_fade)
            self._destroy_canvas(popup)
            return
        # 收起动画：把画布高度缩回 1 再销毁。图元照旧一个不改（靠 Tk 裁剪），
        # 但那些图元的 **PhotoImage 引用得自己拎住** —— ``self._photos`` 上面已经
        # 清空了，不留一份的话它们会被 GC，收缩过程里面板会闪成一块白板。
        for sequence in ("<Motion>", "<Leave>", "<Button-1>", "<ButtonRelease-1>"):
            try:
                popup.unbind(sequence)
            except Exception:
                pass
        try:
            popup.configure(cursor="arrow")
        except Exception:
            pass
        self._closing, self._closing_photos = popup, photos
        self._motion.start(self.COLLAPSE_MS, self._frame_collapse,
                           self._kill_closing)

    def _frame_collapse(self, progress: float) -> None:
        """收起中的一帧：露出带缩回「当前值那一行」，面板与四周的投影一起淡出。

        用 ``1 - soft_in_out(progress)`` 喂给 ``_reveal_band`` —— 那条曲线中心对称，
        所以这正是展开的镜像：收起来的手感与长出去一致，收回去的位置也还是那一行
        （Win11 浮出控件就是这个做法）。投影不单独再收一遍，就是跟着面板一起淡掉
        （底图固定成 ``full``，往 ``empty`` 混合的比例由 0 走到 1）。
        """
        if self._closing is None:
            return
        eased = shape.soft_in_out(progress)
        alpha = 1.0 - eased
        # 从「按下收起那一刻」的画面接着收：带子由当时的 s 缩到 0，带内由当时的
        # mix 混到 1（＝背景）。面板本来就已经全开时 from=1 / mix=0，与老行为逐帧一致；
        # 展开只播了三成就关掉时则是「接着那三成往回收」，不会先跳成全开。
        self._reveal = self._reveal_band(self._collapse_from * alpha)
        self._fade_alpha = alpha
        self._shadow_alpha = alpha
        start = self._collapse_mix
        # 底图固定成「面板 + 投影」，带内一路混到背景快照 ——
        # 面板、投影、文字一起淡掉，同时带子往回收。
        self._paint("full", self._reveal, start + (1.0 - start) * eased)

    @staticmethod
    def _destroy_canvas(canvas) -> None:
        try:
            canvas.place_forget()
        except Exception:
            pass
        try:
            canvas.destroy()
        except Exception:
            pass

    def destroy(self):
        if self._field_job is not None:
            try:
                self.after_cancel(self._field_job)
            except Exception:
                pass
            self._field_job = None
        self._animate = False           # 关窗途中别再排新的 after
        self._motion.cancel()
        self._close(animate=False)
        self._kill_closing()
        super().destroy()


# ------------------------------------------------------------------ 开关
class FluentSwitch(ctk.CTkFrame):
    """WinUI 3 开关：文字在左，「36×20 胶囊轨道 + 11px 圆点（悬停 14px）」在右。

    尺寸在 ``icons.TRACK_W / TRACK_H / KNOB / KNOB_HOVER``，改那里就同时改了轨道和滑块。

    切换时**滑块真的滑过去**（不是换一张图）：``icons.switch_pair`` 支持中间帧，
    这里按真实时间逐帧重画 —— 位置走 Fluent 的 ``fast_out_slow_in``、颜色走线性
    （两条曲线为什么分开，见 ``icons._switch``）。时长 ``TRACK_MS``，
    和 WinUI ToggleSwitch 的 167ms 同一量级。

    **悬停时滑块会长大一圈**（``icons.KNOB`` 11 → ``icons.KNOB_HOVER`` 14，Win11 的行为）：
    ``HOVER_MS`` 内从当前尺寸长到目标尺寸，移开再缩回去。曲线用 ``ease_out_cubic``
    —— 起步就动、尾巴才慢慢收（``fast_out_slow_in`` 一半时间就走了 97%，看着像「啪」
    地一下变大，见 ``_frame_hover``）。
    这条动画和切换动画**各有一条时间线**（两个 ``_Motion``），互不打断：悬停时点一下，
    滑块一边长大一边滑到另一头，画面由 ``_render`` 把两份状态合起来画。

    帧间隔用 ``FRAME_MS``（16ms）而**不是**下拉那个 8ms：它每一帧都要重画一张 PIL 图
    再包成 ``CTkImage``，比下拉「混一张底图」贵得多；180ms 里 11 帧已经够顺，
    再翻倍只是白烧 CPU。（下拉不一样 —— 它只有 80ms，不抢帧数根本看不出是动画。）
    悬停那条只有 ``HOVER_MS``，又要求「快而不跳」，所以单独用更密的 ``HOVER_FRAME_MS``
    （实测一帧画两张图只要 0.5ms，密一点很便宜）。

    ``animate=None``（默认）跟随系统的「显示动画」开关（见 ``system_animations``）；
    ``animate=False`` 时退化成「直接换图」，行为与加动画之前一模一样。
    """

    TRACK_MS = 180              # 切换动画时长（WinUI 的 thumb 动画约 167ms）
    FRAME_MS = 16               # 帧间隔（见类文档：开关不必跟下拉一样密）
    HOVER_MS = 100              # 悬停长大的时长（要「平滑且快速」：比切换短一半）
    HOVER_FRAME_MS = 8          # 悬停那条的帧间隔（比切换密，见类文档）

    def __init__(self, master, text, variable, font=None, command=None,
                 text_color=None, animate=None, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        self._var = variable
        self._command = command
        self._on = bool(variable.get())
        self._hover = False
        self._animate = system_animations() if animate is None else bool(animate)
        self._motion = _Motion(self)
        self._hover_motion = _Motion(self)          # 悬停长大（与切换各走各的时间线）
        self._progress = 1.0 if self._on else 0.0   # 颜色进度（0 关配色 / 1 开配色）
        self._from = self._progress                 # 本次切换动画的起点
        self._to = self._progress                   # 本次切换动画的终点
        self._travel = self._progress               # 滑块行程（0 贴左 / 1 贴右）
        self._hover_s = 0.0                         # 悬停长大幅度（0 静止 / 1 悬停）
        self._hover_from = 0.0                      # 本次悬停动画的起点
        self._hover_to = 0.0                        # 本次悬停动画的终点
        self._images: dict = {}
        self._anim_image = None                     # 动画帧的 CTkImage（不留引用会被 GC）

        self._label = ctk.CTkLabel(self, text=text, font=font, anchor="w",
                                   text_color=text_color or theme.color("text_muted"))
        self._label.pack(side="left", padx=(0, 8))

        self._knob = ctk.CTkLabel(self, text="", image=self._image())
        self._knob.pack(side="left")

        make_clickable(self, self._toggle)
        # 悬停效果：进出任意子孙都只是「稍后确认一次指针还在不在控件里」，
        # 这样就避免了在内部控件之间移动时 Enter/Leave 反复触发导致闪烁
        for w in walk(self):
            for sequence, handler in (("<Enter>", self._pointer_moved),
                                      ("<Leave>", self._pointer_moved)):
                try:
                    w.bind(sequence, handler, add=True)
                except Exception:
                    pass
        self._var.trace_add("write", self._var_changed)

    # ---------------------------------------------------------- 图像
    def _image(self, progress=None, travel=None, knob=None):
        """取开关的图。三个参数给定时现画一帧动画（不进缓存，见 ``switch_pair``）。"""
        if progress is None and travel is None and knob is None:
            # ⚠️ 键里**必须带 hover**：悬停不只是换个轨道底色，滑块还会变大一圈
            # （见 ``icons.KNOB_HOVER``）。以前写成 ``hover and not self._on``
            # 是「开态悬停与静态一模一样」，那条假设现在不成立了。
            key = (self._on, self._hover)
            if key not in self._images:
                self._images[key] = ctk.CTkImage(
                    *icons.switch_pair(*key), size=(icons.TRACK_W, icons.TRACK_H))
            return self._images[key]
        self._anim_image = ctk.CTkImage(
            *icons.switch_pair(self._on, self._hover, progress=progress,
                               travel=travel, knob=knob),
            size=(icons.TRACK_W, icons.TRACK_H))
        return self._anim_image

    def _render(self, live: bool | None = None) -> None:
        """按**当前状态**画一帧：切换进度 + 悬停进度合在一起交给 ``icons``。

        两条动画共用这一个出口（各自只改自己的状态量再调它），所以「悬停着点一下」
        时滑块会一边长大一边滑过去，而不是后一条动画把前一条顶掉。两条都没在跑时走
        静态缓存图 —— 不白画 PIL，端点也跟缓存里的那一张逐像素相同。

        ``live`` 显式给定时不再看动画是否在跑：逐帧喂进度的地方（``_frame_*`` 以及
        ``tools/preview_switch_hover.py`` 那种离线抽帧）必须能画中间帧 —— 那时
        ``_Motion`` 根本没启动，按「没动画就贴静态图」会把喂进去的进度整个吞掉。
        """
        if live is None:
            live = self._motion.running() or self._hover_motion.running()
        if live:
            img = self._image(progress=self._progress, travel=self._travel,
                              knob=self._hover_s)
        else:
            img = self._image()
        try:
            self._knob.configure(image=img)
        except Exception:
            pass

    def _sync(self) -> None:
        """刷新成「当前状态的静态图」。动画进行中直接跳过 —— 别插队打断动画。"""
        if self._motion.running() or self._hover_motion.running():
            return
        self._progress = 1.0 if self._on else 0.0
        self._travel = self._progress
        self._sync_static()

    # ---------------------------------------------------------- 交互
    def get(self) -> bool:
        return bool(self._var.get())

    def set(self, value) -> None:
        self._var.set(bool(value))

    def toggle(self) -> None:
        self._var.set(not bool(self._var.get()))

    def _toggle(self, _event=None) -> None:
        self.toggle()
        if self._command is not None:
            try:
                self._command()
            except Exception:
                pass

    def _var_changed(self, *_args) -> None:
        value = bool(self._var.get())
        if value == self._on:
            return
        self._on = value
        if not self._animate or not self.winfo_exists():
            self._progress = self._travel = 1.0 if value else 0.0
            self._sync_static()
            return
        # 从**当前进度**出发：连着点两下时不会先弹回端点再走一遍
        self._from, self._to = self._progress, 1.0 if value else 0.0
        self._motion.start(self.TRACK_MS, self._frame_switch, self._finish_switch,
                           interval=self.FRAME_MS)

    def _sync_static(self) -> None:
        """直接换成静态端点图（``_sync`` 的「不走动画」版）。"""
        try:
            self._knob.configure(image=self._image())
        except Exception:
            pass

    def _frame_switch(self, s: float) -> None:
        """切换中的一帧：颜色按线性时间走，滑块位置按同样的时间过一遍缓动。

        算式的本体在 ``shape.travel_frame``（离线预览脚本用的是同一份，不会走样）。
        """
        self._progress, self._travel = shape.travel_frame(s, self._from, self._to)
        self._render(live=True)

    def _finish_switch(self) -> None:
        """切换动画收尾：落回端点（静态图与末帧逐像素相同，见 ``icons.mix``）。

        ``_render`` 而不是 ``_sync_static``：悬停动画可能还在跑（点着的时候指针就在
        上面），那就得接着按悬停进度画，不能一步跳回静止尺寸。
        """
        self._progress = self._travel = self._to
        self._anim_image = None
        self._render()

    # ---------------------------------------------------------- 悬停
    def _animate_hover(self, target: float) -> None:
        """滑块在悬停时长大 / 移开时缩回：``HOVER_MS`` 内从**当前尺寸**走到 ``target``。

        从当前值出发而不是从端点出发 —— 指针快速划过（长大还没播完就移开）时不会
        先跳满再缩，正好接上。
        """
        self._hover_from, self._hover_to = self._hover_s, target
        if not self._animate or not self.winfo_exists() \
                or abs(self._hover_to - self._hover_from) < 1e-3:
            self._hover_s = target
            self._sync()
            return
        self._hover_motion.start(self.HOVER_MS, self._frame_hover, self._finish_hover,
                                 interval=self.HOVER_FRAME_MS)

    def _frame_hover(self, s: float) -> None:
        """悬停长大的中间帧。曲线用 ``ease_out_cubic``（起步快、尾巴收）。

        刻意**不用** ``fast_out_slow_in``：那条 20% 的时间就跑到 79%，100ms 的动画
        看着只有 40ms，滑块的边缘是「啪」地一下过去的。``ease_out_cubic`` 在同样
        100ms 里 20% → 49%、60% → 94%，一路都看得见，既快又不硬。
        """
        self._hover_s = shape.lerp(self._hover_from, self._hover_to,
                                   shape.ease_out_cubic(s))
        self._render(live=True)

    def _finish_hover(self) -> None:
        self._hover_s = self._hover_to
        self._anim_image = None
        self._render()

    def _pointer_moved(self, _event=None) -> None:
        try:
            self.after_idle(self._check_hover)
        except Exception:
            pass

    def _check_hover(self) -> None:
        try:
            x, y = self.winfo_pointerxy()
            under = self.winfo_containing(x, y)
        except Exception:
            return
        hover = under is not None and descends_from(under, self)
        if hover != self._hover:
            self._hover = hover
            self._animate_hover(1.0 if hover else 0.0)

    def destroy(self):
        self._motion.cancel()       # 关窗时别留下会去碰死控件的 after
        self._hover_motion.cancel()
        super().destroy()


# ------------------------------------------------------------------ 版本色块
class FluentBadge(ctk.CTkFrame):
    """WinUI 3 的色块 / InfoBadge：小圆角胶囊 + 反色文字（主窗口标题旁显示版本号）。

    为什么不用 ``CTkLabel(fg_color=..., corner_radius=...)``：CTk 的圆角是**字形拼的**
    （见 ``FluentComboBox`` 字段底板那段注释），小尺寸下四个角是硬阶梯。底板仍走
    ``icons.rounded_rect``（4× 超采样 + LANCZOS），文字压在上面。

    宽度**跟着文字走**：文字 label 用 pack 撑开框架（左右让 ``PAD_X``），``<Configure>``
    到了再按实际尺寸画底板 —— 所以这里不用自己量字体宽度。高度给死 ``HEIGHT``（CTkLabel
    的默认高 28 对色块来说太高，挂在大标题旁边像一块砖）。文字 label 的底色填成胶囊色，
    它露出的那块「方块」正好落在圆角以内的直边上（和下拉字段里那个文字 label 是同一套做法）。
    """

    RADIUS = 5          # 逻辑像素（20 高的 1/4，与 WinUI 的 InfoBadge 同一比例）
    HEIGHT = 20
    PAD_X = 8

    def __init__(self, master, text, font=None, fill="accent",
                 text_color="on_accent", radius=None, height=None, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        self._fill_token = fill            # 主题令牌名，深浅色各取一次
        self._text_token = text_color
        self._radius = self.RADIUS if radius is None else radius
        self._height = self.HEIGHT if height is None else height
        self._badge_key = None
        self._badge_photo = None
        self._badge_img = None
        self._badge_job = None

        # 底板（原生 tk.Label：place 起来最省事，且不受 CTk 的 DPI 缩放影响 —— 尺寸
        # 直接用 winfo_width/height 这对**物理**像素）
        self._bg = tk.Label(self, bd=0, highlightthickness=0)
        self._bg.place(x=0, y=0, relwidth=1, relheight=1)

        dark = ctk.get_appearance_mode() == "Dark"
        fill_hex = theme.color(self._fill_token, dark)
        # fg_color / bg_color 都要显式给：transparent 会去取父控件颜色，父控件是透明的，
        # 于是文字底下会糊一块窗口底色（同 FluentComboBox 的注释）
        self._text = ctk.CTkLabel(self, text=text, font=font, height=self._height,
                                  text_color=theme.color(self._text_token, dark),
                                  fg_color=fill_hex, bg_color=fill_hex,
                                  corner_radius=0)
        self._text.pack(padx=(self.PAD_X, self.PAD_X))

        self.bind("<Configure>", lambda _e: self._schedule_badge(), add="+")
        self._schedule_badge()

    # ---------------------------------------------------------- 底板
    def _schedule_badge(self) -> None:
        if self._badge_job is not None:
            return
        try:
            self._badge_job = self.after_idle(self._draw_badge)
        except Exception:
            self._badge_job = None

    def _draw_badge(self) -> None:
        self._badge_job = None
        try:
            w, h = self.winfo_width(), self.winfo_height()
        except Exception:
            return
        if w <= 2 or h <= 2:
            return
        dark = ctk.get_appearance_mode() == "Dark"
        key = (w, h, dark)
        if key == self._badge_key:
            return
        window = theme.color("window", dark)     # 圆角外铺窗口底色，别露出 Tk 的灰
        img = icons.rounded_rect(
            (w, h), radius=self._radius * self._get_widget_scaling(),
            fill=theme.color(self._fill_token, dark), bg=window)
        try:
            photo = ImageTk.PhotoImage(img)
            self._bg.configure(image=photo, bg=window)
        except Exception:
            return
        self._badge_photo = photo                # 不留引用就被 GC 掉成白板
        self._badge_img = img                    # 原始 PIL 图：留给自检量像素
        self._badge_key = key

    def _set_appearance_mode(self, mode_string):
        super()._set_appearance_mode(mode_string)
        dark = ctk.get_appearance_mode() == "Dark"
        fill = theme.color(self._fill_token, dark)
        try:
            self._text.configure(fg_color=fill, bg_color=fill,
                                 text_color=theme.color(self._text_token, dark))
        except Exception:
            pass
        self._badge_key = None                   # 换了深浅色，底板要重画
        self._schedule_badge()


# ------------------------------------------------------------------ 描边底板
def paint_surface(host, *, fill, stroke, bg, radius, border) -> None:
    """在 ``host._canvas`` 上贴一张 Pillow 画的圆角底板（含描边），并置顶。

    画法和下拉字段底板完全一致（见 ``icons.rounded_rect``）：描边往内画 → 四条边粗细
    一致，4× 超采样 + LANCZOS → 圆角是连续过渡而不是硬阶梯。

    画在 Canvas 上的好处是**行为一点都不用改**：CTkButton / CTkEntry 的文字与输入框
    是真正的子控件，永远压在 Canvas 之上；我们只是把 Canvas 上那层「字形拼出来的」
    圆角矩形盖掉。``bg`` 用父容器底色把圆角外填满，省得依赖 Tk 的 alpha 合成。
    """
    try:
        w = round(host._apply_widget_scaling(host._current_width))
        h = round(host._apply_widget_scaling(host._current_height))
    except Exception:
        return
    if w <= 2 or h <= 2:
        return
    border = max(0, round(border))
    key = (w, h, fill, stroke, bg, round(radius, 2), border)
    if key == getattr(host, "_surface_key", None) and host._surface_item is not None:
        return
    img = icons.rounded_rect((w, h), radius=radius, fill=fill, stroke=stroke,
                             border=border, bg=bg)
    try:
        photo = ImageTk.PhotoImage(img)
    except Exception:
        return
    item = getattr(host, "_surface_item", None)
    if item is None:
        item = host._canvas.create_image(0, 0, anchor="nw", image=photo)
        host._surface_item = item
    else:
        try:
            host._canvas.itemconfigure(item, image=photo)
        except Exception:
            item = host._canvas.create_image(0, 0, anchor="nw", image=photo)
            host._surface_item = item
    host._canvas.tag_raise(item)
    host._surface_photo = photo        # 不留引用就被 GC，底板会变白板
    host._surface_img = img            # 原始 PIL 图：留给自检量像素
    host._surface_key = key


def _surface_fill(widget):
    """控件当前的「填充色」令牌（含 "transparent" 与 None 的兜底）。"""
    color = widget._fg_color
    if color is None or color == "transparent":
        color = widget._bg_color
    return widget._apply_appearance_mode(color)


class _PillowBackdrop:
    """把 CTk 控件自己画的「圆角 + 描边」换成 Pillow 画的版本。

    只动画法、不动行为：尺寸、悬停、文字、图标、``command`` 全部沿用 CTk。
    子类要做的只有两件事：``__init__`` 里调 ``_backdrop_init``，在 ``_draw`` 末尾调
    ``_backdrop_paint``（并在 ``_on_enter`` / ``_on_leave`` 后也调一次，好跟上悬停色）。

    为什么值得换：CTk 的圆角是用**字形**拼的（``font_shapes``），半径小的时候一圈只有
    5~8 级过渡色 —— 放大看是硬阶梯，用户会形容成「圆角过渡不平滑、角落线条太细」；
    Pillow 出来的有十几级，和自绘下拉的字段底板才是同一套观感。
    """

    def _backdrop_init(self, border_width, stroke):
        self._surface_border = 0            # 真正画出来的描边宽（逻辑像素）
        self._surface_item = None
        self._surface_photo = None
        self._surface_img = None
        self._surface_key = None
        self._surface_wanted_border = border_width
        self._surface_stroke = stroke

    def _backdrop_cget(self, attribute_name):
        if attribute_name == "border_width":
            return self._surface_wanted_border
        return None

    def _backdrop_paint(self):
        stroke = self._surface_stroke
        paint_surface(
            self,
            fill=self._backdrop_fill(),
            stroke=self._apply_appearance_mode(stroke) if stroke is not None else None,
            bg=self._apply_appearance_mode(self._bg_color),
            radius=self._apply_widget_scaling(self._corner_radius),
            border=self._apply_widget_scaling(self._surface_border),
        )

    def _backdrop_fill(self):
        return _surface_fill(self)

    def _backdrop_activate(self):
        """``super().__init__`` 之后调：把 CTk 的描边关掉，改由我们画。"""
        self._surface_border = self._border_width
        self._surface_wanted_border = self._border_width
        self._surface_stroke = self._border_color    # CTk 已经解析好的描边色令牌
        self._border_width = 0              # 不让 CTk 再画那圈硬阶梯描边
        self._draw()


class FluentButton(_PillowBackdrop, ctk.CTkButton):
    """描边与圆角由 Pillow 画、其余一切照旧的 ``CTkButton``。

    用法和 ``CTkButton`` 完全一样（自动宽度 / 悬停 / 图标 / ``command``），只是圆角
    是抗锯齿的、四边描边粗细一致。
    """

    def __init__(self, master, *args, border_width=None, border_color=None, **kwargs):
        self._backdrop_init(border_width or 0, border_color)
        # 先按用户给的 border_width 建（CTk 会据此算好 grid 内缩），建完再关掉它的描边
        super().__init__(master, *args, border_width=border_width,
                         border_color=border_color, **kwargs)
        self._backdrop_activate()

    def cget(self, attribute_name):
        value = self._backdrop_cget(attribute_name)
        return super().cget(attribute_name) if value is None else value

    def _backdrop_fill(self):
        hovered = (getattr(self, "_mouse_inside", False) and self._hover is True
                   and self._state == "normal" and self._hover_color is not None)
        color = self._hover_color if hovered else self._fg_color
        if color is None or color == "transparent":
            color = self._bg_color
        return self._apply_appearance_mode(color)

    def _draw(self, no_color_updates=False):
        super()._draw(no_color_updates)
        self._backdrop_paint()

    def _on_enter(self, event=None):
        super()._on_enter(event)
        self._backdrop_paint()

    def _on_leave(self, event=None):
        super()._on_leave(event)
        self._backdrop_paint()


class FluentEntry(_PillowBackdrop, ctk.CTkEntry):
    """描边与圆角由 Pillow 画的 ``CTkEntry``（输入行为原样沿用）。"""

    def __init__(self, master, *args, border_width=None, border_color=None, **kwargs):
        self._backdrop_init(border_width or 0, border_color)
        super().__init__(master, *args, border_width=border_width,
                         border_color=border_color, **kwargs)
        self._backdrop_activate()

    def cget(self, attribute_name):
        value = self._backdrop_cget(attribute_name)
        return super().cget(attribute_name) if value is None else value

    def _draw(self, no_color_updates=False):
        super()._draw(no_color_updates)
        self._backdrop_paint()

