"""Application services: paths, configuration, add-on handling, batch rendering.

This layer deliberately imports **no GUI toolkit**.  Both the command line
(``--headless`` / ``--sync-vanilla``) and the CustomTkinter window in
:mod:`mcrender.ui` are built on top of it, so the rendering behaviour is
identical whichever entry point you use.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import traceback
import zipfile

from . import icon as icon_mod
from .assets import Assets
from .recipes import parse
from .render import Renderer

__all__ = [
    "ARCHIVE_EXTS", "CONFIG_PATH", "DEFAULTS", "Generator",
    "app_dir", "bundle_dir", "find_matching_rp", "find_recipes", "icon_path",
    "is_archive", "load_config", "manifest_info", "pack_kinds", "save_config",
    "seed_caches", "unpack_archive",
]

_PKG_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_PKG_DIR)          # project root when running from source


def _frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def app_dir() -> str:
    """配置和缓存所在目录（打包成 exe 后就是 exe 所在目录）。"""
    if _frozen():
        return os.path.dirname(os.path.abspath(sys.executable))
    return ROOT


def bundle_dir() -> str:
    """打包进去的只读数据所在目录。"""
    if _frozen():
        return getattr(sys, "_MEIPASS", app_dir())
    return ROOT


def icon_path() -> str | None:
    """窗口 / exe 图标：用工具自己渲染的工作台。"""
    for root in (app_dir(), bundle_dir(), ROOT):
        p = os.path.join(root, "assets", "icon.ico")
        if os.path.isfile(p):
            return p
    return None


def seed_caches() -> None:
    """打包成 exe 时，把随包携带的缓存拷到 exe 旁边（第一次运行时做一次）。"""
    if not _frozen():
        return
    for name in ("vanilla_cache", "wiki_cache"):
        src = os.path.join(bundle_dir(), name)
        dst = os.path.join(app_dir(), name)
        if os.path.isdir(src) and not os.path.isdir(dst):
            try:
                shutil.copytree(src, dst)
            except Exception:
                pass
    icon_src = os.path.join(bundle_dir(), "assets")
    icon_dst = os.path.join(app_dir(), "assets")
    if os.path.isdir(icon_src) and not os.path.isdir(icon_dst):
        try:
            shutil.copytree(icon_src, icon_dst)
        except Exception:
            pass


CONFIG_PATH = os.path.join(app_dir(), "config.json")
ARCHIVE_EXTS = (".mcaddon", ".mcpack", ".mcworld", ".zip", ".mcproject")
DEFAULTS = {
    # every path starts empty on purpose: the tool ships with no personal paths, and
    # config.json (written next to the exe) is meant to stay out of version control
    "bp": "",
    "rp": "",
    "vanilla": "",
    "out": "",
    "out_subdir": "合成表",
    "scale": 4,
    "layout": "modern",
    "cube_shading": "vanilla",
    "wiki_icons": "auto",
    "wiki_language": "en",
    "vanilla_download": True,
    # turned on automatically once "补全原版资源" has fetched everything: from then on
    # rendering reads only local files and never waits for the network again
    "local_only": False,
    # 界面外观：system 跟随系统浅色 / 深色，也可以手动钉死
    "appearance": "system",
}


def load_config() -> dict:
    cfg = dict(DEFAULTS)
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, encoding="utf-8") as fh:
                cfg.update({k: v for k, v in json.load(fh).items() if v is not None})
        except Exception:
            pass
    return cfg


def save_config(cfg: dict) -> None:
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, ensure_ascii=False, indent=2)
    except Exception:
        pass


# --------------------------------------------------------------------- packs
def is_archive(path: str) -> bool:
    return path.lower().endswith(ARCHIVE_EXTS)


def pack_kinds(root: str) -> set:
    """Which pack types a folder is, from its manifest.json modules."""
    manifest = os.path.join(root, "manifest.json")
    if not os.path.isfile(manifest):
        return set()
    try:
        with open(manifest, encoding="utf-8-sig") as fh:
            data = json.load(fh)
    except Exception:
        return set()
    kinds = set()
    for module in data.get("modules", []) or []:
        kind = str(module.get("type", "")).lower()
        if kind == "data":
            kinds.add("bp")
        elif kind in ("resources", "client_data"):
            kinds.add("rp")
    return kinds


def manifest_info(root: str) -> dict:
    path = os.path.join(root, "manifest.json")
    try:
        with open(path, encoding="utf-8-sig") as fh:
            data = json.load(fh)
    except Exception:
        return {}
    return data or {}


def find_matching_rp(bp_path: str) -> str | None:
    """找一个行为包配套的资源包：先按 com.mojang 目录约定，再按 manifest 依赖 uuid。"""
    bp_path = os.path.abspath(bp_path.rstrip("\\/"))
    base = os.path.basename(bp_path)
    parent = os.path.dirname(bp_path)

    names = []
    for old, new in (("(BP)", "(RP)"), ("(bp)", "(rp)"), ("_BP", "_RP"), ("_bp", "_rp"),
                     ("-BP", "-RP"), ("-bp", "-rp"), ("BP", "RP"), ("bp", "rp")):
        if old in base:
            names.append(base.replace(old, new))
    names.append(base)

    roots = []
    if "development_behavior_packs" in parent:
        roots.append(parent.replace("development_behavior_packs", "development_resource_packs"))
    roots.append(parent)
    roots.append(os.path.join(os.path.dirname(parent), "development_resource_packs"))

    for root in roots:
        for name in names:
            cand = os.path.join(root, name)
            if os.path.isdir(cand) and "rp" in pack_kinds(cand):
                return cand

    # fall back to the uuid link: an RP that depends on this BP's header uuid
    uuid = (manifest_info(bp_path).get("header") or {}).get("uuid")
    if uuid:
        for root in roots:
            if not os.path.isdir(root):
                continue
            try:
                entries = os.listdir(root)
            except OSError:
                continue
            for name in entries:
                cand = os.path.join(root, name)
                if not os.path.isdir(cand) or cand == bp_path:
                    continue
                for dep in (manifest_info(cand).get("dependencies") or []):
                    if isinstance(dep, dict) and dep.get("uuid") == uuid:
                        return cand
    return None


def unpack_archive(path: str, workroot: str, depth: int = 0) -> tuple:
    """Extract an add-on archive, unpacking nested archives too.

    Many ``.mcaddon`` / ``.mcpack`` downloads are zipped twice: the outer archive
    holds another ``.mcpack``/``.zip`` (or the packs sit in a subfolder), so every
    nested archive is extracted as well, up to a sane depth.
    """
    dest = tempfile.mkdtemp(prefix=f"mcaddon{depth}_", dir=workroot)
    nested = []
    try:
        with zipfile.ZipFile(path) as zf:
            zf.extractall(dest)
    except zipfile.BadZipFile:
        return [], [], dest
    for root, _dirs, files in os.walk(dest):
        for name in files:
            if name.lower().endswith(ARCHIVE_EXTS):
                nested.append(os.path.join(root, name))

    bp, rp = [], []
    for root, _dirs, files in os.walk(dest):
        if "manifest.json" in files:
            kinds = pack_kinds(root)
            if "bp" in kinds:
                bp.append(root)
            if "rp" in kinds:
                rp.append(root)

    if depth < 4:
        for inner in nested:
            if os.path.abspath(inner) == os.path.abspath(path):
                continue
            sub_bp, sub_rp, _ = unpack_archive(inner, workroot, depth + 1)
            bp += sub_bp
            rp += sub_rp
    return bp, rp, dest


def find_recipes(root: str) -> list:
    """All recipe jsons under a folder (prefers a 'recipes' subfolder when present)."""
    preferred = os.path.join(root, "recipes")
    base = preferred if os.path.isdir(preferred) else root
    out = []
    for r, _dirs, files in os.walk(base):
        out += [os.path.join(r, f) for f in sorted(files) if f.lower().endswith(".json")]
    return out


# ----------------------------------------------------------------------- core
class Generator:
    """解析配方 → 出图；纹理索引按（BP, RP）组合缓存，批量很快。"""

    def __init__(self, cfg: dict, log=print):
        self.cfg = cfg
        self.log = log
        self._assets_cache: dict = {}
        self._tmp: list = []

    # -- assets
    def assets_for(self, bp=None, rp=None):
        bp = list(bp) if bp else ([self.cfg["bp"]] if self.cfg.get("bp") else [])
        rp = list(rp) if rp else ([self.cfg["rp"]] if self.cfg.get("rp") else [])
        key = (tuple(bp), tuple(rp))
        if key not in self._assets_cache:
            self.log("读取资源包索引…")
            local_only = bool(self.cfg.get("local_only"))
            self._assets_cache[key] = Assets(
                bp_dir=bp, rp_dir=rp, vanilla_dir=self.cfg.get("vanilla"),
                wiki_root=app_dir(), wiki_icons=self.cfg.get("wiki_icons", "auto"),
                wiki_language=self.cfg.get("wiki_language", "en"),
                vanilla_download=bool(self.cfg.get("vanilla_download", True)),
                local_only=local_only,
                tool_root=app_dir(), bundle_root=bundle_dir())
            src = self._assets_cache[key].vanilla
            self.log(f"原版资源：{src.status()}"
                     + ("（只用本地，不联网）" if local_only else ""))
        return self._assets_cache[key]

    # -- output
    def out_root(self) -> str:
        """导出根目录：在用户选的输出目录里再建一个「合成表」子文件夹。

        输出目录本身就叫「合成表」时不再嵌套，避免出现 合成表\\合成表。
        """
        base = (self.cfg.get("out") or "").strip()
        if not base:
            return ""
        sub = (self.cfg.get("out_subdir") or "").strip()
        if sub and os.path.basename(os.path.normpath(base)) != sub:
            return os.path.join(base, sub)
        return base

    # -- batch
    def run(self, paths) -> list:
        results = []
        for path in paths:
            try:
                results += self._run_one(path)
            except Exception:
                self.log("✗ " + str(path) + "：\n" + traceback.format_exc())
        if results:
            self.log(f"\n完成：{len(results)} 张 → {self.out_root()}")
        else:
            self.log("\n没有生成任何图片")
        return results

    def _run_one(self, path: str) -> list:
        if not os.path.exists(path):
            self.log(f"跳过 {path}：路径不存在")
            return []
        if is_archive(path):
            work = tempfile.mkdtemp(prefix="mcwork_", dir=tempfile.gettempdir())
            self._tmp.append(work)
            bp, rp, dest = unpack_archive(path, work)
            self.log(f"解包 {os.path.basename(path)}：BP {len(bp)} 个、RP {len(rp)} 个")
            if not bp and not rp:
                self.log("  ↳ 里面没有 manifest.json，按普通压缩包处理")
                return self.render_files(find_recipes(dest), bp or None, rp or None)
            files = []
            for root in bp:
                files += find_recipes(root)
            return self.render_files(files, bp or None, rp or None)

        if os.path.isdir(path):
            kinds = pack_kinds(path)
            if kinds:
                self.log(f"{os.path.basename(path)} 是一个"
                         f"{'行为包' if 'bp' in kinds else ''}"
                         f"{'资源包' if 'rp' in kinds else ''}")
                if "bp" not in kinds:
                    return []                # 只是资源包，里面没有配方
                bp = [path]
                rp = [path] if "rp" in kinds else None
                if rp is None:
                    paired = find_matching_rp(path)
                    if paired:
                        self.log(f"自动配对的资源包：{paired}")
                        rp = [paired]
                    elif self.cfg.get("rp"):
                        self.log(f"使用配置里的资源包：{self.cfg['rp']}")
                return self.render_files(find_recipes(path), bp, rp)
            return self.render_files(find_recipes(path), None, None)

        if path.lower().endswith(".json"):
            return self.render_files([path], None, None)
        self.log(f"跳过 {os.path.basename(path)}：不认识的文件类型")
        return []

    def render_files(self, files: list, bp=None, rp=None) -> list:
        cfg = self.cfg
        if not (cfg.get("out") or "").strip():
            self.log("还没有选输出目录 —— 请点「输出目录」那一行的「选择…」后再出图")
            return []
        out_dir = self.out_root()
        os.makedirs(out_dir, exist_ok=True)
        if not files:
            self.log("没有找到 recipe json")
            return []
        icon_mod.set_shading(cfg.get("cube_shading", "vanilla"))
        renderer = Renderer(self.assets_for(bp, rp), scale=int(cfg.get("scale", 4)),
                            layout=cfg.get("layout", "modern"), background=None)
        done = []
        for path in files:
            rel = self._relative(path)
            try:
                rec = parse(path)
            except Exception as exc:
                self.log(f"✗ {os.path.basename(path)}：JSON 读不了（{exc}）")
                continue
            if rec is None or rec.kind == "unknown":
                self.log(f"· {os.path.basename(path)}：不是合成表配方，跳过")
                continue
            target = os.path.join(out_dir, os.path.splitext(rel)[0] + ".png")
            try:
                img, _lay = renderer.render(rec)
            except Exception as exc:
                self.log(f"✗ {os.path.basename(path)}：{exc}")
                continue
            os.makedirs(os.path.dirname(target), exist_ok=True)
            img.save(target)
            done.append(target)
            self.log(f"✓ {rec.identifier or os.path.basename(path)}  →  {target}")
        for warning in sorted(set(renderer.warnings)):
            self.log("   ! " + warning)
        renderer.warnings.clear()
        return done

    def _relative(self, path: str) -> str:
        path = os.path.abspath(path)
        folder = os.path.dirname(path)
        for _ in range(4):
            if os.path.basename(folder).lower() == "recipes":
                return os.path.relpath(path, folder)
            parent = os.path.dirname(folder)
            if parent == folder:
                break
            folder = parent
        return os.path.basename(path)

    def prefetch(self, pack_root: str) -> dict:
        """把某个包所有配方要用到的贴图先下好（省得正式出图时一张一张下）。"""
        files = find_recipes(pack_root)
        kinds = pack_kinds(pack_root)
        rp = None
        if "rp" not in kinds:
            paired = find_matching_rp(pack_root)
            rp = [paired] if paired else None
            if paired:
                self.log(f"预取用配套资源包：{paired}")
        assets = self.assets_for([pack_root], rp)
        ids = set()
        recipes = 0
        for path in files:
            try:
                rec = parse(path)
            except Exception:
                continue
            if rec is None or rec.kind == "unknown":
                continue
            recipes += 1
            ids.update(i for i in rec.items() if i)
        before = assets.vanilla.downloaded
        missing = []
        for ident in sorted(ids):
            try:
                icon = assets.resolve(ident)
                icon.render(16)
                if icon.kind == "missing":
                    missing.append(ident)
            except Exception:
                missing.append(ident)
        return {"recipes": recipes, "items": len(ids),
                "downloaded": assets.vanilla.downloaded - before, "missing": missing}

    def cleanup(self):
        for tmp in self._tmp:
            shutil.rmtree(tmp, ignore_errors=True)
        self._tmp.clear()
