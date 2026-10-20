"""Win32 的少量界面原语：抓窗口位图、分层窗口（逐像素 alpha）、动画计时精度。

只在 Windows 上干活；任何一步拿不到能力就返回 ``None`` / ``False``，调用方自己
降级 —— 界面绝不允许因此起不来。

这里有几件事值得解释，都是踩过坑才写下来的：

**一、Tk 的窗口在 Windows 上是「两层」的**
``widget.winfo_id()`` 给的是 Tk 的**内层子窗口**（``WS_CHILD``），外面还套着一层
真正的顶层窗口。所以：

- **抓内容**要打``winfo_id()``（``PrintWindow`` 对子窗口更灵）；
- **分层窗口**必须打 ``GetAncestor(hwnd, GA_ROOT)`` 拿到的那层 ——
  ``UpdateLayeredWindow`` 不认子窗口，对着 ``winfo_id()`` 一律返回 0，
  而且 ``GetLastError()`` 还是 0，完全不告诉你为什么。

**二、抓窗口用 ``PrintWindow`` 而不是 ``ImageGrab``**
``ImageGrab.grab()`` 是从**屏幕** DC 拷像素，远端会话 / 锁屏 / 被遮挡时拿不到东西
（实测这台机器返回的是一张跟屏幕对不上的灰图）。``PrintWindow(hwnd, hdc,
PW_RENDERFULLCONTENT)`` 是让**窗口自己**把内容画进我们给的内存 DC —— 无边框
Toplevel、Mica 背景、Canvas 内容都能画出来。

还有个更细的坑：**顶层那层容器窗口从来没被画过**（它被内层画布铺满了），
``PrintWindow`` 打上去会稳定地返回**全黑**。所以弹窗烘焙要抓**画布**，不是弹窗。

**三、为什么要分层窗口（``UpdateLayeredWindow``）**

> ⚠️ **UI 现在已经不用分层窗口了**：下拉面板改成了主窗口里的**子控件**（见
> ``widgets.FluentComboBox`` 的类文档），不再是独立窗口，也就不需要窗口级 alpha。
> 下面这段连同 ``blit`` / ``premultiply`` / ``blend`` 作为原语保留着，
> 别的场合仍可能用得上。

无边框弹窗想做圆角，常规做法是 ``-transparentcolor`` 抠一个色键。但那条路只有
「整像素等于色键」才会被抠掉，圆角那圈**抗锯齿的过渡像素**是色键与面板色的混合，
抠不掉 —— 于是四个角上留一圈品红。分层窗口走的是真·逐像素 alpha，圆角是对着
桌面合成的，没有这层问题。注意：**设过 ``-transparentcolor`` 的窗口再用
``UpdateLayeredWindow`` 会失败**（前者内部调 ``SetLayeredWindowAttributes``），
两条路只能选一条。

**预乘 alpha 的约定**：``blit()`` 收进来的图必须是**已经预乘**的（RGB 已乘过
alpha），见 ``premultiply``。

**四、动画想跑满帧率，得自己提计时器精度**

Windows 默认的计时器粒度是 ~15.6ms，``after(8)`` 会被量化到 13ms 上下 —— 动画再
怎么调参数也出不了帧。``timer_resolution(True)`` 在动画期间调 ``timeBeginPeriod(1)``
把精度提到 1ms，播完立刻归还（内部计数，可嵌套）。详见该函数的说明与实测表。

**五、标题栏图标为什么还要自己设一遍**

Tk 的 ``wm iconbitmap`` 只把 ico 的 **16×16** 那一档塞进**窗口类**图标 —— 实测
``GetClassLongPtr(hwnd, GCLP_HICONSM)`` 拿回来的位图就是 16×16。本机 175% 缩放下
标题栏图标要 28px，Windows 只能把 16 档**拉大**，看着就是「logo 糊」。要让系统去挑
32 档，得自己 ``LoadImage``（它按请求尺寸挑最合适的一档）+ ``WM_SETICON`` 设成
**窗口**级图标（窗口级优先于类级）。⚠️ 只修 ico 里的 16 档是治标 —— 再怎么修，
它仍是要被放大 1.75 倍的那一张。
"""
from __future__ import annotations

import ctypes
import os
from ctypes import wintypes

if os.name == "nt":
    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32
    kernel32 = ctypes.windll.kernel32
else:                                   # pragma: no cover - 非 Windows 分支
    user32 = gdi32 = kernel32 = None

GWL_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
ULW_ALPHA = 2
AC_SRC_OVER, AC_SRC_ALPHA = 0, 1
GA_ROOT = 2

# ---- 窗口图标（见 apply_window_icon）
SM_CXSMICON, SM_CXICON = 49, 11
WM_SETICON, ICON_SMALL, ICON_BIG = 0x0080, 0, 1
IMAGE_ICON, LR_LOADFROMFILE = 1, 0x0010


class _BMIH(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long),
                ("biHeight", ctypes.c_long), ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", ctypes.c_long),
                ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


class _BMI(ctypes.Structure):
    _fields_ = [("bmiHeader", _BMIH), ("bmiColors", wintypes.DWORD * 3)]


class _BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_byte), ("BlendFlags", ctypes.c_byte),
                ("SourceConstantAlpha", ctypes.c_byte), ("AlphaFormat", ctypes.c_byte)]


def _bind() -> None:
    """把用到的入口的签名钉死。

    **必须**：句柄在 64 位下是 8 字节，而 ctypes 默认把参数当 ``c_int``（4 字节），
    一旦句柄超过 2^31 就会 ``OverflowError`` / 被截断 —— 而且看运气，同一个调用
    有时能过、有时炸。这里全部显式声明。
    """
    u, g = user32, gdi32
    u.GetClientRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.RECT)]
    u.GetClientRect.restype = wintypes.BOOL
    u.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.RECT)]
    u.GetWindowRect.restype = wintypes.BOOL
    u.GetDC.argtypes = [ctypes.c_void_p]
    u.GetDC.restype = ctypes.c_void_p
    u.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    u.ReleaseDC.restype = ctypes.c_int
    u.PrintWindow.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT]
    u.PrintWindow.restype = wintypes.BOOL
    u.GetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int]
    u.GetWindowLongPtrW.restype = ctypes.c_longlong
    u.SetWindowLongPtrW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_longlong]
    u.SetWindowLongPtrW.restype = ctypes.c_longlong
    u.GetAncestor.argtypes = [ctypes.c_void_p, wintypes.UINT]
    u.GetAncestor.restype = ctypes.c_void_p
    u.GetForegroundWindow.argtypes = []
    u.GetForegroundWindow.restype = ctypes.c_void_p
    # 抢前台（见 force_foreground）。句柄参数一律 c_void_p：不声明的话默认按 c_int
    # 截断，句柄超过 2^31 就 OverflowError。
    u.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
    u.ShowWindow.restype = wintypes.BOOL
    u.BringWindowToTop.argtypes = [ctypes.c_void_p]
    u.BringWindowToTop.restype = wintypes.BOOL
    u.SetForegroundWindow.argtypes = [ctypes.c_void_p]
    u.SetForegroundWindow.restype = wintypes.BOOL
    u.SetFocus.argtypes = [ctypes.c_void_p]
    u.SetFocus.restype = ctypes.c_void_p
    u.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.DWORD)]
    u.GetWindowThreadProcessId.restype = wintypes.DWORD
    u.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD, wintypes.BOOL]
    u.AttachThreadInput.restype = wintypes.BOOL
    kernel32.GetCurrentThreadId.argtypes = []
    kernel32.GetCurrentThreadId.restype = wintypes.DWORD
    u.UpdateLayeredWindow.argtypes = [ctypes.c_void_p, ctypes.c_void_p,
                                      ctypes.POINTER(wintypes.POINT),
                                      ctypes.POINTER(wintypes.SIZE),
                                      ctypes.c_void_p, ctypes.POINTER(wintypes.POINT),
                                      wintypes.DWORD, ctypes.POINTER(_BLENDFUNCTION),
                                      wintypes.DWORD]
    u.UpdateLayeredWindow.restype = wintypes.BOOL
    g.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
    g.CreateCompatibleDC.restype = ctypes.c_void_p
    g.CreateCompatibleBitmap.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
    g.CreateCompatibleBitmap.restype = ctypes.c_void_p
    g.CreateDIBSection.argtypes = [ctypes.c_void_p, ctypes.POINTER(_BMI), wintypes.UINT,
                                   ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p,
                                   wintypes.DWORD]
    g.CreateDIBSection.restype = ctypes.c_void_p
    g.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    g.SelectObject.restype = ctypes.c_void_p
    g.DeleteObject.argtypes = [ctypes.c_void_p]
    g.DeleteObject.restype = wintypes.BOOL
    g.DeleteDC.argtypes = [ctypes.c_void_p]
    g.DeleteDC.restype = wintypes.BOOL
    g.GetDIBits.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT, wintypes.UINT,
                            ctypes.c_void_p, ctypes.POINTER(_BMI), wintypes.UINT]
    g.GetDIBits.restype = ctypes.c_int
    g.BitBlt.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                         ctypes.c_int, ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                         wintypes.DWORD]
    g.BitBlt.restype = wintypes.BOOL
    u.LoadImageW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR, wintypes.UINT,
                             ctypes.c_int, ctypes.c_int, wintypes.UINT]
    u.LoadImageW.restype = ctypes.c_void_p
    u.SendMessageW.argtypes = [ctypes.c_void_p, wintypes.UINT,
                               wintypes.WPARAM, wintypes.LPARAM]
    u.SendMessageW.restype = wintypes.LPARAM
    # 这两个是 Win10 1607+ 才有的，缺了不能让整个 _bind() 挂掉
    for _name, _args, _res in (("GetDpiForWindow", [ctypes.c_void_p], wintypes.UINT),
                               ("GetDpiForSystem", [], wintypes.UINT),
                               ("GetSystemMetricsForDpi",
                                [ctypes.c_int, wintypes.UINT], ctypes.c_int)):
        _fn = getattr(u, _name, None)
        if _fn is not None:
            _fn.argtypes = _args
            _fn.restype = _res


if user32 is not None:
    try:
        _bind()
    except Exception:                   # pragma: no cover
        user32 = gdi32 = None


def available() -> bool:
    """本机能不能用这些原语（非 Windows 一律 False）。"""
    return user32 is not None and gdi32 is not None


def _hwnd(widget):
    """控件的 Win32 句柄。

    ⚠️ 这是 Tk 的**内部子窗口**（``WS_CHILD``）：Tk 在 Windows 上把每个顶层窗口做
    成「外层窗口 + 内部子窗口」两层，``winfo_id()`` 给的是里面那个。抓窗口内容
    （``PrintWindow``）用它对，做分层窗口用它是错的 —— 见 ``_top_hwnd``。
    """
    return ctypes.c_void_p(int(widget.winfo_id()))


def _top_hwnd(widget):
    """Tk 顶层窗口（外层）的 Win32 句柄。

    **分层窗口必须拿这个**：``UpdateLayeredWindow`` 不认子窗口，对着
    ``winfo_id()`` 直接返回 0 —— 而且不看错误码（``GetLastError`` 是 0），特别难猜。
    实测：不加处理时不管有没有 ``overrideredirect`` / ``-topmost`` / ``-transient``
    都失败；原因是句柄本身选错了，不是样式问题。
    """
    if not available():
        return None
    hwnd = int(widget.winfo_id())
    try:
        root = user32.GetAncestor(ctypes.c_void_p(hwnd), GA_ROOT)
    except Exception:
        return ctypes.c_void_p(hwnd)
    return ctypes.c_void_p(root or hwnd)


def position(widget):
    """窗口在**屏幕物理像素**里的左上角（`None` = 拿不到）。

    别用 ``winfo_rootx()`` 代替：Tk 的坐标空间（逻辑像素 / 虚拟屏幕）和 Win32 的
    物理像素在 175% 缩放下不是一回事，这里必须是物理值 —— 分层窗口按物理像素摆。
    """
    if not available():
        return None
    rect = wintypes.RECT()
    try:
        if not user32.GetWindowRect(_top_hwnd(widget), ctypes.byref(rect)):
            return None
    except Exception:
        return None
    return int(rect.left), int(rect.top)


def top_hwnd(widget):
    """控件所在**顶层**窗口的句柄（``_top_hwnd`` 的公开别名）。

    ``winfo_id()`` 给的是 Tk 的内层子窗口，跟前台窗口的句柄不是同一个东西 ——
    要比就得拿这个。见 ``_top_hwnd`` 的说明。

    返回句柄的**整数值**（拿不到返回 ``None``）：与 ``foreground()`` 直接 ``==`` /
    ``in`` 比较用 —— 一个 int 跟一个 ``c_void_p`` 对象混着比不会报错，但可能不等，
    那会让「前台是不是我们」永远判错。
    """
    top = _top_hwnd(widget)
    return None if top is None else int(top.value or 0)


def foreground():
    """当前前台顶层窗口的句柄（**整数值**，拿不到返回 ``None``）。

    给「前台窗口还是不是我们的」这类判断用：下拉弹窗是 ``overrideredirect`` 的
    ``-topmost`` 窗口，切到别的程序后要靠它才知道该收起。
    """
    if not available():
        return None
    try:
        hwnd = user32.GetForegroundWindow()
    except Exception:
        return None
    return int(hwnd) if hwnd else None


def force_foreground(widget) -> bool:
    """把窗口**强行**提到前台，返回「真的成了前台窗口」与否。

    给真机脚本用（``tools/check_option_click.py`` 拿合成鼠标事件验点击路由），
    **界面自己别调** —— 用户正打着字，程序抢前台是最讨人厌的行为之一。

    为什么光 ``SetForegroundWindow`` 不够：调用进程不是前台进程时，Windows 会
    **静默失败**（前台锁，返回 0，不告诉你原因）。脚本是后台起的，前台往往是浏览器 /
    编辑器 —— 于是 ``SetCursorPos`` + ``mouse_event`` 合成的点击全送到别人窗口里，
    被测窗口「什么都没发生」：``没有顺带触发下面的按钮`` 这类断言于是**假通过**，
    比失败还糟。绕开前台锁的标准办法是先 ``AttachThreadInput`` 到前台窗口的输入队列，
    共享之后 ``SetForegroundWindow`` 才认；调用完必须解除，否则两条线程的输入队列
    会一直黏在一起。
    """
    if not available():
        return False
    hwnd = top_hwnd(widget)                     # ⚠️ 用整数值：``c_void_p`` 跟 int
    if not hwnd:                                # 比较永远是 False（见 top_hwnd）
        return False
    u = user32
    if foreground() == hwnd:
        return True
    u.ShowWindow(hwnd, 9)                       # 9 = SW_RESTORE：最小化也能叫回来
    fg = foreground()
    tid_fg = u.GetWindowThreadProcessId(fg, None) if fg else 0
    tid_me = kernel32.GetCurrentThreadId()
    attached = False
    if tid_fg and tid_fg != tid_me:
        attached = bool(u.AttachThreadInput(tid_me, tid_fg, True))
    try:
        u.BringWindowToTop(hwnd)
        u.SetForegroundWindow(hwnd)
        u.SetFocus(hwnd)
    finally:
        if attached:
            u.AttachThreadInput(tid_me, tid_fg, False)
    return foreground() == hwnd


# ------------------------------------------------------------------ 抓窗口
_SRCCOPY = 0x00CC0020


def _client_size(hwnd):
    rect = wintypes.RECT()
    if not user32.GetClientRect(hwnd, ctypes.byref(rect)):
        return 0, 0
    return int(rect.right), int(rect.bottom)


def _read(mem, bmp, w: int, h: int):
    """把内存 DC 里的位图读成 PIL RGB 图（``BGRX`` 直读，不绕 RGBA）。"""
    from PIL import Image

    head = _BMIH()
    head.biSize = ctypes.sizeof(_BMIH)
    head.biWidth, head.biHeight = w, -h         # 负高度 = 自上而下
    head.biPlanes, head.biBitCount, head.biCompression = 1, 32, 0
    info = _BMI()
    info.bmiHeader = head
    buf = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(info), 0)
    # ⚠️ 用 ``BGRX`` 直接解码：以前是「RGBA 共享图 + .convert("RGB")」，整窗 1085×805
    # 那一步实测要 4~13ms，而 BGRX + copy 只要 1ms 出头。``frombuffer`` 出来的是
    # **只读共享图**（背后就是 ``buf``），所以必须 ``copy()`` 拿一份自己的像素。
    return Image.frombuffer("RGB", (w, h), buf, "raw", "BGRX", 0, 1).copy()


def grab(widget, rect=None):
    """抓窗口**客户区**的一帧，返回 PIL RGB 图；拿不到返回 ``None``。

    ``rect`` = ``(x, y, w, h)``（客户区坐标）时只抓那一块 —— 只有那块大的位图要读回，
    实测 390×384 一块约 **1ms**，整窗要 6ms，差一个数量级。

    走 **``BitBlt``（从窗口自己的 DC 搬）** 而不是 ``PrintWindow``：

    | 方式 | 全窗 1085×805 | 说明 |
    |---|---|---|
    | ``PrintWindow`` + ``PW_RENDERFULLCONTENT`` | **34ms** | 让窗口重新渲染一整帧 |
    | ``BitBlt``（窗口 DC） | **5.9ms** | 从 DWM 的重定向表面搬像素 |

    两者实测**逐像素完全相同**，被别的窗口压住时也都能拿到**自己的**内容（走的是重定向
    表面，不是屏幕像素）。慢的那条留作兜底：窗口还没被合成出来时 ``BitBlt`` 会得到
    全黑，这时候再让窗口自己去画一遍。
    """
    if not available():
        return None

    hwnd = _hwnd(widget)
    cw, ch = _client_size(hwnd)
    if cw <= 0 or ch <= 0:
        return None
    if rect is None:
        x, y, w, h = 0, 0, cw, ch
    else:
        x, y, w, h = (int(v) for v in rect)
        # 夹进客户区（越界部分 BitBlt 不出来，返回的图会带上未初始化的黑边）
        x, y = max(0, x), max(0, y)
        w, h = min(w, cw - x), min(h, ch - y)
        if w <= 0 or h <= 0:
            return None

    src = user32.GetDC(hwnd)
    if not src:
        return None
    mem = gdi32.CreateCompatibleDC(src)
    bmp = gdi32.CreateCompatibleBitmap(src, w, h)
    old = gdi32.SelectObject(mem, bmp)
    try:
        try:
            ok = gdi32.BitBlt(mem, 0, 0, w, h, src, x, y, _SRCCOPY)
            shot = _read(mem, bmp, w, h) if ok else None
            if shot is not None and shot.getextrema() != ((0, 0), (0, 0), (0, 0)):
                return shot
        except Exception:
            pass
        # 兜底：让窗口自己画（慢 5 倍，但窗口还没合成出来时只有它管用）
        if rect is not None:
            mem_full = gdi32.CreateCompatibleDC(src)
            bmp_full = gdi32.CreateCompatibleBitmap(src, cw, ch)
            old_full = gdi32.SelectObject(mem_full, bmp_full)
            try:
                user32.PrintWindow(hwnd, mem_full, 3)
                full = _read(mem_full, bmp_full, cw, ch)
            finally:
                gdi32.SelectObject(mem_full, old_full)
                gdi32.DeleteObject(bmp_full)
                gdi32.DeleteDC(mem_full)
            return full.crop((x, y, x + w, y + h))
        user32.PrintWindow(hwnd, mem, 3)
        return _read(mem, bmp, w, h)
    except Exception:
        return None
    finally:
        gdi32.SelectObject(mem, old)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mem)
        user32.ReleaseDC(hwnd, src)


def apply_window_icon(widget, path: str) -> bool:
    """按窗口**当前 DPI** 把 ``path``（``.ico``）设成窗口图标；成功返回 True。

    为什么光有 Tk 的 ``wm iconbitmap`` 不够：它设的是**窗口类**的小图标，而且只把
    ico 里 **16×16** 那一档塞进去 —— 实测 ``GCLP_HICONSM`` 拿到的位图就是 16×16。
    本机 175% 缩放下标题栏图标要 28px，Windows 只能把 16 档拉大，看着就是「logo 糊」。

    这里用 ``LoadImage`` 让 Windows 自己按目标尺寸挑档（16 不够它就挑 32），再
    ``WM_SETICON`` 设成**窗口**图标 —— 窗口级优先于类级，标题栏就不吃放大的亏。

    ⚠️ HICON 不 ``DestroyIcon``：窗口还在用，进程退出时由系统回收（两个句柄，可忽略）。
    能力拿不到就返回 False，调用方不用管（Tk 那一步已经兜住了）。
    """
    if not available():
        return False
    hwnd = _top_hwnd(widget)
    if not hwnd:
        return False

    # ⚠️ 窗口还没 map 时 GetDpiForWindow 可能给 0 —— 一路回退到系统 DPI，否则会按 96
    # 算出 16px，等于白设一遍（实测踩过）。
    dpi = 0.0
    probe = getattr(user32, "GetDpiForWindow", None)
    if probe is not None:
        try:
            dpi = float(probe(hwnd)) or 0.0
        except Exception:
            dpi = 0.0
    if not dpi:
        sysprobe = getattr(user32, "GetDpiForSystem", None)
        if sysprobe is not None:
            try:
                dpi = float(sysprobe()) or 0.0
            except Exception:
                dpi = 0.0
    if not dpi:
        dpi = 96.0
    metrics = getattr(user32, "GetSystemMetricsForDpi", None)

    ok = False
    for kind, base in ((ICON_SMALL, 16), (ICON_BIG, 32)):
        index = SM_CXSMICON if kind == ICON_SMALL else SM_CXICON
        size = 0
        if metrics is not None:
            try:
                size = int(metrics(index, round(dpi)))
            except Exception:
                size = 0
        if not size:
            size = round(base * dpi / 96.0)          # 老系统上自己算
        hicon = user32.LoadImageW(None, path, IMAGE_ICON, size, size, LR_LOADFROMFILE)
        if hicon:
            user32.SendMessageW(hwnd, WM_SETICON, kind, hicon)
            ok = True
    return ok


# ------------------------------------------------------------------ 分层窗口
def blit(widget, image, at=None) -> bool:
    """把一张**预乘 alpha** 的 RGBA 图交给分层窗口显示。

    ``at`` 给 ``(x, y)`` 时按屏幕物理像素把窗口搬过去；**默认不给**，`pptDst` 传 NULL
    表示「只换内容、不动窗口」—— 一般都用这个：窗口位置由 Tk 摆好，我们不掺和。
    （实测：把一个 Tk 坐标当 pptDst 传进去，窗口会被搬歪，两边坐标空间不是一回事。）

    返回 False 表示这台机器/这个窗口用不了分层窗口，调用方要自己降级。
    """
    if not available():
        return False
    w, h = image.size
    if w <= 0 or h <= 0:
        return False
    try:
        data = image.tobytes("raw", "BGRA")
    except Exception:
        return False

    hwnd = _top_hwnd(widget)
    ex = user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
    if not (ex & WS_EX_LAYERED):
        user32.SetWindowLongPtrW(hwnd, GWL_EXSTYLE, ex | WS_EX_LAYERED)

    screen = user32.GetDC(None)
    mem = gdi32.CreateCompatibleDC(screen)
    info = _BMI()
    info.bmiHeader.biSize = ctypes.sizeof(_BMIH)
    info.bmiHeader.biWidth, info.bmiHeader.biHeight = w, -h
    info.bmiHeader.biPlanes, info.bmiHeader.biBitCount = 1, 32
    info.bmiHeader.biCompression = 0
    bits = ctypes.c_void_p()
    bmp = gdi32.CreateDIBSection(mem, ctypes.byref(info), 0, ctypes.byref(bits), None, 0)
    if not bmp:
        gdi32.DeleteDC(mem)
        user32.ReleaseDC(None, screen)
        return False
    old = gdi32.SelectObject(mem, bmp)
    try:
        ctypes.memmove(bits, data, len(data))
        blend = _BLENDFUNCTION(AC_SRC_OVER, 0, 255, AC_SRC_ALPHA)
        size = wintypes.SIZE(w, h)
        src = wintypes.POINT(0, 0)
        # ``pptDst`` 传 NULL = 「只换内容、别动窗口位置」。
        # ⚠️ 必须显式传 ``None``（ctypes 会把 POINTER 参数的 None 变成 NULL）——
        # 写成 ``ctypes.byref(None)`` 会**抛 TypeError**，而它被下面那个兜底 except
        # 吞掉，表现成「blit 永远返回 False」，极难排查。
        if at is None:
            dst_ref = None
        else:
            dst = wintypes.POINT(int(at[0]), int(at[1]))
            dst_ref = ctypes.byref(dst)
        ok = user32.UpdateLayeredWindow(hwnd, screen, dst_ref,
                                        ctypes.byref(size), mem, ctypes.byref(src),
                                        0, ctypes.byref(blend), ULW_ALPHA)
        return bool(ok)
    except Exception:
        return False
    finally:
        gdi32.SelectObject(mem, old)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mem)
        user32.ReleaseDC(None, screen)


# ------------------------------------------------------------------ 预乘工具
def premultiply(image):
    """把普通 RGBA 变成预乘 alpha（``blit`` 要的形式）。"""
    from PIL import Image, ImageChops

    if image.mode != "RGBA":
        image = image.convert("RGBA")
    r, g, b, a = image.split()
    return Image.merge("RGBA", (ImageChops.multiply(r, a), ImageChops.multiply(g, a),
                                ImageChops.multiply(b, a), a))


def blend(dst, src, box) -> None:
    """在**预乘 alpha** 的 ``dst`` 上就地叠一张预乘 ``src``（都是 RGBA）。

    ``Image.alpha_composite`` 走的是直通 alpha，对预乘数据会算错，所以这里自己
    写一遍：``out = src + dst * (1 - src.a)``，逐通道都是线性运算，ImageChops 足够快。
    """
    from PIL import Image, ImageChops

    x, y = box
    w, h = src.size
    patch = dst.crop((x, y, x + w, y + h))
    inv = src.split()[3].point(lambda v: 255 - v)
    pb, sb = patch.split(), src.split()
    out = Image.merge("RGBA", tuple(
        ImageChops.add(ImageChops.multiply(pb[i], inv), sb[i]) for i in range(4)))
    dst.paste(out, (x, y))


# ------------------------------------------------------------------ 计时器精度
TIMERR_NOERROR = 0
_period_holds = 0       # ``timeBeginPeriod`` 的嵌套计数，成对加减


def timer_resolution(high: bool) -> bool:
    """动画期间把系统计时器精度提到 1ms（``timeBeginPeriod(1)``）；``False`` 归还。

    **为什么非提不可。** Tk 的 ``after(ms)`` 只是「不早于 ms」的请求，实际落点受系统
    计时器粒度限制，而 Windows 默认那档是 ~15.6ms。本机实测（真机、Tk 主循环里数回调）：

    | ``after()`` | 不提高精度（中位间隔） | 提高精度后 |
    |---|---|---|
    | 4ms  | 9.68ms | 4.09ms |
    | 8ms  | 13.32ms | **8.18ms** |
    | 16ms | 16.40ms | 16.19ms |

    也就是说 8ms 的请求会被量化到 13ms 上下，**80ms 的动画只画得出 5~7 帧**；提过精度
    之后 ``after(8)`` 稳定落在 8.1ms，同样 80ms 能画 **10 帧**。而且均匀比帧数更要紧 ——
    忽快忽慢（4、4、6、12、12ms）比少几帧难看得多。

    ⚠️ 上面这张表是在**空跑**的 Tk 主循环里量的（`tools/probe_timer.py` 里就是这样量的）：
    提过精度之后，请求多少就是多少。进了真动画还要再加**每帧自己的开销**（Pillow 混图 +
    换图 + Tk 重画面板，实测 ~2ms），它压在帧间隔之上 —— 所以真动画里请求 6ms 落 8.4ms。
    挑帧间隔要按真动画的实测，别按「60fps = 16ms」拍（见 ``widgets.FRAME_MS``）。

    **内部计数**：多个动画重叠时（下拉 + 开关，或同时开两个下拉）不会互相把精度收掉。
    但**必须成对调用** —— ``high=True`` 一次配 ``False`` 一次。多减一次不会有副作用
    （计数不会变负），少减一次会让精度一直提着（费电）。

    返回值只表示「这次调用有没有生效」；拿不到能力（非 Windows / 老系统）返回 ``False``，
    调用方照常跑动画，只是帧少一点。
    """
    global _period_holds
    if os.name != "nt":
        return False
    try:
        winmm = ctypes.windll.winmm
    except Exception:
        return False
    if high:
        if _period_holds == 0:
            try:
                if winmm.timeBeginPeriod(1) != TIMERR_NOERROR:
                    return False
            except Exception:
                return False
        _period_holds += 1
        return True
    if _period_holds <= 0:
        return False
    _period_holds -= 1
    if _period_holds == 0:
        try:
            winmm.timeEndPeriod(1)
        except Exception:
            pass
    return True


def timer_holds() -> int:
    """当前有几个动画在提着计时器精度。

    测试用：动画播完（或控件被销毁）之后**必须回到 0** —— 留着就是白耗电。
    """
    return _period_holds
