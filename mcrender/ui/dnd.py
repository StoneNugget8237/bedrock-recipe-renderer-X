"""Windows 文件拖放：把任意 Tk/CTk 窗口变成投放目标。

Tkinter 本身没有拖放支持，这里直接给窗口挂一个 Win32 窗口过程，接 ``WM_DROPFILES``。
只在 Windows 上生效；其它平台、远程桌面或接口不可用时返回 ``False``，
界面上的「选择…」按钮功能完全一样，不会因为拖放失败而受影响。
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes

WM_DROPFILES = 0x0233
GWLP_WNDPROC = -4


def enable_drop(root, on_drop) -> bool:
    """让 ``root`` 接受文件拖放，命中的路径交给 ``on_drop(list[str])``。

    返回是否挂载成功。回调在 Tk 主线程里被调用（Windows 会把消息投递给
    我们自己的窗口过程），所以直接做重活会卡界面 —— 调用方自己开线程。
    """
    try:
        shell32 = ctypes.windll.shell32
        user32 = ctypes.windll.user32
    except Exception:
        return False

    try:
        hwnd = wintypes.HWND(root.winfo_id())
        shell32.DragAcceptFiles(hwnd, True)

        wndproc_t = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, ctypes.c_uint,
                                       wintypes.WPARAM, wintypes.LPARAM)

        user32.SetWindowLongPtrW.restype = ctypes.c_void_p
        user32.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
        user32.CallWindowProcW.restype = ctypes.c_ssize_t
        user32.CallWindowProcW.argtypes = [ctypes.c_void_p, wintypes.HWND, ctypes.c_uint,
                                           wintypes.WPARAM, wintypes.LPARAM]
        shell32.DragQueryFileW.argtypes = [wintypes.HANDLE, wintypes.UINT, wintypes.LPWSTR,
                                           wintypes.UINT]
        shell32.DragFinish.argtypes = [wintypes.HANDLE]

        def wndproc(h, msg, wparam, lparam):
            if msg == WM_DROPFILES:
                hdrop = wintypes.HANDLE(wparam)
                count = shell32.DragQueryFileW(hdrop, 0xFFFFFFFF, None, 0)
                picked = []
                for i in range(count):
                    need = shell32.DragQueryFileW(hdrop, i, None, 0)
                    buf = ctypes.create_unicode_buffer(need + 1)
                    shell32.DragQueryFileW(hdrop, i, buf, need + 1)
                    picked.append(buf.value)
                shell32.DragFinish(hdrop)
                on_drop(picked)
                return 0
            return user32.CallWindowProcW(old_proc, h, msg, wparam, lparam)

        proc = wndproc_t(wndproc)
        old_proc = user32.SetWindowLongPtrW(hwnd, GWLP_WNDPROC, proc)
        # 窗口过程和旧过程的引用都挂在 root 上，否则会被 GC 掉、消息一来就崩
        root._mc_old_proc = old_proc
        root._mc_drop_proc = proc
        return True
    except Exception:
        return False
