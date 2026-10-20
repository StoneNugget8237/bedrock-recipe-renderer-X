"""检查更新这条链路的自检脚本（不依赖窗口，也不用真联网）。

    python tools/check_update.py            # 只跑 semver 比较表（离线、瞬时）
    python tools/check_update.py --live     # 再问一次 GitHub，打印拿到的 tag 与判定
    python tools/check_update.py --toast    # 再真弹一条通知（顺带注册 AUMID 快捷方式）

为什么单开一个脚本：``smoke_ui`` 只验「按钮 → 通知」的接线（网络和通知都被替身掉了），
版本比较这一层是纯逻辑，值得独立钉死 —— ``v1.10.0`` 跟 ``v1.9.0`` 谁大、``-rc.1``
算不算新，这些用眼睛看是看不出错的。

版本比较的期望值来自 semver 规范（https://semver.org/lang/zh-CN/#11-优先层级说明）：
预发布标识的优先级低于正式版、数字段按数值比、段数多的更大、``+build`` 不参与比较。
"""
from __future__ import annotations

import argparse
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from mcrender import __version__, notify, update          # noqa: E402

# (候选, 当前, 期望「候选更新」)
CASES = (
    ("v1.1.0", "1.1.0", False),
    ("v1.1.1", "1.1.0", True),
    ("v1.10.0", "1.9.9", True),             # 字符串比大小会得出相反答案
    ("1.9.9", "1.10.0", False),
    ("v2.0.0", "1.99.99", True),
    ("v1.2", "1.1.9", True),                # 只写两段也认
    ("v1.1.0-rc.1", "1.1.0", False),        # 预发布低于正式版
    ("1.1.0", "1.1.0-rc.1", True),
    ("1.1.0-rc.2", "1.1.0-rc.1", True),
    ("1.1.0-rc.10", "1.1.0-rc.9", True),    # 数字段按数值
    ("1.1.0-alpha", "1.1.0-alpha.1", False),  # 前缀相同 → 段数多的更大
    ("1.1.0+build.7", "1.1.0", False),      # build 元数据不参与
)


def check_semver() -> int:
    print("== semver 比较（离线） ==")
    bad = 0
    for tag, current, want in CASES:
        got = update.is_newer(tag, current)
        ok = got == want
        bad += not ok
        print(f"  {'✓' if ok else '✗'} {tag:>14} vs {current:<10} → 更新={got}"
              f"（应为 {want}）")
    for junk in ("latest", "", "v", "1.x.0"):
        try:
            update.is_newer(junk, "1.0.0")
        except update.UpdateError:
            print(f"  ✓ {junk!r:>8} 认不出 → 报 UpdateError")
        else:
            print(f"  ✗ {junk!r:>8} 认不出却当成了能比")
            bad += 1
    print(f"  {len(CASES) + 4} 条，失败 {bad}\n")
    return bad


def check_live() -> int:
    print("== 问一次 GitHub（当前版本 v%s）==" % __version__)
    try:
        result = update.check(__version__)
    except update.UpdateError as exc:
        print(f"  ! 拿不到：{exc}")
        print("   （限流 / 断网都算正常情况，界面那边会弹「检查更新失败」）\n")
        return 0
    print(f"  最新 tag：{result['tag']}；比当前新：{result['newer']}")
    print(f"  下载页：{update.RELEASE_URL}\n")
    return 0


def check_toast() -> int:
    print("== 系统通知 ==")
    bad = 0
    # 1) AUMID 必须在开始菜单有个带 System.AppUserModel.ID 的快捷方式 ——
    #    没有它 toast 只进通知中心、横幅永远不弹。
    lnk = notify.shortcut_path()
    if notify.ensure_registered() and os.path.exists(lnk):
        data = open(lnk, "rb").read()
        has_id = notify.APP_ID.encode("utf-16-le") in data
        print(f"  {'✓' if has_id else '✗'} 快捷方式带 AUMID —— {lnk}")
        bad += not has_id
    else:
        print(f"  ✗ 注册 AUMID 快捷方式失败 —— {lnk}")
        bad += 1
    # 2) 真发一条
    ok = notify.toast("新版本可用", "最新发布版：v9.9.9",
                      button="前往下载", url=update.RELEASE_URL)
    print(f"  {'✓ 已发出' if ok else '✗ 发不出去（非 Windows，或这台机器挡住了）'}")
    print("  点通知上的「前往下载」应当打开浏览器。\n")
    return 0 if ok and not bad else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="检查更新自检")
    parser.add_argument("--live", action="store_true", help="真问一次 GitHub")
    parser.add_argument("--toast", action="store_true", help="真弹一条通知")
    args = parser.parse_args(argv)

    bad = check_semver()
    if args.live:
        bad += check_live()
    if args.toast:
        bad += check_toast()
    print("全部通过 ✓" if not bad else f"有 {bad} 项失败 ✗")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
