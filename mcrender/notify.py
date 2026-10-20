"""Windows 通知（Toast）—— 检查更新的结果就用它弹。

**为什么走 PowerShell 的 WinRT 类型**：``Windows.UI.Notifications`` 在 Windows
PowerShell 5.1 里是现成的，不用给这个绿色免安装的工具再加一个 C 扩展依赖
（``winrt`` / ``winotify`` 那类都要装东西、还要跟着 PyInstaller 一起打包）。
代价是每次弹通知要起一个 powershell 进程（~0.3s），对一个手动点的按钮完全可以接受。

**编码**：整段 toast XML 用 base64 传进去（UTF-8 先编码、PowerShell 里再解回来）。
命令行参数是纯 ASCII → 中文标题 / 正文不会受控制台代码页影响，也不用写临时 .ps1
（Windows PowerShell 5.1 读 .ps1 默认按 ANSI，中文必糊）。

**⚠️ 必须先给 AUMID 注册身份**：从 Windows 10 1709 起，Toast 的
AUMID 必须是**已注册**的 —— 光调用 ``CreateToastNotifier(任意id).Show()`` 会返回
成功、通知也进了 wpndatabase，但**横幅永远不弹**（只进通知中心）。本机实测：不注册
时整屏 3.5s 帧差为 0；挂上开始菜单快捷方式后横幅立刻正常出现。

注册方式 = 开始菜单里放一个带 ``System.AppUserModel.ID`` 的 ``.lnk``（不需要 pin）。
所以 ``toast()`` 会先 ``ensure_registered()``：没有就建一次，之后直接跳过。
建快捷方式要 ``Add-Type`` 编一小段 C#（IShellLinkW + IPropertyStore），~1s，
**每个用户只付一次**；界面在启动时就用后台线程把它做掉。

**「前往下载」按钮**用 ``activationType="protocol"``：点一下交给 shell 打开 URL，
**不需要**给这个程序注册 COM 激活处理器 —— 也就没有额外安装步骤。

⚠️ 万一某台机器死活拒绝注册，toast 依旧会「成功」但可能不显横幅 —— 调用方
``window.py`` 已经把结论同时写在标题区一行小字里，别让按钮「点了没反应」。
"""
from __future__ import annotations

import base64
import os
import subprocess
import sys
import threading
from xml.sax.saxutils import escape as _escape

APP_ID = "NewBee.BedrockRecipeRendererX"
# PowerShell 自己的 AUMID：所有 Windows 上都注册过，兜底用（通知来源会显示成 PowerShell）
FALLBACK_APP_ID = (r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}"
                   r"\WindowsPowerShell\v1.0\powershell.exe")

# 开始菜单快捷方式的名字：toast 里显示的「发送者」就是这个文件名（去掉 .lnk）
_SHORTCUT_NAME = "Bedrock Recipe Renderer X.lnk"

_CREATE_NO_WINDOW = 0x08000000     # 别让 powershell 闪一下黑框
TIMEOUT = 20                        # 秒：起进程 + 弹通知，给足余量
REGISTER_TIMEOUT = 40               # 秒：Add-Type 编译 + 写快捷方式，慢一些

_registered = None                  # None = 还没查过；True/False = 已确认
_register_lock = threading.Lock()   # 启动线程和「检查更新」可能同时进来


def available() -> bool:
    """只有 Windows 才有这套 API。"""
    return os.name == "nt"


def _text(value) -> str:
    """XML 正文转义（标题 / 正文里可能有 ``<`` ``&``）。"""
    return _escape(str(value))


def _attr(value) -> str:
    """XML 属性值转义（多管引号）。"""
    return _escape(str(value), {'"': "&quot;", "'": "&apos;"})


def payload(title: str, body: str, *, button: str | None = None,
            url: str | None = None, tag: str | None = None,
            group: str | None = None) -> str:
    """拼出 toast 的 XML。``button`` + ``url`` 都给了才会带那颗按钮。

    ⚠️ **``tag`` / ``group`` 不能省成 None 就不管了**：Windows 会把「同一个应用、
    内容一模一样」的 toast 去重（短时间内的第二条只进通知中心、**不弹横幅**）——
    连点两次「检查更新」时第二条就悄无声息。带上 ``tag``+``group`` 之后，同一条会被
    当成「**更新**那条通知」（先替换再显示），横幅照弹。Tag 上限 16 字符。
    """
    attrs = []
    if url:
        attrs.append('activationType="protocol" launch="%s"' % _attr(url))
    if tag:
        attrs.append('tag="%s"' % _attr(tag[:16]))
    if group:
        attrs.append('group="%s"' % _attr(group[:64]))
    parts = [
        "<toast%s>" % (" " + " ".join(attrs) if attrs else ""),
        '<visual><binding template="ToastGeneric">',
        "<text>%s</text>" % _text(title),
        "<text>%s</text>" % _text(body),
        "</binding></visual>",
    ]
    if button and url:
        parts.append('<actions><action content="%s" activationType="protocol" '
                     'arguments="%s" /></actions>' % (_attr(button), _attr(url)))
    parts.append("</toast>")
    return "".join(parts)


def _script(payload_xml: str, app_id: str) -> str:
    """PowerShell 脚本（**全是 ASCII**，XML 以 base64 传）。"""
    blob = base64.b64encode(payload_xml.encode("utf-8")).decode("ascii")
    return (
        "$ErrorActionPreference='Stop';"
        f"$xml=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{blob}'));"
        "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, "
        "ContentType=WindowsRuntime]|Out-Null;"
        "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, "
        "ContentType=WindowsRuntime]|Out-Null;"
        "$doc=New-Object Windows.Data.Xml.Dom.XmlDocument;"
        "$doc.LoadXml($xml);"
        "$toast=New-Object Windows.UI.Notifications.ToastNotification $doc;"
        "[Windows.UI.Notifications.ToastNotificationManager]::"
        f"CreateToastNotifier('{app_id}').Show($toast)"
    )


# ---------------------------------------------------------------- AUMID 注册
def shortcut_path() -> str:
    """开始菜单里那个快捷方式的路径。"""
    base = os.environ.get("APPDATA")
    if not base:
        base = os.path.join(os.path.expanduser("~"), "AppData", "Roaming")
    return os.path.join(base, "Microsoft", "Windows", "Start Menu", "Programs",
                        _SHORTCUT_NAME)


def _ps_str(value: str) -> str:
    """PowerShell 单引号字符串（路径里的 ``'`` 要双写）。"""
    return "'" + str(value).replace("'", "''") + "'"


_PS_REGISTER = r'''
$ErrorActionPreference='Stop'
$lnk = __LNK__
$exe = __EXE__
Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
using System.Text;

[ComImport, Guid("000214F9-0000-0000-C000-000000000046"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IShellLinkW {
    void GetPath([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder pszFile, int cch, IntPtr pfd, uint fFlags);
    void GetIDList(out IntPtr ppidl);
    void SetIDList(IntPtr pidl);
    void GetDescription([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder pszName, int cch);
    void SetDescription([MarshalAs(UnmanagedType.LPWStr)] string pszName);
    void GetWorkingDirectory([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder pszDir, int cch);
    void SetWorkingDirectory([MarshalAs(UnmanagedType.LPWStr)] string pszDir);
    void GetArguments([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder pszArgs, int cch);
    void SetArguments([MarshalAs(UnmanagedType.LPWStr)] string pszArgs);
    void GetHotkey(out short pwHotkey);
    void SetHotkey(short wHotkey);
    void GetShowCmd(out int piShowCmd);
    void SetShowCmd(int iShowCmd);
    void GetIconLocation([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder pszIconPath, int cch, out int piIcon);
    void SetIconLocation([MarshalAs(UnmanagedType.LPWStr)] string pszIconPath, int iIcon);
    void SetRelativePath([MarshalAs(UnmanagedType.LPWStr)] string pszPathRel, int dwReserved);
    void Resolve(IntPtr hwnd, int fFlags);
    void SetPath([MarshalAs(UnmanagedType.LPWStr)] string pszFile);
}

[ComImport, Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IPropertyStore {
    int GetCount(out uint c);
    int GetAt(uint i, out PropertyKey k);
    int GetValue(ref PropertyKey k, out PropVariant v);
    int SetValue(ref PropertyKey k, ref PropVariant v);
    int Commit();
}

[ComImport, Guid("0000010B-0000-0000-C000-000000000046"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
interface IPersistFile {
    int GetClassID(out Guid c);
    int IsDirty();
    void Load([MarshalAs(UnmanagedType.LPWStr)] string p, int mode);
    void Save([MarshalAs(UnmanagedType.LPWStr)] string p, [MarshalAs(UnmanagedType.Bool)] bool remember);
    void SaveCompleted([MarshalAs(UnmanagedType.LPWStr)] string p);
    void GetCurFile([MarshalAs(UnmanagedType.LPWStr)] out string p);
}

[StructLayout(LayoutKind.Sequential)]
struct PropertyKey { public Guid fmtid; public uint pid; }

[StructLayout(LayoutKind.Sequential)]
struct PropVariant {
    public ushort vt;
    public ushort w1;
    public ushort w2;
    public ushort w3;
    public IntPtr p;
}

public static class AumidShortcut {
    // PKEY_AppUserModel_ID = {9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3}, pid 5
    static PropertyKey Key() {
        PropertyKey k = new PropertyKey();
        k.fmtid = new Guid("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3");
        k.pid = 5;
        return k;
    }
    public static void Create(string lnkPath, string exePath, string appId) {
        Type t = Type.GetTypeFromCLSID(new Guid("00021401-0000-0000-C000-000000000046"));
        object o = Activator.CreateInstance(t);
        IShellLinkW link = (IShellLinkW)o;
        link.SetPath(exePath);
        link.SetWorkingDirectory(System.IO.Path.GetDirectoryName(exePath));
        link.SetDescription("Bedrock Recipe Renderer X");
        IPropertyStore store = (IPropertyStore)o;
        PropertyKey k = Key();
        PropVariant v = new PropVariant();
        v.vt = 31;                              // VT_LPWSTR
        v.p = Marshal.StringToCoTaskMemUni(appId);
        try {
            int hr = store.SetValue(ref k, ref v);
            if (hr != 0) throw new Exception("SetValue hr=" + hr);
            store.Commit();
        } finally {
            Marshal.FreeCoTaskMem(v.p);
        }
        ((IPersistFile)o).Save(lnkPath, true);
    }
}
"@
[AumidShortcut]::Create($lnk, $exe, '__APP_ID__')
'''


def _create_shortcut(lnk: str, exe: str) -> bool:
    """起一次 PowerShell 把快捷方式建出来（带 AUMID）。成功返回 True。"""
    script = (_PS_REGISTER
              .replace("__LNK__", _ps_str(lnk))
              .replace("__EXE__", _ps_str(exe))
              .replace("__APP_ID__", APP_ID))
    try:
        done = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive",
             "-ExecutionPolicy", "Bypass", "-Command", script],
            stdin=subprocess.DEVNULL, capture_output=True,
            timeout=REGISTER_TIMEOUT, creationflags=_CREATE_NO_WINDOW)
    except Exception:
        return False
    return done.returncode == 0 and os.path.exists(lnk)


def ensure_registered() -> bool:
    """确保开始菜单里有带 AUMID 的快捷方式（没有就建一次）。幂等、线程安全。

    **不建它 Toast 就不会显示横幅**（见模块文档）。已经有就直接返回，不起进程，
    所以可以放心在启动线程和每次 ``toast()`` 里都调一遍。
    """
    global _registered
    if not available():
        return False
    if _registered is True:
        return True
    with _register_lock:
        if _registered is True:
            return True
        lnk = shortcut_path()
        if os.path.exists(lnk):
            _registered = True
            return True
        exe = sys.executable or ""
        if not exe:
            return False
        _registered = _create_shortcut(lnk, exe)
        return _registered


def toast(title: str, body: str, *, button: str | None = None,
          url: str | None = None, tag: str | None = None,
          group: str = "bedrock-recipe-renderer",
          timeout: float = TIMEOUT) -> bool:
    """弹一条系统通知，成功返回 ``True``。

    ``button`` 给了才带按钮（按钮点开 ``url``）；``tag`` 见 ``payload`` 的说明
    —— 不给的话同一条内容重复弹会被系统去重、只进通知中心不弹横幅。
    """
    if not available():
        return False
    ensure_registered()                 # 没有 AUMID 身份 → 横幅不弹（见模块文档）
    xml_text = payload(title, body, button=button, url=url, tag=tag, group=group)
    for app_id in (APP_ID, FALLBACK_APP_ID):
        try:
            done = subprocess.run(
                ["powershell.exe", "-NoProfile", "-NonInteractive",
                 "-ExecutionPolicy", "Bypass", "-Command", _script(xml_text, app_id)],
                stdin=subprocess.DEVNULL, capture_output=True,
                timeout=timeout, creationflags=_CREATE_NO_WINDOW)
        except Exception:                   # 起不来 / 超时：换下一个 AUMID 再试
            continue
        if done.returncode == 0:
            return True
    return False
