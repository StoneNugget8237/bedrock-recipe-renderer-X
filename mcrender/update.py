"""检查更新：读 GitHub Releases 的最新 tag，按 semver 跟当前版本比。

分三层，方便**脱 GUI、断网**单测：

- ``parse_version`` / ``compare`` / ``is_newer`` —— 纯字符串运算，不会碰网络；
- ``latest_tag`` —— 唯一碰网络的地方（stdlib ``urllib``，带超时与 User-Agent）；
- ``check`` —— 把两者拼起来，返回 ``{"tag": ..., "newer": bool}``。

不引第三方 HTTP 库：这个工具是绿色免安装的，为一个 GET 请求加个依赖不值当。

**semver 的比较规则**（比字符串比大小靠谱得多，``v1.10.0`` 不该小于 ``v1.9.0``）：

1. 主版本 / 次版本 / 修订号逐段按数字比；
2. 三段都相同时，**带预发布标识的更低**（``1.1.0-rc1 < 1.1.0``）；
3. 两边都带预发布时，按标识逐段比：纯数字段按数值、含字母段按 ASCII，数字段更低；
   前缀都相同则**段数多的那个更大**（``-rc.1 < -rc.1.1``）；
4. ``+build`` 元数据不参与比较。

GitHub 的 tag 一般写成 ``v1.1.0``，开头的 ``v`` 容忍。宽松处理：只写两段（``1.2``）
也认，缺的段按 0 算 —— 但缺主版本号（比如 ``latest``）就判为「认不出」。
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

# 「检查更新」用的仓库 / 页面。改仓库只改这两行。
API_URL = ("https://api.github.com/repos/StoneNugget8237/"
           "bedrock-recipe-renderer-X/releases/latest")
RELEASE_URL = ("https://github.com/StoneNugget8237/"
               "bedrock-recipe-renderer-X/releases/latest")

TIMEOUT = 6.0                   # 秒：点按钮的人等不了太久，断了就当失败
FALLBACK_TIMEOUT = 4.0          # 退路给短一点：反正已经等过一次了

_SEMVER = re.compile(
    r"^[vV]?(\d+)(?:\.(\d+))?(?:\.(\d+))?"
    r"(?:-([0-9A-Za-z.\-]+))?(?:\+([0-9A-Za-z.\-]+))?$")


class UpdateError(RuntimeError):
    """检查更新失败（网络、HTTP 状态、返回格式、版本号认不出）。"""


def parse_version(text) -> tuple | None:
    """``"v1.1.0-rc.1+build"`` → ``(1, 1, 0, ("rc", "1"))``；认不出返回 ``None``。

    第四段是预发布标识（没有就是空元组）；``+build`` 直接丢掉。
    """
    match = _SEMVER.match(str(text).strip())
    if not match:
        return None
    major, minor, patch = (int(g) if g else 0 for g in match.group(1, 2, 3))
    pre = tuple(match.group(4).split(".")) if match.group(4) else ()
    return (major, minor, patch, pre)


def compare(left: tuple, right: tuple) -> int:
    """比两个已解析的版本：左小于右返回 -1、相等 0、大于 1（semver 的优先级规则）。"""
    for i in range(3):
        if left[i] != right[i]:
            return 1 if left[i] > right[i] else -1
    a, b = left[3], right[3]
    if not a or not b:                      # 没有预发布标识的更大
        return 0 if a == b else (-1 if a else 1)
    for x, y in zip(a, b):
        if x == y:
            continue
        x_num, y_num = x.isdigit(), y.isdigit()
        if x_num and y_num:
            return 1 if int(x) > int(y) else -1
        if x_num != y_num:                  # 数字标识符的优先级比字母低
            return -1 if x_num else 1
        return 1 if x > y else -1
    if len(a) == len(b):                    # 前缀相同 → 段数多的更大
        return 0
    return 1 if len(a) > len(b) else -1


def is_newer(candidate, current) -> bool:
    """``candidate`` 是否真的比 ``current`` 新。任一边认不出就抛 ``UpdateError``。"""
    new, old = parse_version(candidate), parse_version(current)
    if new is None:
        raise UpdateError(f"认不出最新版本号：{candidate}")
    if old is None:
        raise UpdateError(f"认不出当前版本号：{current}")
    return compare(new, old) > 0


def _api_tag(current: str | None, timeout: float, url: str) -> str:
    """请求 GitHub 的 ``releases/latest``，返回 ``tag_name``（失败抛 ``UpdateError``）。"""
    request = urllib.request.Request(url, headers={
        # GitHub 的 API 要求带 User-Agent，不带会直接 403
        "User-Agent": f"BedrockRecipeRendererX/{current or 'unknown'}",
        "Accept": "application/vnd.github+json",
    })
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
    except urllib.error.HTTPError as exc:
        detail, reset = "", None
        try:
            detail = str(json.loads(exc.read() or b"{}").get("message") or "")
        except Exception:
            pass
        try:
            reset = exc.headers.get("X-RateLimit-Reset")
        except Exception:
            reset = None
        # 未认证的 GitHub API 每 IP 每小时只有 60 次；撞上了给个人话，别只甩「HTTP 403」
        if exc.code in (403, 429) and ("rate limit" in detail.lower() or reset):
            when = ""
            if reset and str(reset).isdigit():
                when = "，" + time.strftime("%H:%M", time.localtime(int(reset))) + " 后恢复"
            raise UpdateError(f"GitHub API 限流（未登录 60 次/小时）{when}") from exc
        if exc.code == 404:
            raise UpdateError("还没有发布版（HTTP 404）") from exc
        raise UpdateError(f"HTTP {exc.code}" + (f"：{detail}" if detail else "")) from exc
    except urllib.error.URLError as exc:
        raise UpdateError(f"网络不通：{exc.reason}") from exc
    except OSError as exc:                  # 超时 / 连接被重置
        raise UpdateError(f"网络不通：{exc}") from exc

    try:
        data = json.loads(body.decode("utf-8", "replace"))
    except ValueError as exc:
        raise UpdateError("返回的不是合法 JSON") from exc
    tag = str((data or {}).get("tag_name") or "").strip()
    if not tag:
        raise UpdateError("返回里没有 tag_name")
    return tag


def _tag_from_redirect(current: str | None = None,
                       timeout: float = FALLBACK_TIMEOUT) -> str | None:
    """不走 API 的退路：``releases/latest`` 会 302 到 ``releases/tag/<tag>``，取那段。

    GitHub API 按 IP 限流（不登录 60 次/小时），同一出口 IP 的机器多了很容易撞上；
    HTML 这条没有那个限制。取到的仍然是**官方的 tag 名**，只是不经过 JSON 接口。
    """
    request = urllib.request.Request(RELEASE_URL, headers={
        "User-Agent": f"BedrockRecipeRendererX/{current or 'unknown'}",
    })
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            final = response.geturl() or ""
    except Exception:
        return None
    marker = "/releases/tag/"
    if marker not in final:
        return None
    return urllib.parse.unquote(final.split(marker, 1)[1].strip("/")) or None


def latest_tag(current: str | None = None, timeout: float = TIMEOUT,
               url: str = API_URL) -> str:
    """拿最新发布的 tag：先问 API（要 ``tag_name``），限流时退到页面跳转。

    两条路都失败就抛 ``UpdateError``（优先报 API 那条的原因，更有信息量）。
    """
    try:
        return _api_tag(current, timeout, url)
    except UpdateError:
        tag = _tag_from_redirect(current)
        if tag:
            return tag
        raise


def check(current: str, timeout: float = TIMEOUT, url: str = API_URL) -> dict:
    """拉最新 tag 并跟 ``current`` 比：``{"tag": "v1.2.0", "newer": True}``。"""
    tag = latest_tag(current=current, timeout=timeout, url=url)
    return {"tag": tag, "newer": is_newer(tag, current)}
