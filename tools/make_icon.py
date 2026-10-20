"""Generate the window / exe icon.

默认**不碰图标本体**，只拿现成的 ``assets/icon.png`` 生成多尺寸 ``assets/icon.ico``
（pe 资源 / 任务栏 / 标题栏都用它）。这里唯一的机关是**按尺寸把格子缝撑宽**：

    直接从 256 缩，缝宽 = 8 * size / 256 —— 16 档只剩 0.5 物理像素，降采样时
    被平均成半透明，格子糊成一团。Windows 在 175% 缩放下把标题栏图标做成
    28px，挑 16/24 档时正好撞上这个，看起来就是「logo 过于模糊」。

所以缝窄于 ``SEAM_MIN_PX`` 的那些档，先用 ``MinFilter`` 腐蚀 alpha（缝等宽变宽）
再 LANCZOS 缩下来；32 档以上缝本来就有 1px+，保持原样不动。

``--render`` 才调用工具自己的方块渲染器重画 ``assets/icon.png``。⚠️ 它渲染的是
``crafting_table`` 的**方块纹理**，和仓库里这版带 X 徽标的图标不是一张图，
所以默认不碰 png（以前这个脚本一跑就把图标换掉了）。
"""
from __future__ import annotations

import argparse
import os
import sys

from PIL import Image, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

OUT = os.path.join(ROOT, "assets")
PNG = os.path.join(OUT, "icon.png")
ICO = os.path.join(OUT, "icon.ico")

ICO_SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]

SEAM_MIN_PX = 0.9      # 缝窄于这个物理像素就得撑宽
SEAM_WANT_PX = 1.0     # 撑到这么宽
ALPHA_OPAQUE = 8       # 低于它的算「缝」


def _seam_width(img: Image.Image) -> float:
    """量格子之间透明缝的宽度。

    只在**正好穿过两条竖缝**的那些行上量（也就是格子行的中间），再取出现次数最多的
    那个组合 —— 横缝行整行都是透明的、格子圆角又会贡献一两像素的碎段，
    直接对所有透明段取中位数会量出偏小的假值（实测 8px 的缝量成 5px）。
    """
    w, h = img.size
    alpha = img.split()[3]
    tally: dict = {}
    for y in range(h):
        runs, run = [], None
        for x in range(w):
            if alpha.getpixel((x, y)) < ALPHA_OPAQUE:
                if run is None:
                    run = x
            elif run is not None:
                if run > 0:                       # 前面有实心像素，才是缝
                    runs.append(x - run)
                run = None
        if len(runs) == 2:                        # 正好两条竖缝 → 这行在格子行里
            key = (runs[0], runs[1])
            tally[key] = tally.get(key, 0) + 1
    if not tally:
        return 0.0
    best = max(tally.items(), key=lambda kv: kv[1])[0]
    return sum(best) / len(best)


def _fit(img: Image.Image, size: int, seam: float) -> Image.Image:
    """出一档：缝太窄就腐蚀 alpha 把缝撑宽，再 LANCZOS 缩到 ``size``。"""
    shrink = size / img.size[0]
    if seam <= 0 or seam * shrink >= SEAM_MIN_PX:
        return img.resize((size, size), Image.Resampling.LANCZOS)
    need = SEAM_WANT_PX * img.size[0] / size      # 想达到的缝宽（换算回原图）
    k = max(0, int(round(need - seam))) + 1       # MinFilter(k) 让缝变宽 k-1
    if k % 2 == 0:
        k += 1
    if k <= 1:
        return img.resize((size, size), Image.Resampling.LANCZOS)
    r, g, b, a = img.split()
    thinner = Image.merge("RGBA", (r, g, b, a.filter(ImageFilter.MinFilter(k))))
    return thinner.resize((size, size), Image.Resampling.LANCZOS)


def write_ico(src: Image.Image, path: str = ICO, sizes=ICO_SIZES) -> float:
    """把 ``src``（通常是 256 的 icon.png）写成多尺寸 ico，返回量到的缝宽。

    走 ``append_images`` 而不是 ``sizes=``：Pillow 遇到尺寸匹配的那一帧会**直接用**，
    否则会从原图重新缩 —— 后者的 LANCZOS 还是救不了 16 档的缝。
    """
    seam = _seam_width(src)
    frames = [_fit(src, s[0], seam) for s in sizes]
    src.save(path, format="ICO", append_images=frames, sizes=sizes)
    return seam


def render_png() -> None:
    """用工具自己的方块渲染器重画 ``assets/icon.png``（``--render`` 才走）。"""
    import json

    from mcrender.assets import Assets

    cfg_path = os.path.join(ROOT, "config.json")
    cfg = {}
    if os.path.exists(cfg_path):
        with open(cfg_path, encoding="utf-8") as fh:
            cfg = json.load(fh)

    assets = Assets(bp_dir=cfg.get("bp"), rp_dir=cfg.get("rp"),
                    vanilla_dir=cfg.get("vanilla"), tool_root=ROOT,
                    wiki_icons="never", vanilla_download=True)

    icon = assets.resolve("minecraft:crafting_table")
    if icon.kind == "missing":
        print("crafting_table texture not found; falling back to stone")
        icon = assets.resolve("minecraft:stone")
    base = icon.render(64)
    print("base render:", icon.kind, base.size)

    big = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    big.paste(base.resize((256, 256), Image.NEAREST), (0, 0))
    big.save(PNG)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="从 assets/icon.png 生成多尺寸 icon.ico（小尺寸自动撑宽格子缝）")
    ap.add_argument("--render", action="store_true",
                    help="先用方块渲染器重画 assets/icon.png（会覆盖现有图标）")
    args = ap.parse_args()

    os.makedirs(OUT, exist_ok=True)
    if args.render:
        render_png()
    if not os.path.exists(PNG):
        print("没有 assets/icon.png；加 --render 先生成一张")
        return 1

    src = Image.open(PNG).convert("RGBA")
    seam = write_ico(src)
    print(f"icon.png {src.size}，缝宽 {seam:.0f}px")
    for size, _ in ICO_SIZES:
        shown = seam * size / src.size[0]
        print(f"  {size:3d}px  缝 {shown:4.2f}px  "
              f"{'原样' if shown >= SEAM_MIN_PX else '撑宽'}")
    print("wrote", ICO)
    return 0


if __name__ == "__main__":
    sys.exit(main())
