# mc_craft_render — 基岩版合成表渲染器

读基岩版 addon 的 `recipes/*.json` + 资源包纹理，输出**和游戏合成台界面一致**的 PNG 合成表
（面板 + 中文标题 + 槽位 + 灰色箭头 + 26×26 产物格 + 数量角标，方块物品用等轴 3D 立方体渲染）。

![示例](docs/example_stone_star.png)

```
mc_craft_render/
├─ mcrender_gui.py           # 入口：分派 headless / sync-vanilla / 图形界面
├─ mcrender/                 # 渲染器（纯 Python + Pillow）
│  ├─ app.py                 # 应用服务：路径/配置/解包/BP-RP 配对/批量出图（不依赖 GUI）
│  ├─ jsonc.py               # 宽容 JSON 解析（BOM / 注释 / 尾逗号 / 多段 JSON）
│  ├─ assets.py              # 物品ID → 纹理（RP → 子包 → 原版；多面纹理；别名/模糊匹配）
│  ├─ icon.py                # 平面图标 + 等轴方块立方体（按输出分辨率栅格化）
│  ├─ fonts.py               # 字模：截图提取字模 + 原版字模页 + 手写 ASCII 数字
│  ├─ glyphs.json            # 从截图里 1:1 还原的 zh_CN 汉字点阵（合成/熔炉/酿造台…）
│  ├─ panel.py               # 面板 / 槽位 / 箭头 sprite
│  ├─ recipes.py             # recipe JSON → 统一结构
│  ├─ render.py              # 布局 + 合成输出
│  ├─ ui/                    # CustomTkinter 界面层（仅开窗口时导入）
│  │  ├─ theme.py            # Fluent 设计令牌 + Mica / 标题栏着色封装
│  │  ├─ window.py           # 主窗口
│  │  ├─ widgets.py          # 自绘控件：FluentComboBox / FluentSwitch
│  │  ├─ icons.py            # 图标（外观三图标位图 / 箭头 / 开关自绘）
│  │  ├─ shape.py            # 纯几何 / 纯数学（圆角顶点、行号反推、缓动曲线）
│  │  ├─ win32.py            # Win32 原语：PrintWindow 抓图 + 分层窗口（逐像素 alpha）
│  │  └─ dnd.py              # Windows 文件拖放（WM_DROPFILES）
│  └─ cli.py                 # 命令行入口（纯渲染）
├─ tools/
│  ├─ verify_reference.py    # 与游戏截图逐像素比对
│  ├─ extract_glyphs.py      # 从截图里提取汉字字模
│  ├─ smoke_ui.py            # 真实窗口冒烟（需带 tkinter 的 Python）
│  ├─ check_option_click.py  # 合成真鼠标事件：点下拉选项不能顺带点到下面的按钮
│  ├─ check_ui_names.py      # 查与 Tk 祖先同名的私有成员
│  ├─ check_borders.py       # 抓真实窗口像素，查四边描边 + 圆角抗锯齿
│  ├─ preview_ui_icons.py    # 离线渲染自绘图形（不需要 tkinter）
│  ├─ preview_ui_combo.py    # 离线渲染下拉面板（不需要 tkinter）
│  └─ screenshot_ui.py       # 真窗口截图（Win32 PrintWindow）
├─ out/                      # 输出（265 张 PNG + _report.md）
└─ README.md
```

## 用法

```powershell
# 任何 Python 3.11+（需要 Pillow）；命令行参数没给就用 config.json 里的值
$py = "python"

# 全量渲染（路径由命令行或 config.json 提供；本程序不内置任何个人路径）
& $py -m mcrender --bp <行为包> --rp <资源包> --out out --scale 4

# 只渲染某几条 / 某类
& $py -m mcrender --filter stone_star --out out
& $py -m mcrender --kind shaped,shapeless --out out
& $py -m mcrender --list                  # 只列配方，不渲染

--scale 4                  # 1 UI 单位 = 几个像素（游戏 GUI scale，默认 4，始终 NEAREST）
--layout modern|classic    # modern = 你现在游戏里的版式（默认，标题居中，面板 176×80）
                           # classic = 你第一张参考图那种紧凑面板（160×80，标题左对齐）
--background none          # none=透明（默认）或 #C6C6C6 / white
--title 合成 --title-size 8
--no-counts
--cube-shading reference   # 用参考图拟合出的方块左右面亮度（默认 vanilla 原版面亮度）
--bp / --rp / --vanilla / --recipes / --out / --report
```

输出保持 `recipes/` 下的目录结构；`out/_report.md` 记录渲染/跳过/缺纹理/警告。

## 分辨率为什么现在不糊了

三条渲染规则，都是照游戏实测的：

| 内容 | 做法 |
| --- | --- |
| 面板 / 槽位 / 箭头 | 按 1 UI 单位 = 1 像素绘制，再整体 NEAREST 放大到 GUI scale（同上表 0.00%~0.6% 误差） |
| **中文标题** | 游戏是把 **16×16 字模直接铺进 8×8 单位字框**（GUI scale 4 时每个字模像素 = 2×2 屏幕像素，笔画 2px 锐利）。之前我先降采样成 8×8 再放大，细节丢一半 → 糊。现在按 `draw_crisp()` 在输出分辨率上贴 16×16 点阵 |
| **物品 / 方块** | 直接在输出分辨率栅格化：16×16 平面图标 NEAREST 放大；方块立方体按 `rotation[30,225,0] scale .625` 在 64×64 上做仿射光栅化，轮廓就是真的 64×64 轮廓，不是 16×16 放大 |

字模来源：samples 包只有 zh_TW / ja_JP 字模页（字形偏小），游戏用的是 zh_CN 全高字形，
所以 `tools/extract_glyphs.py` 从你给的高清截图里把原始 16×16 点阵**精确还原**（截图里字是 2×2 均匀块，
隔点采样即可无损还原），存进 `mcrender/glyphs.json`，优先于字模页使用：

```powershell
& $py -m tools.extract_glyphs <截图.png> "合成" --region 150 16 400 62 --box 203 34
```
（`--box` 是标题 8 单位字框的左上角像素坐标；不给就自动搜索。想要更多字，用别的截图按同样方式提取即可。）

## 校验结果

```powershell
& $py -m tools.verify_reference out\stone_star.png --layout modern
```

对比你最新那张合成台高清截图（GUI scale 4，面板外沿在 (11,12)），**只统计双方都是 GUI 色调的像素**
（物品贴图区域跳过，因为你的截图挂了光影类资源包）：

```
compared 203112 GUI pixels of 215600 (94.2%)
pixel mismatches: 1676 = 0.825%
```

分区误差：背景面板 0.00%、箭头 **0.00%**、空格子 0.62%（只有槽位 4 个角）、标题 0.78%、
面板边框 2.3~2.9%（四角 1~2px 倒角差异，游戏面板宽度本身是 175.75 单位不是整数）。
剩下的 1676 个差异全部落在右上角倒角那几列。

## 支持范围与已知取舍

- **配方类型**：`recipe_shaped`、`recipe_shapeless`、`recipe_furnace`（含 blast_furnace / smoker）、
  `recipe_stonecutter`、`recipe_smithing_transform`、`recipe_brewing_mix/container`。265 条全部出图。
- **版式**：
  - **所有合成统一 3×3 合成台**（不管是 2×2 还是 3×3 的配方），图样/材料从网格**左上角**开始排。
  - **无序合成**（`recipe_shapeless`）在面板**右下角**画原版那个🔀交叉箭头标记（点阵来自你的截图，见 `mcrender/sprites.json`）。
  - 熔炉（输入+燃料空槽+火焰+箭头+产物）、切石机、锻造台（模板/底材/附加）各有版式；
    **酿造台按游戏真实界面重建**（实测你截图的坐标）：燃料空槽在左上、材料槽在上中、三个药水瓶子槽在下排（中间那个低 7 单位，
    与游戏一致）、槽间用管道连接、右侧竖版进度箭头、左下是燃料盘管和燃料条（贴图取自原版 `textures/ui/brewing_*.png`）。
    材料槽 = 配方 `reagent`，**只有中间那个瓶子槽放产物药水**（左右两个只留空瓶底纹，与游戏一致）。
  - 标题按配方 `tags` 取（合成 / 熔炉 / 高炉 / 烟熏炉 / 切石机 / 锻造台 / 酿造台 / 石头工作台 / 石头转换台 / 石头锻造台）。
- **方块模型（非纯方块）**：会读 BP 方块 JSON 的 `minecraft:geometry` + `minecraft:material_instances`，
  以及 RP 的 `models/**/*.geo.json`，按 Bedrock 几何格式真实渲染：
  骨骼父子链 / `pivot` / `rotation`（Z→Y→X）、立方体 `origin`/`size`/`inflate`/`mirror`、
  盒式 UV `[u,v]` 和逐面 UV `{"north":{"uv":…,"uv_size":…}}`、`texture_width/height`；
  输出分辨率下用 **z-buffer 逐像素光栅化**，面亮度按旋转后的法线取（上 1.0 / 南北 0.8 / 东西 0.6）。
  你包里三个自定义模型（`stone_smithing_table`、`mimic_stone_flower`、`ancient_stone_totem_doll`）现在都是真模型视图；
  其余方块是 `minecraft:geometry.full_block`，走普通立方体。
- **原版特殊形状内置库**（`mcrender/shapes.py`）：原版把台阶/楼梯/火把/花草这些形状写死在游戏里，
  addon 读不到模型文件，所以这些形状是本工具自带的，按名字/后缀自动匹配，渲染时走同一套几何管线：
  台阶 `_slab`、楼梯 `_stairs`、地毯 `_carpet`、压力板、雪层、睡莲、活板门、**十字花草**（蒲公英/虞美人/郁金香/各类树苗/草/蘑菇/下界疣/甜浆果…）、
  火把/红石火把/灵魂火把、灯笼、锁链、末地烛、避雷针、营火、栅栏/栅栏门、墙、玻璃板/铁栏杆、
  门、告示牌、旗帜、梯子、脚手架、按钮、拉杆、花盆、铁轨、铁砧、蛋糕、箱子、漏斗、炼药锅、堆肥桶、
  酿造台、切石机、砂轮、讲台、钟、头颅、仙人掌、竹子、床、可可豆等（约 40 种）。
  名字匹配不上时按后缀猜（`*_slab` / `*_stairs` / `*_door` / `*_wall` / `*_pane` / `*_rod` …）；
  想加新形状直接在 `shapes.py` 的 `SHAPES` 里补一条（单位：16=1 格，x/z 居中，y 从方块底部起，每个面给 16×16 贴图上的 UV 矩形）。
  纹理同时支持 `.png` 和 `.tga`。
- **药水**：`potion_type:regeneration` 取 `potion_bottle_regeneration` 等专用图标，不做液体染色。
- **物品标签**（`minecraft:planks` 等）映射到代表物品（木板→橡木木板、`stone_crafting_materials`→圆石），报告里注明。
- **数量角标**：0-9 是按原版 5×7 度量手写的点阵（samples 包不含 ASCII 字模页），按 GUI scale 放大。
- **方块阴影**：默认用原版面亮度（上 1.0 / 南北 0.8 / 东西 0.6）；你截图里的立方体因为光影包而不同，
  `--cube-shading reference` 可切到从截图拟合的亮度。
- **非立方体方块**（花、火把、铁轨等）走平面图标，名单在 `assets.py` 的 `FLAT_BLOCKS`。

---

## 拖拽用法（推荐）

本目录就是"程序"本体，入口是 **`合成表生成器.bat`**：

1. 把 `recipe json` 文件（或整个 `recipes` 文件夹）**拖到 `合成表生成器.bat` 图标上**；
2. 弹出的窗口里会逐条显示 `✓ 配方ID → 输出图片`；
3. 图片输出到**你在界面里选的输出目录**（`config.json` 的 `out`，首次使用为空，需要自己选），
   并保持 json 所在的子目录结构。

窗口里也能拖（把文件拖进窗口的"拖放区域"），还可以用「选择 JSON 文件…」「选择文件夹…」按钮。
版式（modern/classic）、缩放、方块阴影、输出目录都能在窗口里改，改动会写回 `config.json`。

无窗口批量模式（给 bat/脚本调用）：

```powershell
& C:\Python314\python.exe mcrender_gui.py --headless <json或目录> [...]
```

`config.json` 字段：`bp`（行为包，含 recipes/）、`rp`（资源包）、`vanilla`（原版 samples 包）、
`out`、`scale`(GUI scale)、`layout`(modern/classic)、`cube_shading`(vanilla/reference)。
换 addon 只改这个文件，不用动代码。

---

## 特殊模型 / 草类 → wiki 图标（可关）

游戏里有些方块不是靠模型文件画的：箱子、潜影盒、床、旗帜、告示牌、钟、头颅、饰纹陶罐、末地传送门这些走**专用渲染器**；
草、蕨、海带、藤蔓这类贴图是**灰度图**，靠游戏按生物群系上色 —— 这两种 addon 里都读不到完整信息，所以：

- 这些方块会去 **minecraft.wiki / zh.minecraft.wiki** 取官方库存图标（300px 高清渲染），首次下载后存进
  `wiki_cache/<id>.png`，之后**离线也能用**；取不到才回退到内置几何体/自身渲染。
- 其它所有方块、物品仍然用**你自己的纹理 + 像素渲染**，风格统一。
- `config.json` 的 `wiki_icons`：`auto`（默认，只给上面两类用）/ `always`（所有方块都先试 wiki）/
  `never`（完全不用 wiki，全部本地画）。
- 草/树叶的颜色：优先用 `terrain_texture.json` 里的 `tint_color`（如睡莲 `#208030`），
  否则从原版 `textures/colormap/grass.png`、`foliage.png` 取**平原色**（草地 ≈ #8ABE56、树叶 ≈ #6DAD2C），
  已经是彩色的贴图不会被动。

## 输入：json / 文件夹 / 压缩包

| 拖进来的东西 | 行为 |
| --- | --- |
| `recipe.json` | 用 config.json 里的 BP/RP 出这一条 |
| 含 recipes 的文件夹 | 递归找 json，按 `recipes/` 相对路径输出 |
| 行为包文件夹（有 manifest.json，type=data） | 用它的 recipes + 里面/配置里的 RP |
| 资源包文件夹（type=resources） | 只当纹理来源（单独拖它不会出图） |
| `.mcaddon` / `.mcpack` / `.mcworld` / `.zip` | 解包 → 按 manifest.json 自动分出 BP 和 RP → 出图（世界包里的内嵌包也能认出来） |

## 界面

窗口里可以选 **行为包 BP / 资源包 RP / 输出目录 / 原版包**，以及版式、缩放、方块阴影、wiki 图标开关；
所有选择都会写回 `config.json`，**下次打开还是上次的设置**。拖放区域支持文件、文件夹和上面的压缩包，
拖到 `合成表生成器.bat` 图标上也一样。

---

## 原版资源：本地包 → 缓存 → 官方示例（三合一）

工具需要的原版资源（贴图、`item_texture.json`/`terrain_texture.json`/`blocks.json`、GUI 箭头火焰、colormap、字体）按顺序找：

1. **本地包**：`config.json` 的 `vanilla`（你给的 `Minecraft_samples\resource_pack`），以及任何拖进来的资源包；
2. **本地缓存**：`<工具目录>\vanilla_cache\`（下载过的文件都在这里，之后**离线可用**）；
3. **官方示例包**：`https://github.com/Mojang/bedrock-samples` 的 `resource_pack/`，**缺少什么就下什么**，
   下完进缓存。所以哪怕不填本地包、甚至没有 samples 目录，只要联过一次网就能用。

- GUI 里有「**补全原版资源（联网）**」按钮；命令行 `python mcrender_gui.py --sync-vanilla <行为包目录>`，
  会先把核心文件下好，再**预取该行为包所有配方用到的物品贴图**（省得首次出图一张一张下）。
- `config.json` 的 `vanilla_download`：`true`（默认，允许联网补全）/ `false`（只用本地包 + 缓存）。
- 网络不稳或断网时自动**熔断转离线**（连续失败就本次运行不再联网），不会卡住；
  下不到就回退到内置形状或平面图标，并在日志里报告。
- 只对 `minecraft:` 命名空间的 id 联网：你自己 addon 的物品不会去做无意义的下载。
- 两处缓存的体积都不大（核心文件 0.4 MB，wiki 图标每张几十 KB），随工具目录一起拷走即可。

### 字体说明

官方示例包和你的 samples 都**不含**字形位图（`texts/<语言>/font/glyph_XX.png` 两边都没有），
所以中文标题用的是 `mcrender/glyphs.json`（从你的截图里提取的 16×16 字模）+ 手写 ASCII 点阵，
不需要联网也不需要原版包。

## 压缩包：支持“双重压缩”

有些 `.mcaddon` / `.mcpack` 是**压缩套压缩**（外层 zip 里再放 `.mcpack`/`.zip`，或包在子文件夹里）。
现在解包会**递归**进行（最多 4 层），每一层都按 `manifest.json` 里的 `modules[].type`
（`data` → BP，`resources` → RP）识别，然后把找到的所有 BP 的配方一起出图。实测：外层 `double.mcaddon`
里放 `packs/inner.mcpack` 也能正常出图。

## 导出位置：自动多一层「合成表」文件夹

图片不再直接倒进你选的那个目录，而是放进它下面的 `合成表` 子文件夹：

```
你选的输出目录\
└── 合成表\            ← 所有图片都在这里（含配方子目录结构）
    ├── sacrifice\blood_offering_vial.png
    ├── armors\boots\lv1stone_boots.png
    └── ...
```

- `config.json` 的 `out_subdir` 控制这层文件夹名，**默认 `合成表`**；
- 如果输出目录本身就叫「合成表」（比如 `...\备份\合成表`），**不会再套一层**；
- 想让图片直接放输出目录，把 `out_subdir` 设成 `""` 即可；
- 界面上「输出目录」下面会显示一行「图片会放在：…」，确认最终落点；
- 「打开输出文件夹」按钮直接打开这个 `合成表` 文件夹。

---

## 打包成 exe

工具根目录的 `build_exe.bat`（已装 PyInstaller）生成两个独立 exe，**就放在工具根目录里**
（和 `build_exe.bat` 同一个文件夹，不再有 `dist\` 子目录）：

| 文件 | 说明 |
| --- | --- |
| `bedrock-recipe-renderer.exe` | 图形界面版，双击即用，**不需要装 Python** |
| `bedrock-recipe-renderer-cli.exe` | 命令行版（`--headless` / `--sync-vanilla`），带控制台输出 |

- 输出位置靠 `--distpath "."`；PyInstaller 的中间产物（`.spec`、临时构建）都塞在
  `build\pyinstaller\`，脚本跑完会把它整个删掉，根目录只留下两个 exe。
- 版本资源：打包前 `tools/make_version_file.py` 按 `mcrender/__init__.py::__version__` 生成
  `build\version_*.txt`，交给 `--version-file`，所以 exe「属性 → 详细信息」里的版本号跟
  窗口标题、标题旁的色块是同一串。
- 打包内容：`mcrender` 整个包 + `glyphs.json` / `sprites.json` + **预下好的 `vanilla_cache` / `wiki_cache`**
  （首次运行自动释放到 exe 旁边），所以拿到 exe 就能**离线**出图。
- exe 旁边的 `config.json` 记住设置；缓存也在旁边，整个文件夹拷走即可移植。
- 注意 exe 旁边要有写权限（配置/缓存写在那里），别放 `C:\Program Files`。

## 结构不同的包：项目式仓库 / 命名空间贴图键

HiddenYears2 这类"**开发仓库**"压缩包（BP + RP + `src/` + `assets/`）暴露了两个问题：

1. **贴图不在 RP 里**：这个 RP 根本没有 `textures/` 目录，贴图和两个索引都放在项目根目录的
   `assets\textures\` 下（构建时才合并进 RP）。现在会自动识别这种情况：
   如果某个包所在目录里**同时存在行为包和资源包**（项目结构），就把旁边的 `assets\`
   这类含 `textures\item_texture.json` 的目录也当作纹理根目录（优先级最低，只补空缺）。
   纯粹放在 `development_resource_packs\` 里的一堆不相干资源包**不会**被吸进来。
2. **贴图键带命名空间**：`"texture": "hiddenyears:crusher_top"`，而 RP/项目的
   `terrain_texture.json` 里正是以 `hiddenyears:crusher_top` 为键 —— 现在能正确命中。
3. **行为包自带 `material_instances` 的整方块**：以前只用 RP 的 `blocks.json`/`terrain_texture`
   猜面贴图，现在**优先用 BP 里写死的面→贴图映射**（`geometry.full_block` 或没写 geometry 时），
   这也是游戏本身的做法。

顺带修了 `totem_of_undying`（贴图文件叫 `totem`）以及全套唱片 `music_disc_* → record_*`。

## 物品 id 与贴图文件名不对应（紫黑方块的根因）

Bedrock 里不少物品的 **id 和贴图文件名不是一回事**，取不到就画成紫黑格。现在按规律补齐了：

| id | 贴图文件 | 规则 |
| --- | --- | --- |
| `bow` / `crossbow` | `bow_standby` / `crossbow_standby` | 显式别名 + 后缀兜底（`_item` `_standby` `_uncast` `_normal` `_empty` `_filled`） |
| `clock` / `compass` | `clock_item` / `compass_item` | 同上 |
| `wooden_sword` … | `wood_sword` … | `wooden_` → `wood_` |
| `golden_helmet` … | `gold_helmet` … | `golden_` → `gold_` |
| `white_dye` … | `dye_powder_white` … | `<颜色>_dye` → `dye_powder_<颜色>` |
| `beef` / `chicken` / `porkchop` / `mutton` | `beef_raw` … | 生肉自动加 `_raw` |
| `powder_snow_bucket` / `tropical_fish_bucket` | `bucket_powder_snow` / `bucket_tropical` | `<x>_bucket` → `bucket_<x>` |
| `wheat_seeds` | `seeds_wheat` | `<x>_seeds` → `seeds_<x>` |
| `oak_sign` / `spruce_sign` / `dark_oak_sign` | `sign` / `sign_spruce` / `sign_darkoak` | 告示牌专用规则 |
| `lapis_lazuli` / `glass_bottle` / `enchanted_golden_apple` / `fermented_spider_eye` / `turtle_scute` … | `dye_powder_blue` / `potion_bottle_empty` / `apple_golden` / `spider_eye_fermented` / `turtle_shell_piece` … | `EXTRA_ALIASES` 表 |

另外：**海带（kelp）、小麦、地狱疣、甜菜根这类"既是植物又是物品"的，用物品贴图**，不再用方块/十字模型
（`wheat`/`beetroot`/`nether_wart`/`kelp` 等 7 个），方块类（火把、蒲公英…）仍然用模型。

离线全量自检：**265 条配方里唯一"取不到贴图"的是 `tag:minecraft:stone_crafting_materials`**
（物品标签，本来就有代表物品映射）；常见原版物品 150 项里只剩 4 项确实没有对应贴图
（`dust`、`firework_star`、`knowledge_book`、`spectral_arrow`，原版样例包就没有这些文件）。

## 旧 data 药水：直接用 wiki 的药水渲染图

原版药水图标是「瓶子贴图 + 灰度 overlay 按药水颜色染色」，我自己配色那版颜色和游戏对不上
（比如夜视药水，游戏/wiki 是绿色，我按 Java 老配色染成了深蓝 ✗）。现在改成：

1. **优先取 wiki 的官方渲染图**：按 `Invicon [Splash|Lingering] Potion of …` 从
   `minecraft.wiki`（16×16，像素风）取图，常见/喷溅/滞留三种瓶子各有对应文件，
   下载后缓存在 `wiki_cache\potion_*.png`，之后离线可用；
2. 英文 wiki 没有的条目再试中文 wiki（32×32）；
3. **完全离线时**才回退到"瓶子 + 染色"的本地合成（颜色表仍是 Java 经典配色）。

`potion_type:regeneration` 的写法同样支持，`long_/strong_` 之类会归到对应基础药水的渲染图。

## 变体（data）与特殊模型的两个修正

- **`data` / `variation` 现在会被解析**：配方里 `{"item": "minecraft:skull", "data": 1}` 这类写法，
  内部记成 `minecraft:skull#1`，再按变体表换成具体物品（`skull` 0~6 →
  骷髅头/凋灵骷髅头/僵尸头/玩家头/苦力怕头/末影龙头/猪灵头），顺带支持羊毛、地毯、染料、混凝土、
  玻璃、木板、原木、树苗、树叶、石头、泥土、砂岩、石砖等常见变体的旧 data 值。
  之前凋灵骷髅配方的输入输出都画成同一个通用"头"，现在是正确的两种头。
- **wiki 图标加了尺寸检查**：wiki 有些页面（比如 `Skull` 这种归类页）的配图是 **2560×1459 的拼图**，
  缩到 16 单位就是一个糊球 —— 现在超过 1000px 的图直接判定不可用并删掉，改用具体变体页面
  （`Skeleton Skull`、`Wither Skeleton Skull` 等，都是干净的 300×300 渲染图）。
- **失水恶魂（`dried_ghast`）**：它和箱子、床一样是游戏里写死的模型，之前被当整方块画成一个怪方块。
  现在优先用 wiki 官方图标；**断网时回退到内置模型** —— 一个 10×10×10 的身体 + 四条触须，
  身体六个面各自用自己的贴图（`dried_ghast_front/back/left/right/top/bottom`），触须用
  `dried_ghast_tentacles`，为此给几何体加了「逐立方体贴图」支持。

## 选择文件夹：BP 与 RP 分开，两个都选好才出图

路径行各自的「选择…」按钮就是文件夹选择器，界面上不再另设重复按钮：

- **行为包 BP 行的「选择…」**：选行为包 → 程序**自动找配套 RP**（先按 `com.mojang` 目录约定
  `development_behavior_packs\X(BP)` → `development_resource_packs\X(RP)`，再按 manifest 里
  `dependencies` 的 uuid 找），找到就自动填进 RP 行；
- **资源包 RP 行的「选择…」**：只切换纹理来源；
- **选完不再自动出图**：BP、RP 都就绪后，点 **「▶ 开始出图（当前 BP + RP 的全部配方）」** 才生成
  —— 只选一边时按钮会提示先补齐另一边，避免自定义贴图查不到而出紫黑方块；
- 拖压缩包（`.mcaddon/.mcpack/.zip`）或拖文件夹仍然**立即出图**（解包出来的 BP/RP 自动配对），行为不变。

## 自定义物品标签

无序配方常写成 `{"tag": "hiddenyears:is_artifact"}` —— 标签本身没有贴图。现在扫描行为包 `items/**/*.json`
时会读 `minecraft:tags` 组件，建立「标签 → 一个成员物品」索引，画图时用那个成员代替，
并在日志里写明「item tag X drawn as Y」。原版的常见标签（木板/原木/石制材料…）仍走内置表。

## 锻造台 UI（对齐游戏）

按游戏界面改：标题 **「升级装备」**（原版 `smithing_table` 标签），左上角画 `textures/ui/smithing_icon.png`
（那把锤子图标），三个槽排成一行并给第一/第三个槽叠上游戏自带的
`templates_slot_overlay.png` / `smithing_material_slot_overlay.png`（虚线框），后面接箭头和产物槽。
自定义工作台（`stone_smithing_table` 等）仍用它们自己的标题。

## 酿造台燃料条

燃料条从"空条"改成 **`brewing_fuel_bar_full.png`**（游戏里那根黄色条），位置大小不变。

## 命名空间写法

`item_texture` 的键有 `<name>`、`<ns>.<name>`（点号）和 `<ns>:<name>`（冒号）三种写法，
查表时三种都会试。**不再为作者的 id 笔误做迁就**：如果配方里写的 id 在包和原版里都不存在
（例如物品声明成 `nj:unstable_golden_spear`、配方却写 `nj:unstable_gold_spear`），
就按缺失处理（画紫黑格），不去猜近似名字。

## 一键补全原版纹理（整包下载，只增不删）

「**补全原版资源（下载全部纹理）**」按钮会把 `item_texture.json` + `terrain_texture.json` 里
引用到的**全部**原版物品与方块纹理（约 **2100+** 个路径）下载到 `vanilla_cache\`，8 线程并发、
带进度日志，**下载后不会被自动删除**。之后出图只读本地，不再联网（速度快很多）。

旁边的「**删除本地纹理缓存**」按钮会弹确认框并显示当前占用体积，确认后清空 `vanilla_cache\`；
删除后回到"缺什么补什么"的按需模式（或直接从你配置的本地原版包读取）。

不点这个按钮时，行为与以前完全一样：**缺什么补什么**。

## 手动选 BP / RP 不会自动出图

路径行的「选择…」只负责设值（BP 会自动找配套 RP 并写进配置、日志给出提示），
必须点 **「▶ 开始出图（当前 BP + RP 的全部配方）」** 才会生成。
拖 json / 文件夹 / `.mcaddon` 进来仍然是立即出图。

## 锻造台 / 酿造台界面按游戏 UI 定义还原

数值取自游戏自带的 `ui/smithing_table_2_screen.json` 与 `ui/brewing_stand_screen.json`：

- **锻造台**：面板加高到 176x104；30x30 的 `smithing_icon.png` 在左上角，标题「升级装备」居中且与图标同高；
  三个 18x18 槽排一行（x=10/28/46, y=56），第一/第三个槽叠 16x16 的模板/材料 overlay；箭头在 x=74，产物槽 26x26 在 (97,52)。
- **酿造台**：弹簧（`brewing_fuel_pipes`）按**原始 30x22** 画不再拉伸；燃料条外框 24x6 在 (60,46)、
  黄色填充 22x4 在 (61,47)，左端正好接上弹簧的管子；**燃料条上方补上 `bubbles_empty/full` 12x30 的气泡**。

## UI 贴图改为内置（不再依赖下载）

`assets\ui\textures\ui\` 里内置了 15 张界面贴图（箭头、火焰、酿造台弹簧/燃料条/管道/气泡、瓶子、
锻造台图标与槽 overlay 等），在查找原版资源时**优先级最高**。所以即使联网下载失败
或被"删除本地纹理缓存"清掉，界面也不会缺件（日志里那两条 `bubbles_*.png missing` 不会再出现）。

## 气泡等元素按游戏 UI 定义摆放

`ui/brewing_stand_screen.json` 里每个元素都是相对屏幕中心的偏移，直接照搬：
弹簧 (-47,-5) 30x22、燃料条 (-23,3) 24x6 + 黄条 22x4、气泡 (-23,-14) 12x30、
**管道用游戏原版 `brewing_pipes`（30x26）**（之前是手画的竖管，会压到燃料条 → 现在不会了）、
箭头 (16,-14) 9x28。

## 切石机界面

单输入槽 (41,30)、大箭头 (72,~32)、26x26 产物槽 (103,26)，行整体在 176 宽面板里居中，
标题「切石机」居中 —— 与游戏截图一致。

## 锻造台标题

标题紧挨左上角的 30x30 工作台图标（x=38），不再拉到面板中间；箭头左移到 x=71，与产物槽留出间距。
**中文标题缺字时**（比如「升级装备」「切石机」）改用系统字体（微软雅黑）在 16x16 单元内渲染后
**二值化**，输出清晰的像素字形，不再是模糊的抗锯齿缩放。

## 机器配方用 tags 决定版式

Bedrock 允许把机器配方写成普通的 `recipe_shapeless` / `recipe_shaped`，只要 `tags` 里写清工作台即可。
你包里的 11 条切石机配方就是这样（`recipe_shapeless` + `tags:["stonecutter"]`），以前会被当成无序合成画成 3×3 ✗。
现在 `tags` 优先决定面板：

| tag | 版式 |
| --- | --- |
| `stonecutter` | 切石机（单输入槽 + 大箭头 + 26×26 产物） |
| `furnace` / `blast_furnace` / `smoker` | 熔炉（输入 + 燃料空槽 + 火焰 + 箭头 + 产物） |
| `smithing_table` | 锻造台（若配方里真给了 template/base/addition） |
| 其它 | 合成台 3×3 |

## 下过一次之后就不再联网（local_only）

「**补全原版资源（下载全部纹理）**」只要**没有失败条目**，就会自动把 `config.json` 的
`local_only` 设为 `true`，之后：

- 原版纹理只从**本地原版包 + `vanilla_cache`** 里找，**完全不再发请求**；
- **wiki 图标**也只读 `wiki_cache`，缓存里没有就直接跳过（不再联网查）；
- 设置行里多了一个勾选框 **「只用本地（不联网）」**，可以手动开关；
- 点了「**删除本地纹理缓存**」会自动把 `local_only` 关掉，回到"缺什么补什么"的模式。

实测（磐石之旅 249 个物品）：

| 模式 | 耗时 | 联网请求 |
| --- | --- | --- |
| `local_only = true` | **0.31 秒** | 0 |
| `local_only = false`（按需补全） | 273 秒 | 有（未命中项要等超时） |

不点"补全原版资源"时 `local_only` 保持 `false`，行为与以前完全一致。

## 「补全原版资源」永远能完成的三个白名单

官方样例包里根本没有这 3 个条目（每次补全都会红着失败 4 次，导致无法进入纯本地模式）：

| 条目 | 处理 |
| --- | --- |
| `textures/items/camera` | 教育版物品，样例包没有 → 列入 `KNOWN_MISSING` |
| `textures/blocks/hanging_pale_moss_middle` | 同上 |
| `textures/blocks/hanging_pale_moss_tip` | 同上 |
| `textures/blocks/cake` | 方块形式的蛋糕贴图，样例包没有；**只认物品形式** `textures/items/cake`（配方卡用不到方块形式）→ 列入 `KNOWN_MISSING` |

`KNOWN_MISSING` 里的条目不算失败（日志只在统计里显示「跳过 4」，不会单独刷一段话）。
所以补全后 **失败数 = 0**，会自动切到 `local_only`；`assets\ui`（内置界面贴图）作为最高优先级
本地目录，而且**打包进 exe 的那一份也会被搜索**（`bundle_root`），所以 exe 旁边缺这个文件夹也不影响出图。

## 原版包支持压缩文件（.zip / .mcpack / .mcaddon）

「原版包」那一行现在可以选**文件夹**，也可以选**压缩文件**。压缩文件不需要解压整包：
用到哪张纹理就当场把那一个成员读出来放进 `vanilla_cache`，所以放一个"只含合成所需纹理的精简包"
也完全够用。

判定有效：压缩文件/文件夹里能找到 `textures/item_texture.json`。
一旦有效，**立刻切到本地模式**（`local_only = true`），不发起任何联网下载。
日志只写两行：

```
原版包（压缩文件）可用，已切到本地模式，不再联网。
原版包无效（找不到 textures/item_texture.json）：<路径>
```

## 下载失败自动重试一次

并发下载结束后，对失败项再**顺序重试一遍**（并发时容易出现成批失败）。
重试日志：`重试 16 个失败项`。重试仍失败的才会留在失败清单里。

## 五处贴图 / 文字问题（本轮修复）

1. **石头工具、骨粉抓到了 wiki 文章插图**（多把锄头/多把剑/骨头）。
   原因：`minecraft:stone_hoe` 之类的条目**不在**样例包那份精简 `item_texture.json` 里，
   本地找不到时回退到 wiki，而 wiki 取的是**条目配图**而不是物品图标。
   现在 ①wiki 只接受"小且接近正方"的图标（≤96px、长宽比 0.55–1.8），
   ②优先按 `Invicon <名称>.png` → `<名称> JE1 BE1.png` → `<名称>.png` 顺序取文件，条目配图放最后。
2. **`ruby_griffin_egg`（HiddenYears）**：配方产物 id 是 `hiddenyears:ruby_griffin_egg`，
   但 RP 的物品贴图键写成 `hiddenyears:egg_ruby_griffin`（词序相反），
   方块贴图又是 `ruby_griffin_egg_stage_0..3` —— **作者命名不一致**，按你的要求不处理。
3. **两种高度的花**（`sunflower` / `lilac` / `rose_bush` / `peony` / `pitcher_plant`）：
   以前走方块模型（灰色十字），现在与 `WIKI_SPECIAL` 一致，取**物品图**。
4. **补全原版资源时按配方再预取一遍**：`sync_all` 只下载索引里列出的贴图，
   石头工具、骨粉这类"直接用文件名引用"的贴图不在索引里。现在补全后会遍历当前 BP 的所有配方，
   把缺的贴图也下下来（日志：`按配方预取贴图…`），这样切到本地模式后才真的不缺件。
5. **中文标题**：缺字时用系统字体渲染，现在把字形**居中并限制在 16×16 单元内**，
   不再出现最后一个字被切掉的情况（`切石机` / `升级装备` / `石头工作台` 都完整）。

## 输出目录与本地模式

`config.json` 的 `local_only` 现在同时受"补全原版资源"和"原版包是否可用"控制；
放了有效的原版包（文件夹或 zip）会立刻切到本地模式。

## 全量审计（磐石之旅 249 个配方物品）

| 场景 | item | block | model | **missing** |
| --- | --- | --- | --- | --- |
| 配置了原版包 | 179 | 46 | 22 | **2**（`dried_ghast`、一个 tag） |
| **没配原版包** | 138 | 20 | 2 | **89** |

"兼容性差"的根源就是第二种：`vanilla` 为空时 89 个物品在本地找不到，
只能靠 wiki 兜底，而 wiki 有时给的是条目配图。
**配置好原版包（文件夹或 zip），或再点一次「补全原版资源」**即可（新版补全会按配方把非索引贴图也下下来）。

## 头/骷髅一族

`minecraft:skull`（无 data）、`minecraft:wither_skull`、`*_head` 以前会退化成"平铺方块贴图"（棕色噪点）。
现在统一归一到骷髅变体 0–6，取 wiki 的物品图标（官方样例包不含任何头部贴图），
内置骷髅模型作为离线兜底。实测 `skull` → 骷髅头、`wither_skull` → 凋灵骷髅头。

## 界面重做：Tkinter → CustomTkinter（Fluent / WinUI 3 风格）

原来的界面是手写 Tkinter（`tk.Label` + `ttk.Combobox` + `tk.Button`），外观是系统默认灰控件。
本轮换成 Windows 11 的 Fluent 观感，同时按「界面与业务分离」重排了代码结构。

**新增依赖**：`customtkinter`（Fluent 控件 + 深/浅色自动切换）、`pywinstyles`（Win11 Mica 背景与标题栏着色）。
两者只在**开窗口**时导入 —— `--headless` / `--sync-vanilla` 走 `mcrender.app`，完全不碰它们，
命令行批处理逻辑不受影响（渲染核心仍然只依赖 Pillow）。

**结构变化**：

| 之前 | 现在 |
| --- | --- |
| `mcrender_gui.py`（888 行）：GUI + 解包 + BP/RP 配对 + 批量调度 + 命令行 全混在一起 | `mcrender_gui.py` 只做入口分派 |
| — | `mcrender/app.py`：路径 / 配置 / 解包 / 配对 / `Generator` 抽出来，**不 import 任何 GUI 库** |
| — | `mcrender/ui/`：`theme.py`（设计令牌 + Mica）、`window.py`（主窗口）、`dnd.py`（拖放） |

`mcrender_gui.py` 仍然把 `ARCHIVE_EXTS` / `load_config` / `Generator` 等名字转发出来，旧的外部脚本不受影响。

**外观细节**：
- 深 / 浅色两套色板取自 WinUI 3 Fluent 资源，默认**跟随系统**；标题栏右侧按钮循环切换，写入 `config.json` 的 `appearance`。
- 卡片式布局：圆角、细描边、Win11 层级配色；输入框 / 下拉 / 开关 / 按钮都按 Fluent 度量重做。
- 日志区按语义着色（成功绿 / 失败红 / 警告橙 / 说明灰），跟随外观一并切换。
- 拖放改为封装 `WM_DROPFILES` 的 `dnd.py`（窗口过程挂到 CTk 主窗口，旧过程引用挂在窗口上防止被 GC）。
- Mica / 亚克力 / 标题栏着色都是**尽力而为**：拿不到（非 Windows、系统太旧、pywinstyles 缺失）就静默降级，界面照常可用。

**两个控件是自绘的**。CustomTkinter 的这两个控件形状写死在库里，改不成 Fluent，
所以在 `mcrender/ui/widgets.py` 里重做：

| 控件 | 原实现为什么不行 | 现在怎么做 |
| --- | --- | --- |
| 下拉 | `CTkOptionMenu` 的下拉其实是原生 `tkinter.Menu`（Tk 自己绘制，方角边框 + 原生高亮），颜色能改、形状改不了 | `FluentComboBox`：字段底板用 **Pillow 画一张带描边的圆角图**贴上去（不是 `CTkFrame.border_width`，原因见下）+ 自绘箭头；点开是**主窗口里的一块子 `tk.Canvas`**（`place` 到盖住字段的位置 + `tkraise`），面板底、选项块、选中竖条、字段底板都是 Pillow 图（`canvas.create_image`）。面板**不是独立窗口**，见「下拉面板搬进主窗口」 |
| 开关 | `CTkSwitch` 是在 Canvas 上手画的大药丸 + 大圆点，比例和 WinUI 3 差得远，而且文字只能放滑块右边 | `FluentSwitch`：文字在左，右侧 36×20 胶囊轨道 + 11px 滑块，用 Pillow 画好交给 `CTkImage` |

### 按钮 / 输入框：行为沿用 CTk，只把「圆角描边」换成 Pillow 画的

`FluentButton` / `FluentEntry` 是 `CTkButton` / `CTkEntry` 的子类，**尺寸、悬停、
文字、图标、`command`、输入行为全部沿用 CTk**，唯一换掉的是 Canvas 上那层圆角矩形：

- CTk 的圆角是用**字形**拼的（`font_shapes`，Windows 上首选）。半径 4~5 逻辑像素时，
  角上一圈只有 **5~8 级**过渡色 —— 放大看是硬阶梯。`polygon_shapes` / `circle_shapes`
  更差（只有 2~3 级），`set_round_to_even_numbers(False)` 对圆角没有任何改善，
  所以**没有现成的开关可调**，只能自己画。
- 做法：`_draw()` 里先 `super()._draw()`，再往 `self._canvas` 上贴一张
  `icons.rounded_rect` 画的图并 `tag_raise`。CTk 的文字 / 输入框是**真正的子控件**，
  永远压在 Canvas 之上，所以`behavior` 一点不用动；圆角外用父容器底色填满，
  不依赖 Tk 的 alpha 合成。
- CTk 自己那圈描边要关掉（`_border_width = 0`），但**先按原值建完再关** —— 它的
  `_create_grid` 会用 `border_width` 算格子的 minsize，先关会让布局差一点。
- 悬停色要自己跟：CTk 的 `_on_enter` / `_on_leave` 只 `itemconfig` 那层被我们盖住的
  `inner_parts`，所以覆盖这两个方法（**都先 `super()`**）再重画一次底板。
- 效果：角上过渡色 6~8 级 → **13~28 级**，和下拉字段底板是同一套观感。
- `cget("border_width")` 被重写成返回「被关掉之前」的真实宽度，免得自检脚本以为按钮没描边。

**选项块的几何**（`FluentComboBox` 的类常量，逻辑像素）：

```
ROW_H 32   单个选项块高
ITEM_GAP 6 相邻两块之间的缝     ← 行距 = 32 + 6 = 38
PAD_V 6    面板上下的内边距 / PAD_H 5  选项块左右内缩
PILL_W 3 / PILL_H 16 / PILL_INSET 2     选中竖条宽 / 高 / 相对选项块左沿的内缩
SHADOW_SPREAD 20 / SHADOW_OPACITY 0.22   面板四周那圈投影的留白 / 铺开距离、中心不透明度
```

⚠️ **画布 ≠ 面板**：面板四周还有一圈投影留白（`_shadow_pad`），画布按「面板 + 左/右/下各一份、
上边只有 `_shadow_top`」铺（见上面的动画一节）。所以图元坐标 = **面板坐标 + `(pad, top)`**，
画布的 `place` 也按画布算；而 `geom` / `_bounds` / 命中判定一律仍是**面板坐标** ——
`_index_at()` 里那句 `y - self._shadow_top` 就是把指针的画布坐标换算回面板坐标。
工具脚本量屏幕坐标时别忘了这个偏移（`tools/check_option_click.py` 踩过）。

行位置和面板总高不由 `_open()` 自己算，而是交给 `shape.row_bounds()` /
`shape.list_height()` —— 和命中判定（`shape.row_index`）共用同一套数字，
两边各算一遍迟早会错位。指针落在两块之间的缝里会归到**上面那一行**，
判成 -1 反而会让点击落空、把面板关掉。

下拉列表坚持**单张 Canvas、不放任何子控件**，是因为踩过坑：早先用
`Frame + Label + 竖条` 三层拼，指针在父子控件之间移动会成对触发 `Enter`/`Leave`，
悬停高亮时不时不生效；而且悬停分支曾复用选中底色，划过当前值那一行等于没反应。
现在改成：
- 指针位置用整块 Canvas 的一个 `<Motion>`，`y` 反推成行号（`shape.row_index`）——
  没有嵌套控件就没有抖动；
- 普通项**不铺底色**（透明，直接露出面板底，整列读起来是一片干净的列表而不是一格一格
  的方块）；只有悬停和当前值才出现色块，两档**取同一个色值**（`menu_hover` 与
  `menu_selected` 相等），也就是「鼠标触碰的加深程度 = 选中的加深程度」，
  底色本身很浅（浅色模式下只比白底深一点），**谁被选中完全由左侧 `3×16` 的强调色
  竖条说明**；两档底色相对面板底 (layer) 的色差由预览脚本断言（≥8 阶）；
  选项文字的左沿**不能**和字段文字共用 `TEXT_X`：竖条占的是
  `PAD_H + PILL_INSET .. + PILL_W` = 7..10 逻辑像素，而 `TEXT_X = 9` —— 文字正好从
  竖条**里面**起步（用户报的「竖线与文字过近，没有留出一定距离」；量真机图：竖条
  x=37..41、文字第 40 列就开始了）。现在文字左沿 = `PAD_H + PILL_INSET + PILL_W +
  MENU_TEXT_GAP`（`geom["menu_text_x"]`），从竖条右沿再让出 `MENU_TEXT_GAP`；
- 面板圆角：面板是**主窗口里的子控件**，子控件没有 alpha，所以四角那四小块用「抓一帧
  主窗口内容当底图、把 Pillow 画的圆角面板叠上去」来填 —— 见下面「下拉面板搬进主窗口」。
  **四周那圈投影走的是同一条路**（半透明的黑色也是 `alpha_composite` 叠进底图的），
  所以画布得比面板大一圈、抓背景快照也要按画布那一圈来抓。

**收起时机**：Esc、**松手（点选项 / 点面板留白 —— 见下面的警告）**、点字段、点主窗口别处、
主窗口失焦、主窗口最小化，以及**主窗口一被缩放 / 挪动就收起**（`_on_host_configure`）——
面板是子控件，位置是「按字段算出来再 `place` 的」，不会自己跟着字段走，留着就会浮在
错误的位置上（窗口变小时还会被裁掉一截）。失焦那条是「延迟 60ms 再用 `GetForegroundWindow`
确认」——立刻收会误伤，因为点列表项本身也会让焦点变动。

> **`_on_host_configure` / `_on_host_unmap` 必须判 `event.widget is host`。** Tk 的
> bindtag 里有 toplevel 那一档，**子控件**的 `<Configure>` / `<Unmap>` 也会调到绑在
> 顶层窗口上的处理函数 —— 不判来源的话，面板自己 `place` 上去那一下 `Configure` 就会
> 把它立刻收掉（而且没有任何报错，现象是「下拉怎么都打不开」）。
> `tools/smoke_ui.py` 有一条断言专门盯它：用一个只带 `widget` 属性的假事件，分别以字段
> 和主窗口的身份各调一次，前者必须**不收**、后者必须收。

> **收起面板要挂在「松手」上，不能挂在「按下」上。** 一次点击是**两个独立事件**：
> `<Button-1>` →（用户还按着的那段时间）→ `<ButtonRelease-1>`。只要在按下时就把面板
> `destroy()`（排到 `after_idle` 也一样 —— 事件队列一空它就跑了，物理松手还没到），
> **松手的那次 `ButtonRelease` 就会落到面板底下的控件上**，而 `CTkButton` 的 command
> 恰恰是在 `_on_release` 里触发的（`if self._mouse_inside ... self._command()`）。
> 下拉展开后正好压着按钮行，于是「选一个选项」会顺带把下面的按钮也点一下。
>
> 现在的做法：**`_on_press` 只负责改值（点下去立刻看到效果），面板不收；
> `_on_release` 才 `_close_soon()`**。这样松手那一下由面板自己接住（Tk 在按下时会
> **隐式抓取指针**，所以即使按住一项拖到别处再松手，release 也回到面板），
> 等事件派发完再销毁。`_close_soon()` 排到 idle 只是为了别"在自己的绑定里销毁自己"
> （Tk 里那容易引出 `invalid command name` 那类 bgerror），已经跟防漏点击无关了。
>
> **面板搬进主窗口之后这条更要紧**：以前弹窗是独立 `Toplevel`，至少还有「另一个窗口」
> 这层天然隔离；现在是同一个窗口里的兄弟控件，一层隔离都没有。
>
> ⚠️ **这个 bug 踩过两次、而且第一次的回归断言是假通过的**：旧断言在 `event_generate`
> 之后**立刻**看面板还在不在 —— 那一刻确实还在（`after_idle` 还没跑），所以一路绿灯。
> 要抓住它必须满足两点：**跑够真实时间把 idle 队列清空**，以及**用真鼠标事件**
> （release 投给谁由 OS 按指针位置决定，`event_generate` 是直接塞给某个控件）。
> 现在两边都有：`tools/smoke_ui.py` 里是「按住期间面板必须一直活着」（确定性、泵 250ms），
> `tools/check_option_click.py` 是合成真鼠标的完整回归（点选项 / 点留白 / 拖到按钮上松手 /
> 连点字段 / 拖很远再松手，5 个场景）。

`mcrender/ui/icons.py` 里的图形分两类：

- **外观三图标（跟随系统 / 浅色 / 深色）是外部位图** —— `assets/icons/appearance_*.png`，
  由用户给的 SVG 光栅化而来（源 SVG 存在 `assets/icons/source/`）。它们是单色形状，
  运行时只取 alpha 通道当遮罩、再按当前主题文字色染色，所以浅 / 深两版各染一次。
  文件名按「该模式」而非图形取：`light` 配太阳、`dark` 配月亮、`system` 配显示器。
  位图缺失时才退回模块里手绘的 `_sun` / `_moon` / `_auto` 兜底。
- **下拉箭头 / 开关 / 圆角底板仍是现画** —— 按 4 倍尺寸绘制再 `LANCZOS` 缩回来拿到抗锯齿边缘。

两种都出「浅色版 + 深色版」各一张、包成 `CTkImage`，由 CustomTkinter 按当前外观自动挑选。

纯几何（圆角矩形顶点、列表的行位置 / 总高、行号反推）单独放在 `mcrender/ui/shape.py`，
**不导入 tkinter**，这样它和依赖它的预览脚本在没有 tkinter 的机器上也能跑，
也让「画」和「判命中」能共用同一份算式。

**没有 tkinter 也能检查这些图形**：`python tools/preview_ui_icons.py` 会把它们按浅 / 深
两种主题渲染成一张 PNG（默认写系统临时目录并打印路径），顺带量出开关的实测直径与
四周留白是否对称（LANCZOS 边缘抗锯齿会让实测值比设计值小约 1px，看趋势即可）。

**下拉面板同理**：`python tools/preview_ui_combo.py [输出路径] [--scale 1.75]` 按
`FluentComboBox` 里**解析出来的真常量**（不复制数值）用 Pillow 重画面板，渲染
「无悬停 / 悬停普通项 / 悬停另一项」三种状态 × 深浅两色，并断言：相邻块的缝隙等于
`ITEM_GAP`、每行的中心 / 缝隙 / 上下边界都落在正确的行号上、两档底色相对面板底的色差够大、
以及**字段与面板的四条边（上/下/左/右）都有描边**（要求边像素**离描边色比离填充色更近**，
只判「在容差内」会把填充色误判成描边、造成假通过）。
`--scale` 传系统 DPI 缩放，出来的图就和屏幕上的**物理像素**一致。开发机上没有 tkinter，
这是唯一能肉眼确认圆角与悬停效果的办法。

### 动画：下拉「以当前值那一行为中心向两侧展开」、四周「亮起投影」、开关「滑过去」（2026-10-14，展开方向 2026-10-15 改）

三处都是 WinUI 3 的招牌动作，也都按 Fluent 的时间与曲线做：

| | 时长 | 帧间隔 | 曲线 | 做法 |
|---|---|---|---|---|
| 下拉展开 | **110ms** | 6ms | `soft_in_out` | 面板先挪到「选中行中线 == 字段中线」（盖住字段），**露出带**再从那条中线向两头扫开：画布一次摆满、全程不动，带外两段贴背景快照（盖住内容）、带内一段贴底图（**纯裁剪、不淡入**，见下） |
| 下拉收起 | **90ms** | 6ms | `soft_in_out` | 同一份露出带算式喂 `1 - soft_in_out(p)`（展开的镜像），三段拼贴反向合拢，底图同时往背景里混 |
| 四周投影淡入 | **150ms** | 6ms | `soft_in_out` | 展开**播完之后**的第二段：底图从 `plain`（只有面板）混到 `full`（面板 + 投影）—— **只让柔光长出来，面板本身不变** |
| 开关切换 | 180ms | 16ms | `fast_out_slow_in` | 逐帧重画滑块（`icons.switch_pair` 出中间帧） |

展开 / 收起最初是 80 / 60ms（Fluent 的 `Faster` 档），后来按用户要求「稍微加长」到
110 / 90ms —— 落在 `Faster`(100ms) 与 `Normal`(200ms) 之间，配合两端速度为 0 的
`soft_in_out` 观感是「长出来」而不是「弹开」。**时长的两次调整都没有动帧间隔和计时器
精度**，原因是下面这条：

**「更快 / 更慢」和「更顺」是两个旋钮，别只拧一个。** 时长只管快慢；帧数要靠**帧间隔**
和**计时器精度**去抢。把 150ms 直接改成 80ms 而什么都不做的话，帧数会从 9 帧掉到 5 帧、
一看就是「跳」过去的。这里同时拧了三处：

1. **帧间隔 6ms**（`widgets.FRAME_MS`）。真动画里每一帧自己还要花 2~3ms（混一条 +
   换三张图 + 重画），压在间隔之上 —— 请求 6ms 落 9~10ms。取 6ms：110ms 里出 12~13 帧、
   间隔全在 8~12ms。**别按「60fps = 16ms」拍。**
2. **动画期间提系统计时器精度**（`win32.timer_resolution`）。Windows 默认粒度 ~15.6ms，
   不提升的话上面那个 6ms 会被量化成 13ms 上下，帧数白砍一半、还忽快忽慢。
3. **曲线**：`soft_in_out` 两端速度为 0，时长短也不显仓促（见下）。

实测结果（`tools/smoke_ui.py` 的动画一节每次都重新量）：展开 **12~13 帧 / 112ms**、
收起 10~11 帧 / 90ms、投影淡入 16~17 帧 / 152ms，帧间隔中位数 **9.5~10.5ms**。
（换成三段拼贴之前是 15 帧 / 8.0ms；多出来的那一两毫秒是「带外两块也要各换一张图」，
换来的是画布窗口彻底不动 —— 撕裂就是这么消掉的。）

**提计时器精度必须「有借有还」**：`_Motion` 用 `_held` 记账，**自然跑完 / `cancel()` /
控件已销毁三条路都收敛到 `_release()`** —— 漏一条就一直提着不放，白耗电而且看不出来。
`_Motion.start` 里的 `interval` 只是「不早于」，真正兑现靠精度；所以 `_acquire` 与
`_release` 是配套的，改一个必须改另一个。⚠️ 展开与投影是**两段**动画，用的是同一个
`_Motion`：第一段跑完时 `tick` 先 `_release()` 再调 `on_done`，第二段在自己的 `start()`
里重新 `_acquire()` —— 所以「等两段都播完再断言精度已归还」是必须的，只等第一段会假失败
（`tools/smoke_ui.py` 里就是这么写的）。

**为什么投影要单独做一段、还排在展开之后**：用户的要求就是「在动画播放完毕后在下拉列表
四周淡入阴影」。技术上也确实只能这样 —— 投影是**整张**画在画布底图上的，露出带还没铺满时
那圈柔光会被上下裁掉两条、贴在带的边缘像一道切口。所以先把面板长出来（`_fade_shadow_in`
里把 `_reveal` 置空 = 整块画布都露出来，再跑第二段）。

⚠️ **投影这段必须混 `plain` → `full`，不能混背景快照 `empty`**（`_paint(..., base_key="plain")`）。

早先那版把底图从 `empty` 混到 `full`，`mix` 由 1 走到 0 —— 于是**第一帧 `mix=1`，整块面板
（连底带字的底子）瞬间变透明**，再花 150ms 淡回来。屏幕上就是「菜单刚长好，忽然整个闪没，
然后又浮出来」，用户报的「打开频闪」正是它。实测（面板正中压着的那个像素，客户区坐标）：

```
 105.1ms  (251,251,251)  mix=0.014   ← 展开落地，面板还在
 113.6ms  ( 26, 26, 26)  mix=1.0     ← 投影段第一帧：面板没了，露出底下的窗口文字
 121.0ms  ( 27, 27, 27)  mix=0.993   ← 之后 150ms 一路淡回来
 ...  266.4ms  (255,255,255)  mix=0.0
```

⚠️ **展开是纯裁剪，面板不淡入**（`_frame_expand` 里 `mix` 恒 0）。底图淡入、文字却是画布
图元（全不透明），于是展开前半程屏幕上就是「几个数字浮在窗口按钮上、面板只是一层薄雾」——
用户截图拍到的正是那一帧（40ms 时面板只有 16% 不透明，40~90ms 之间还能读出底下的按钮字）。
Win11 的浮出控件本身也是 clip reveal。`smoke_ui` 里那条断言随之反过来：**展开途中
`_fade_alpha` 必须一路是 1**（以前要求的恰好是「必须有半透明帧」）。

**投影怎么画的**（`icons.soft_shadow`）：实心圆角矩形遮罩 → 高斯模糊（半径 =
留白 / 2.0）→ 按 `opacity` 变黑。几个要点：

- 必须**实心**再去模糊。用 `outline` 画一圈再模糊的话，中间会空出一块比边缘浅的洞。
- 模糊半径取「留白 / 2.0」（σ = spread / SHADOW_BLUR）——但**留白不能等于 spread**：
  高斯的尾巴要到 `3σ` 之外才看不见，留白只给 σ 的两倍时，尾巴会在画布边沿被**硬切一刀**，
  面板外沿那圈柔光就成了一条直边（2026-10-18 用户报的「阴影边缘不自然」）。留白按
  `icons.shadow_pad(spread)` = `ceil(spread × 3 / 2) + |offset_y|` 单独算，`soft_shadow`
  多收一个 `pad` 参数：形状按 `pad` 内缩、模糊完尾巴正好落到 0。
  实测：`spread=20` → 留白 32、`spread=35` → 留白 55；画布边沿那一圈的黑度**恰好 0**。
- `opacity` 是**遮罩中心**的值，面板边缘那一圈只有它的**一半**（模糊一个阶跃边，边界处
  正好是 50%）。调浓淡按这个换算，别照着数字猜。
- 阴影整体**下移 2 逻辑像素**（光源在上方），补偿办法是遮罩里的形状先上移、模糊完再挪
  回来 —— 直接挪遮罩的话底部会在图像边沿被切掉。
- **「更柔和 + 更浅」怎么调**（2026-10-15，用户要求）：留白 `14 → 20` 逻辑像素、`opacity`
  `0.34 → 0.22`。关键在**留白与模糊半径是绑在一起的**（σ = 留白 / 2.0）：只把 σ 调大而
  留白不动，高斯尾巴就会撞到画布边沿被切出一条硬边；放大留白才是「同一形状铺得更开」。
  实测剖面（175% 缩放，面板左边往外的黑度 0~255）：贴边 **45 → 29**（约 17% → 11% 黑），
  12px 内衰减 **28 级 → 13 级**（每像素斜率减半，所以更柔），尾巴从 24px 铺到 35px。

**下拉为什么把画布切成三段、而不是把画布窗口夹到带子上**（2026-10-18 返工）：面板是原生
`tk.Canvas`，它的内容会被自己的窗口矩形裁掉，所以最早那版干脆把画布 `place(height=1)`
慢慢长高；2026-10-15 改成露出带之后，又升级成「每帧把画布窗口摆成带子 + 带内图元整体
`move(-带顶边)` 顶回去」。功能上对，屏幕上却是**画面撕裂 / 上下跳**（用户报的现象）。
原因在 Windows 那边：`place` 改的是**窗口**的位置与尺寸，窗口管理器先把旧像素整块 BitBlt
到新位置、再等下一次重画补齐 —— 一帧搬一次，屏幕上看就是上下两半错位，阴影也跟着被搬歪。

现在**画布一次就摆满**（位置、尺寸都是最终的，整段动画一个像素都不碰它），裁剪交给
**三块拼贴**（`_paint` 里一次画完）：

| 段 | 内容 | 叠放位置 | 作用 |
|---|---|---|---|
| `[0, 带顶)` / `[带底, 高)` | 背景快照 `empty`（**按段裁好**再贴） | 内容图元**上面** | 把底图和文字色块一起盖回背景 |
| `[带顶, 带底)` | `empty` 与底图按 `mix` 混过的那一条 | 内容图元**下面** | 面板的底子：`mix=1` 全看不见、`0` 全实 |

三段正好铺满画布，一帧传的像素总量不多于一块画布（RGB，不带 alpha）。

⚠️ 两条走过的弯路，别再回去：
- **每帧 `place` 画布窗口**（更早那版）—— Windows 补画面的时机不可控，撕裂；
- **一层全画布 RGBA 盖帘**：一次 alpha 混合就能同时做掉裁剪与淡入，代码最短，但每帧
  要重传一张带 alpha 的整图、画布还得逐像素混合，展开帧数掉得明显（实测 13 帧 vs 9 帧
  那种量级）。另外**「一张整图挪锚点、靠画布边沿去裁」也不成立** —— 图元显示的是整张
  图，挪锚点只是让它错位，露出来的那截内容对不上号（试过：底部那条把背景图的前 211 行
  搬到了下面，屏幕上一块错位的白框）。要用哪一段，就得**裁好**再贴。

⚠️ 一帧只改**图元**、不改窗口，这条守不住就会回到撕裂。`_paint()` 是唯一的落笔点
（换底图 + 贴三段），`_open` / `_frame_*` / `_settle` 都只通过它画。
⚠️ 每帧新出的 `PhotoImage` 必须存回 `fade["tile_photos"]` 拎住引用，否则下一帧就被回收、
那块会闪白板（与收起动画留 `photos` 同因）。

**露出带怎么算**（`_reveal_band`）：面板先挪到「选中行中线 == 字段中线」（见上面「位置」那段），
再让带子从那条中线往两头长：

```
s=0 ──→ 一条退化的线，正好落在「当前值」那一行的中线上（= 字段中线的屏幕位置）
s=1 ──→ 整块画布
上沿 = lerp(原点, 0, s)        下沿 = lerp(原点, 满高, s)     ⟹ 带高恰好 = s × 满高
```

视觉上就是「从字段正中向上下同时长开、把字段盖掉」，第一个选项被选中时看着仍像
「往下长」（上边一撞到画布顶就停）。收起就是同一算式喂 `1 - s`，所以收起**必然是**
展开的镜像 —— 上下两条边一起往里收。

**画布全程不动之后，命中判定也简单了**：`event.y` 就是画布坐标，减掉 `_shadow_top` 即得
面板坐标，不需要任何补偿。只多一条：**带子以外的那几行不认**（它们被背景盖着、看着根本
不存在，点中会很突兀 —— 用户按着字段往下一划就选中了还没长出来的项）。
`_reveal is None`（动画收尾 / 关着）时与静态态完全一致。

**投影那一段不裁内容**（`band=None`）：三段拼贴全收掉，只把底图在「只有面板」与
「面板 + 投影」之间按进度混一次 —— 文字的浓淡一点都不该跟着变。

**三张源图 + 现算帧**：`_open` 存「空背景 / 只有面板 / 面板 + 投影」三张 RGB。
`frame_image()` 给工具用时按 `_paint` 记下的参数**现算**：`Image.blend` 混出底图，再把
带外两段换回 `empty`（与屏幕上的三段拼贴逐字一致）。


**曲线为什么从 `ease_out_cubic` 换成 `soft_in_out`**：前者起步速度是 3 倍，16ms 一帧
的话第一帧就窜掉一大截，观感是「弹开」；`fast_out_slow_in` 更极端（半程走完 97%）。
`soft_in_out` = Fluent 的 `EasyEase` = `cubic-bezier(0.33, 0, 0.67, 1)`，**两端速度都是 0**、
峰值只有 1.5 倍，每一帧的高度增量都均匀 —— 柔和了才敢把时长压到百来毫秒。这条曲线中心
对称（`f(1-t) == 1-f(t)`），所以收起直接取 `1 - soft_in_out(progress)`，
和展开是同一段运动镜像回来。

**收起仍然同步清 `_popup`**：`_close()` 一进来就把 `_popup` 置 None，「松手那一下
必须由面板吃掉」的时序（见 `_on_release`）**一个字没改**；收起动画只是让那块画布
多活 `COLLAPSE_MS` 再销毁，期间它已经不接受任何交互（事件已解绑、`_geom` 已清空）。

**开关为什么要两个进度**：位置必须过 Fluent 的 `fast_out_slow_in`，颜色要线性过渡；
而且**缓动要作用在「行程」上而不是绝对进度上** —— 如果让 `icons` 对绝对进度取
`ease(t)`，「关」的时候 t 从 1 往回走就变成「前 80% 几乎不动、最后猛跳」。
所以 `icons._switch` 收 `progress`（颜色）与 `travel`（位置）两个参数，
`shape.travel_frame(s, start, target)` 负责把线性时间换算成这两个值，
离线预览脚本用的是同一份算式（不抄第二遍）。

**帧驱动器 `widgets._Motion`**：按**真实时间**（`perf_counter`）算进度而不是数帧号 ——
`after(16)` 只是希望值，系统忙时会晚到，按帧号推进会把卡顿放大成慢动作。
它带自增 `token` 可随时取消（展开没跑完就收起、连点、重开都不会打架），
并在 `winfo_exists()` 为假时自己停下（否则关窗后残留的 `after` 会去碰已经没了的控件，
刷出 `invalid command name`）。

**曲线与端点**：`shape.py` 里是纯函数，`fast_out_slow_in` 就是
`cubic-bezier(0.1, 0.9, 0.2, 1.0)`（牛顿迭代解 `x(s)=t`，失败退二分）。动画首末帧必须与
静态端点**逐像素一致**，否则收尾会跳一下 —— `icons.mix` 在 `t=0/1` 返回原色保证了这点，
`tools/preview_ui_icons.py` 会离线断言（含曲线端点与单调性）。

**帧数：`after` 的真实节奏要去量，别猜，而且有两笔账。** `_Motion.start(interval=...)`
里那个数字只是「不早于」：

- **系统那一层**：Windows 默认粒度 ~15.6ms，不提精度的话请求 6ms 落 12.5ms、请求 8ms
  落 11.3ms（`tools/probe_timer.py` 会「提 / 不提」各跑一遍给你看）。提了精度之后
  空跑的主循环请求多少就是多少（6.09ms / 8.23ms）。
- **每帧自己的开销**：Pillow 混图 + 换图 + Tk 把面板重画出去，合计 ~2ms，**压在间隔之上**。
  所以真动画里请求 6ms 落 8.4ms、请求 8ms 落 10.2ms。

也就是说百来毫秒想凑够十几帧，得同时拧：`widgets.FRAME_MS = 6` +
动画期间 `win32.timer_resolution(True)`。**提精度是有借有还的** —— `_Motion._held`
记账，自然跑完 / `cancel()` / 控件已销毁三条路都走 `_release()`。

要重新标定（换机器、换 Windows 版本、改帧间隔）就跑 `python tools/probe_timer.py`
看系统那层，再看 `tools/smoke_ui.py` 的 `[1b]` 一节打印的真实帧间隔与帧数。

**给工具脚本的两个口子**：

- `animate=False`（两个控件的构造参数）—— 完全退化成原来的同步行为。
  `tools/smoke_ui.py` 的几何一节靠它拿确定的稳定态。
- `_settle()` —— 把正在播的动画立刻推到终点（`_reveal` 置空 = 整块画布都露出来）。
  `tools/screenshot_ui.py` 截图前调它（展开途中抓到的是一块只露出中间一段的面板）。
  **调完还要 `root.update()`**，因为 `place` 只是排进几何管理器，Tk 还没落。

动画本身由 `tools/smoke_ui.py` 的 `[1b]` 一节真机验证：把帧回调换成记账的壳数帧数
（壳必须在 `_open()` **之前**装好 —— `_Motion.start` 启动那一刻就把 `on_frame` 抓走了），
断言百来毫秒里 ≥10 帧、帧帧带高都不同、帧间隔中位数 ≤12ms、实测时长在配置值附近、
**展开途中投影必须是 0**、三段各自够帧、精度借了必还（等两段都播完再断言）。
⚠️ 这一节**不能借 `pump()`** 泵帧：它 sleep 8ms，跟帧间隔同量级，会把动画的定时器挤晚
（实测从 15 帧掉到 8 帧、跨度从 113ms 涨到 131ms）—— 量出来的是观察者的样子，不是程序的样子。

另有一条只量系统那层的探针：`python tools/probe_timer.py` 打印不同 `after()` 请求下的
真实中位间隔（提精度 / 不提精度各一遍）。换机器或怀疑「动画变卡了」先跑它。

**要「看」动画**（改曲线 / 时长 / 投影之前之后各看一次）：

    python tools/preview_ui_anim.py [输出路径] [--appearance light|dark]
    # 默认写 docs/anim_combo_light.png / docs/anim_combo_dark.png

它真建一个窗口，按**写死的进度**把帧一格一格喂进去（`_frame_expand` / `_frame_shadow` /
`_frame_collapse`），每帧抓一次窗口像素、按**同一个矩形**裁（字段 + 整个面板盒子，
所以字段是天然的对齐参照物），三段各拼成一行，帧号写在每帧上方。进度写死 → 两张图可以
直接对拍「这次改动到底改了什么」。喂帧前必须 `_motion.cancel()`，否则真动画会插帧。
⚠️ 它同时会把每帧的**露出带几何**打印出来（`p=0.45 露出带 y 140..330（高 190）中线 235`），
「以当前值那一行为中心」「收起是展开的镜像」这两条一眼就能核对 —— 中线应当从原点的
`_reveal_origin` 单调滑向画布中线的位置，而带子高 = `soft_in_out(p) × 满高`。
展开那一段还会多打一列「**带外与展开前不同的像素**」，必须是 0：它是「Tk 真的按那一帧把
画布重画了」的兜底（三段拼贴贴错位置、或者带外那两段没盖上，数字立刻就不是 0）。
⚠️ 裁图矩形用 `box._box`（画布的满尺寸矩形）算 —— 现在画布全程不动，`canvas.winfo_*` 也能
用，但 `_box` 不依赖几何管理器有没有把 `place` 落下来，抽帧脚本里更省事。

### 自绘面板必须自己跟 DPI 走

CustomTkinter 只缩放**它自己创建的控件**；用原生 `tk.Canvas` 画的东西它一概不管。
在 175% 缩放的机器上，字段里的字被 CTk 放大到 21px，而 Canvas 上的选项文字还是逻辑值
14px —— 这就是「下拉菜单文字过小」的真正原因，跟字号常量调多大没关系。

所以面板的几何与字体都要过一遍 CTk 的官方换算（`_get_widget_scaling` /
`_apply_font_scaling`，继承自 `CTkFrame`）：逻辑值 32 → 物理 56，字号 -14 → -24。
`FluentComboBox._geometry()` 负责这件事，所有 GAP/PAD/RADIUS 都从逻辑值现算，
不缓存跨窗口复用。

### 底板一律用 Pillow 画：四边描边 + 抗锯齿圆角

用户报过「圆角过渡不平滑、角落线条太细」和「下拉选框下端无框线」。现在字段底板、下拉
面板、按钮 / 输入框底板**都交给 `mcrender/ui/icons.py:rounded_rect()`**，两个毛病各有来历：

- **圆角不平滑**：`tk.Canvas` 没有抗锯齿，而 CTk 的圆角是用**字形**拼的（`font_shapes`），
  半径 4~5 逻辑像素时角上一圈只有 **5~8 级**过渡色，放大看就是硬阶梯；`polygon_shapes` /
  `circle_shapes` 更差（2~3 级）。Pillow 走 4 倍超采样 + `LANCZOS` 缩回，有十几级。
- **缺底边**：`ImageDraw.rounded_rectangle` 的描边**骑在路径上**（一半在内、一半在外），
  所以盒子必须取满 `[0, 0, W-1, H-1]` 让它**往内**画；盒子若按 `border/2` 内缩，
  最外那半行就被图片边界裁掉，表现正是「上/左/右有边、下边没有」。

> 顺带记一笔，免得以后又冤枉它：`CTkFrame` / `CTkButton` 自己的 `border_width` 在 175%
> 下**并不会**缺底边 —— 实测 112×30 r4 bw1、112×30 + `pack_propagate(False)`、64×30、
> 112×31、bw2 五种组合四条边都齐全。CTk 内部确实把宽高**取成偶数**
> （`math.floor(w/2)*2`，见 `DrawEngine.draw_background_corners`），但那只影响圆角的
> 位置，不影响描边完整性。

`FluentComboBox` / `FluentButton` / `FluentEntry` 都是「挂一个铺满的底板 + 让内容压在
它上面」：下拉字段用 `tk.Label(place(relwidth=1, relheight=1))`，按钮 / 输入框则直接
把图画进 CTk 自己的 Canvas（见下）。两个细节：内容 Label 必须显式
`fg_color=theme.color("control")`（用 `"transparent"` 会解析成窗口底色、边缘露灰）；
`ImageTk.PhotoImage` **必须留一个 Python 引用**，否则被 GC 掉字段变白板。

### 字段里的文字与箭头：`bg_color` 缺一不可，且只能 `place`

用户报过「选框存在不必要竖线」和「轮廓依旧不均匀」，两处都在字段的内容层上：

- **不必要的竖线** —— `CTkLabel` 的底色其实是画在它自己那块 Canvas 上的一层填充，
  而 Canvas 自己的背景取自 **`bg_color`**（见 customtkinter `ctk_label._draw`）。
  只给 `fg_color` 不给 `bg_color` 的话，Canvas 背景是**窗口底色**，比控件本身小两三像素的
  那块就会露出来 —— 在字段里正好是一条**窗口色的竖条**。所以 `_text` / `_arrow` 两个
  标签的 `fg_color` 和 `bg_color` 都要显式写成字段填充色。
- **轮廓不均匀** —— 早先用 `pack`：两个 Label 的自然高 49、字段高 52，`pack` 会在竖直
  方向居中，于是各压住一行描边；两个 Label 之间还留着箭头那个 `padx` 的缝，缝里透出
  别的东西。现在用 `place` 摆进描边以内那块矩形。

> **但只让开 `border` 还不够**（用户第二轮报的「选择区域下方框线不均匀」就是这个）。
> 这两个 Label 是**不透明的底色块**，压到描边上就是把描边擦掉。实测 175%、字段
> 182×52 物理像素、描边占第 0/1 与 50/51 行：Label 落在第 2..50 行 → 底边**上半行**
> （第 50 行）被啃，只剩 `x=2..15 / 147..150 / 172..179` 几段还是描边色，看着断断续续。
> 所以竖直方向再多让 1 逻辑像素（`FIELD_PAD_V`，= 1.75 物理像素），空出整整一行：
>
> ```python
> inset_y = FIELD_BORDER + FIELD_PAD_V     # 逻辑像素
> inner_h = max(1.0, lh - 2 * inset_y)
> widget.configure(width=..., height=inner_h)
> widget.place(x=..., y=inset_y)           # 文字仍然正好竖直居中
> ```
>
> **这个 bug 只查自己画的 Pillow 底板图是查不出来的** —— `_field_img` 四条边完美无缺，
> 坏在真窗口里。`tools/smoke_ui.py` 因此改成**抓真窗口的像素**，断言这四条描边
> **整条一样深**。

`pack` 换 `place` 时踩到一个**没有任何报错**的坑，值得单记：

> **`CTkBaseClass.place()` 不接受 `width=` / `height=`** —— 传了直接
> `raise ValueError("'width' and 'height' arguments must be passed to the constructor")`。
> 尺寸只能走 `configure(width=, height=)`。如果这个调用又被 `try/except` 兜住，
> 现象就是**文字和箭头整个不显示**、字段变成一块空白底 —— 而不是报错。
> `tools/smoke_ui.py` 专门加了一条断言盯它（「字段里的文字摆进去了，且没盖住描边」）。

还有一个方向相反的坑：**CTk 的 `place()` 和 `configure(width=/height=)` 会自己按 DPI
缩放**（`_apply_argument_scaling` 缩 `x`/`y`，`_set_dimensions` 缩宽高）。所以
`_layout_children()` 里一律用**逻辑像素**算，再乘一遍就是双倍缩放。
偏偏 `winfo_width()/winfo_height()` 给的是**物理**值，所以要先除回去。

### 下拉面板搬进主窗口（2026-10-13）

用户的原话是「下拉菜单与主程序实际上作为两个窗口显示」——希望**在保留设计的前提下**把
面板放进主窗体里。改完之后面板是主窗口里的**一块子 `tk.Canvas`**
（`place` 到「选中那一行正好压住字段」的位置 + `tk.Misc.tkraise`），不再是独立窗口。

原来的做法是 `overrideredirect` + `-topmost` + `transient` 的 `Toplevel`，换来三样东西
必须自己扛：

| 独立窗口的代价 | 搬进窗口后 |
| --- | --- |
| 任务栏 / Alt-Tab 里像第二个窗口，主窗口一动它不跟手（要靠 `_anchor` 自己盯位置） | 它本来就是主窗口的一部分，跟着走、被客户区裁剪 |
| 焦点会落到弹窗上，此后切到别的程序**收不到 `FocusOut`** → 只能按 `WATCH_MS` 轮询 `GetForegroundWindow` 兜底（`_watch_foreground` / `_watch_armed` / `WATCH_MISSES` 那一整套） | 整块面板跟主窗口同生共死，切走时它自己就被盖住了 —— 那套轮询整个删掉 |
| 圆角得靠**分层窗口**（`UpdateLayeredWindow`，逐像素 alpha）或者 `-transparentcolor` 抠色键 | 见下：抓一帧背景当底图 |

**代价：Tk 的子控件没有 alpha。** 圆角那四小块玻璃必须自己填。做法是在摆面板**之前**
先抓一帧主窗口内容（`_backdrop` → `win32.grab`），把 Pillow 画的圆角面板用
`Image.alpha_composite` 叠在这张快照上当画布底图 —— 四角露出来的就是**面板底下真实的
样子**（在这一版的真实布局里，就是被盖住的那排按钮和窗口底）。抓不到快照时退化成窗口
底色填充，面板照常可用，只是四角有一小块色差。

> ⚠️ **抓图必须在摆上面板之前。** 面板是主窗口的子控件，`PrintWindow` 会把窗口内容
> 连同面板一起画出来 —— 摆好再抓就成了「面板 + 面板」，叠完四角永远是一块方色的底。
> 这和以前「烘焙抓图画布时抓到了上一帧的悬停竖条」是同一类错误：**抓图里不能有自己**。

`rounded_rect` 出的是**直通 alpha**，所以这里就该用 `Image.alpha_composite`；只有
`win32.blit` 那条分层窗口的路才要求预乘（那套 `premultiply` / `blend` 原语留在
`win32.py` 里，UI 已不再使用）。

**位置**（`_anchor`）：竖直方向**不是**「挂在字段下方」，而是**把面板挪到「选中那一行的
中线 == 字段中线」的位置** —— 面板于是整个盖住字段、并向上探出字段之外（2026-10-15 用户
要求「以选中的选项为中心覆盖原来的显示区域」，拿系统设置里那个「缩放」下拉的前后两张
截图配准后逐像素量过：选中项那一行的中线与字段中线**重合，误差 0**）。横向仍旧对齐字段
左沿。参数 `row_cy` 由 `_open` 先把行几何算出来再传进来（`_anchor` 只做纯算术）。

面板是子控件，超出去的部分会被窗口裁掉，所以「放得下吗」要跟**窗口客户区**比，不能跟
屏幕比（以前跟屏幕比，还得把 DPI 虚拟化过的屏高还原成物理值）。横向越界先往左收；竖直
方向**两头都夹**进客户区 —— 夹完就不再保证「选中项压在字段上」了，那属于窗口太矮，只能
这样。越界是用**画布**判的（它比面板大一圈），按面板判的话那圈投影会被窗口切掉。
坐标一律**物理像素**：画布是原生 `tk.Canvas`，它的 `place()` **不走** CustomTkinter 的
DPI 换算，而 `winfo_rootx/rooty/width/height` 给的都是物理值，两边正好对得上
（`tools/smoke_ui.py` 会断言「面板整个落在客户区里」+「选中那一行的中线压在字段中线上」，
175% 缩放下都过）。

> ⚠️ **投影留白四边等宽**（`_shadow_top == _shadow_pad`）。旧版是「面板挂在字段下方」，
> 画布顶边只能往上探到字段底沿（`min(pad, gap)`），否则那圈阴影会糊到字段身上啃掉它的
> 底边描边。现在面板本来就把字段盖住了，那个「上沿不能越界」的约束随之作废 ——
> `clip_top` 那套东西（`Painter.shadow(clip_top=...)`、`preview_ui_combo` 的对应参数）
> 一并删掉，四边对称反而更好画、也更好验。

> **抬画布只能用 `tk.Misc.tkraise(canvas)`。** `tk.Canvas` 把 `lift` **和** `tkraise`
> **两个名字都**重定向成了 `tag_raise`（"把某个画布图元抬到另一个上面"），
> 直接写 `canvas.lift()` / `canvas.tkraise()` 会抛
> `wrong # args: should be "raise tagOrId ?aboveThis?"` —— 库注释里也写着
> 「the preferred name for them is `tag_lower` / `tag_raise`」，就是别再碰这两个别名。
> 又是一个「和 Tk 内部撞名」的实例（下面那节还有两个）。

**图元顺序**：面板底 → 悬停/当前值色块 → 选中竖条 → 文字。**文字必须最后建**：反过来的
话「当前值」和指针划过的那一项，整条文字会被色块盖住（用户报的「选中选项无法显示文字」）。
现在靠三次循环刻意排开（先所有色块、再所有竖条、最后所有文字），
`tools/smoke_ui.py` 会按三种悬停状态各抓一张最终图数文字像素。

**面板的「最终显示图」怎么拿**：面板就在主窗口里，所以 `panel_image()` 直接
`win32.grab(主窗口)` 再按相对客户区的位置裁一块 —— 比原来那条 `layer_image()`
（独立窗口 + 预乘 alpha + 得小心 `Image.alpha_composite` 算错）简单得多，而且是
**不透明的 RGB**，自检脚本可以直接逐像素比。

<details>
<summary>历史：独立窗口时代的分层窗口 / 抠色键那套（已删除，留个记录）</summary>

用户最初的抱怨是「下拉菜单边角异常」，根因是 `-transparentcolor`：DWM 只把**整像素等于
色键**的像素透掉，而圆角上那圈**抗锯齿的过渡像素**是色键（`#FF00FE`）与面板色的混合，
于是留在屏幕上 —— 四个角各一小团**品红**（实测 148 个像素）。改成 Win32 分层窗口
（`UpdateLayeredWindow`）后走真·逐像素 alpha，圆角直接对着桌面合成。

那条路上踩出来、现在仍然写在 `mcrender/ui/win32.py` 里的几条：

| 现象 | 真因 |
| --- | --- |
| `UpdateLayeredWindow` 返回 0，`GetLastError()` 还是 0 | 句柄给的是 `winfo_id()`，那是 Tk 的**内层子窗口**（`WS_CHILD`）；分层窗口只认顶层，得 `GetAncestor(hwnd, GA_ROOT)` |
| 弹窗变成一整块**黑板** | 烘焙时抓窗口抓早了/抓错了：Tk 顶层窗口的**外层容器从来没被画过**（被内层画布铺满），`PrintWindow` 打上去稳定返回全黑 → 抓的必须是**画布**，还要 `update_idletasks()` + 哨兵像素确认 |
| `blit()` 永远返回 False | `at=None` 时写成 `ctypes.byref(None)` 会**抛 TypeError**，被兜底 `except` 吞掉 |
| 设过 `-transparentcolor` 的窗口再 blit 必失败 | 前者内部调 `SetLayeredWindowAttributes`，和 `UpdateLayeredWindow` 互斥，两条路只能选一条 |
| 「选中选项无法显示文字」 | 把「面板 + 文字」烤成一张底图、色块直接叠上去，当前值 / 悬停项的文字被整条盖住 |
| 「竖线有时渲染为黑色」（12 次复现 1 次） | 从画布抓图里反解文字层，前提是「底图上除了文字都是纯面板色」；竖条一旦进了抓图就会被反解成一条**文字色**的竖条 |
| 「深色模式下角落颜色有些奇怪」（四角各一圈亮 21 级的弧） | 反解文字层的遮罩画成了**直角**矩形，够不着**圆角那段弧**（半径 17.5 物理像素时弧的中点离两条边各 5 像素），描边弧被当成文字反解、又叠回本来就有描边的面板 |

这些机制（`_bake_layer` / `_text_layer` / `_compose_layer` / `layer_image` /
`_watch_foreground`）连同它们的断言一起删掉了 —— 面板不再是独立窗口，它们就没有立足点了。

</details>

### 两个坐标空间：`winfo_screen*` 是逻辑值，窗口几何是物理值

这台机器（175% 缩放）实测：

```
winfo_screenwidth/height = 1255 × 837     ← DPI 虚拟化后的**逻辑**值
winfo_rootx/rooty        = 92 / 112       ← **物理**像素（client 原点）
winfo_width/height       = 1610 × 1295    ← 物理
屏幕实际                  = 2196 × 1464    ← 物理
```

`FluentComboBox` 现在只跟**窗口客户区**打交道（面板是子控件，本来也出不去了），
所以 `_anchor` 里用的全是 `winfo_width()/height()` 和 `winfo_rootx/rooty()` 的差值 ——
一路物理值，没有跨坐标空间的比较。但**字段里的文字与箭头**仍然吃这个坑：
CTk 的 `place()` / `configure(width=/height=)` 会自己按 DPI 缩放（
`_apply_argument_scaling` 缩 `x`/`y`，`_set_dimensions` 缩宽高），所以
`_layout_children()` 里一律用**逻辑像素**算，再乘一遍就是双倍缩放；
偏偏 `winfo_width()/winfo_height()` 给的是**物理**值，所以要先除回去。
而**下拉面的**几何反过来：它是原生 `tk.Canvas`，`place()` 不缩放，所以那边一路物理值。

> 顺带说明为什么 plain `tk.Toplevel` 的 `geometry()` 是物理像素、而 `CTk` 主窗口是逻辑：
> `customtkinter.CTk.geometry()` 被 CTk 覆写成「先乘窗口缩放」，原生 Toplevel 走的是
> Tk 的裸实现。


### 别用会和 Tk 内部撞名的成员

`FluentComboBox` 是 `CTkFrame` 子类，自己定义的同名私有成员会悄悄覆盖父类实现，
编译期毫无征兆、只在特定时机炸。已经踩过两次：

| 名字 | 撞上谁 | 症状 |
| --- | --- | --- |
| `self._bind` | `tkinter.Misc._bind` | `super().__init__()` 里 `self._bind('bind', ...)` 直接 TypeError，窗口建不起来 |
| `self._canvas` | `CTkFrame` 内部画布 | 置空后 CTk 收到 `<Configure>` 重绘时 AttributeError |
| `canvas.lift()` / `canvas.tkraise()` | `tk.Canvas.tag_raise`（这两个名字**都**被重定向过去了） | `wrong # args: should be "raise tagOrId ?aboveThis?"` —— 抬自己上来的唯一写法是 `tk.Misc.tkraise(canvas)` |

`python tools/check_ui_names.py` 会沿**真实继承链**回溯（`FluentComboBox → CTkFrame →
CTkBaseClass → … → Misc`），把冲突名字列出来。只查链上的祖先，所以 `_text`、`_command`
这类与 CTkLabel / CTkButton 同名（但不在链上）的成员不会误报。
⚠️ 最后那条 `lift`/`tkraise` 它**查不出来** —— 那是**方法调用**撞名，不是「自己定义了
同名私有成员」。这类只能靠真机跑一遍发现（这次就是 `smoke_ui.py` 直接抛出来的）。

**有意覆写不算冲突**：同名方法体里调了 `super().<同名>(...)` 的，是刻意先让父类逻辑跑完
再叠加自己的行为，脚本会标成「有意覆写」而不计入冲突。典型是 `_set_appearance_mode` ——
CustomTkinter 的 `AppearanceModeTracker` 在切主题时调它，我们重写它以让 Pillow 底板跟着
重画，**第一行必须 `super()._set_appearance_mode(mode_string)`**，否则库自己的深浅色链路
就断了。

### 看一眼真实窗口：`tools/screenshot_ui.py`

    python tools/screenshot_ui.py 输出.png --appearance light [--widgets]

`ImageGrab.grab()` 在无 GUI 会话里只会返回一张和屏幕对不上的灰图（实测放一块已知颜色的
纯色窗口去抓都抓不对），但 Win32 的 `PrintWindow(hwnd, memDC, PW_RENDERFULLCONTENT)`
能让窗口**自己**把内容画进一块内存 DC，无边框窗口、Mica 背景都能画出来。
抓图这些原语都收在 `mcrender/ui/win32.py`，**别在工具里另抄一份** ——
以前 `screenshot_ui.py` 里那份漏了 64 位句柄的 `argtypes`，偶发 `OverflowError`。

下拉面板现在就在**主窗口里面**（盖在字段上的一块子画布），所以 `win32.grab(root)` 一次
就把它带上了 —— 不需要再单独抓一次弹窗、按屏幕坐标贴回来。那份「优先拿
`layer_image()`，拿不到就抓弹窗 + 抠色键 + 品红族规则」的 `punch` / `compose_menu` /
`settle_menu` 都删掉了（那是面板还是独立 `Toplevel` 时的事）。
`--widgets` 会把第一个下拉展开一起截。

**别靠放大截图目测圆角半径**：NEAREST 放大 8 倍会把细描边的抗锯齿放大成阶梯，看着像
15px 而实际只有 7px。要量就直接量像素 —— 找描边包围盒，再看「距上边 1px 的那一行里描边
从哪开始」，用内收量 `d` 反推半径（圆方程 `d = R - sqrt(2R-1)`，`d=3 → R≈7`
= `4 逻辑像素 × 1.75`）。顺手量包围盒宽高还能确认尺寸没被 DPI 吃掉
（实测 196×52 物理 = 112×30 逻辑，字段高正是设计的 30）。

### 关窗要清掉所有待触发的 after

`root.destroy()` 会把注册过的 Tcl 命令一并删掉，之后那些**仍然排着**的 after 被 Tcl 调到
就找不到命令，控制台刷一串 `invalid command name ...`。来源有三处：

- 本程序自己的日志轮询 `_drain_log`（每 60ms 自续）；
- CustomTkinter 内部的外观模式轮询（每 30ms，`AppearanceModeTracker.update`）；
- CustomTkinter 内部的 DPI 轮询（每 100ms，`ScalingTracker.check_dpi_scaling`）。

后两个是库的，既不保存任务 id 也没有关闭入口。所以 `MainWindow.close()` 在 destroy 之前
按 Tcl 层 `after info` 把待触发任务全部取消 —— 反正要退出，全清是对的。

这类报错**只打印到 stderr，不抛 Python 异常**，`try/except` 包 `update()` 也拦不住，
只能在子进程里跑一遍关窗再检查 stderr。

### 真实窗口冒烟：`tools/smoke_ui.py`

    python tools/smoke_ui.py

把界面**真的建起来**跑一遍：下拉定位与 DPI 换算、量出相邻选项块之间真的留出了
`ITEM_GAP` 的缝、普通项确实没铺底色（只有悬停 / 当前值才有色块）、选中竖条量出来确实是
3×16、**竖条右沿到选项文字首列的缝**、悬停与当前值同深浅、字段与面板的**四条边都有描边
（含底边）**、字段转角确实是抗锯齿的（角上有一堆过渡色）、**四周那圈投影的浓淡是渐变的**、
按钮 / 输入框没有漏网的原生实现且都铺上了 Pillow 底板、悬停会换底色且移开能还原、
**字段里的文字与箭头确实摆进去了**（CTk 的 `place()` 不接受 `width/height`，踩过就整块消失，
见上文）、**面板是主窗口里的子控件**（同一个顶层窗口句柄、整个落在客户区里）、
**选中那一行的中线压在字段中线上**（面板因此向上伸出字段之外、把字段盖住）、
主窗口 4 下拉 / 1 开关 / 外观循环、关窗后 stderr 是否干净，共 97 项。

> ⚠️ 「字段四条描边整条一样深」这一条**必须在面板关着的时候抓**。面板现在是**盖在字段上**
> 的，而 `win32.grab()` 走 `PrintWindow(PW_RENDERFULLCONTENT)` —— 它给的是「这块矩形**在
> 屏幕上**的样子」，会把压在上面的兄弟画布一起抓进来（实测量到的是面板底色、四条边全被判成
> 不均）。这一条要验的是「字段里的 Label 有没有啃到描边」，跟面板在不在无关，所以先
> `_close()` 量完再 `_open()` 开回来（关一次会换一块新画布，旧的 `popup` 成了死窗口，
> 后面几节要用的话记得重新取 `box._popup`）。

动画那节（`[1b]`）除帧数 / 时长 / 精度之外，还钉住了**展开方向**这五条：
- **以「当前值」那一行为中心向两侧延展** —— 采样露出带：头一帧的带子中线必须落在那
  一行的中线上（±4px），途中上沿只往上走（`min(tops) < 原点 - 10`）、下沿只往下走
  （`max(bottoms) > 原点 + 10`），铺满时正好是 `(0, 满高)`。而那一行的中线**在屏幕上
  正好压在字段中线上**（面板是挪过去盖住字段的，见上面「位置」那段）—— 几何那一节会
  单独断言这一条，所以「从字段正中长开」是算出来的，不是看着像；
- **展开途中画布窗口和每一行在屏幕上的位置一个像素都没动** —— 逐帧取画布窗口的
  `winfo_*` 矩形（全程只能有一个值）+ 每一行图元的屏幕 y（每行各自只能有一个值）。
  这条是 2026-10-18 返工出来的：早先每帧 `place` 画布窗口夹到带子上，上面那些数字全绿，
  屏幕上却是整份列表**撕裂 / 上下跳**（Windows 搬旧像素、等重画 —— 一帧搬一次就错位）；
- **展开途中指针的命中判定跟着带子走** —— 用每一行在屏幕上的中线反推 `event.y`
  （`屏幕 y - 画布窗口 rooty`）喂给 `_index_at`：**带内**必须还回那一行，**带外必须 -1**
  （被背景盖着、看着还不存在的行不能点中）；
- **收起是展开的镜像** —— 收起途中采样，**上下两条边一起往里收**（旧实现是顶边钉死、
  只抬底边，量出来的顶边永远还是 0）；
- **露出带以外逐像素等于背景** —— `frame_image()` 拿合成帧，逐行扫三列：带外必须
  `== fade["empty"]` 的同一个像素、带内必须有差异。这条只能查合成帧，抓屏会带上窗口
  非客户区的偏移（`PrintWindow` 从窗口左上角画起），拿来判逐像素会整体错位。
- **展开途中面板始终实心** —— 采样 `_fade_alpha`，全程必须 1.0（判据是「**没有**半透明帧」）。
  ⚠️ 这条与 2026-10-19 之前的写法**正好相反**（那时要求「途中有半透明帧」）：底图淡入时
  文字还是全不透明的画布图元，屏幕上就是「数字浮在窗口按钮上」，用户报的「打开频闪」。
- **展开落地 → 投影淡入的接缝不掉帧** —— 手工喂 `_frame_expand(1.0)` 与 `_frame_shadow(0.0)`
  两帧，`frame_image()` 必须**逐像素相同**。这条只能手工喂：真机抓屏是按真实时间跑的，
  正好卡在那一帧的概率很低（也正是它能活到现在的原因）。把 `base_key="plain"` 注掉
  立刻变红，差异 bbox 正好是整块面板。

专门盯用户报过的缺陷 / 本轮改动的那几项，判据都在**真窗口抓下来的像素**上：

- **面板是主窗口里的子控件** —— `popup.master is root`、`popup.winfo_toplevel() is root`，
  且 `win32.top_hwnd(面板) == win32.top_hwnd(主窗口)`（任务栏 / 切换器里不会多一个窗口）；
- **面板四角露的是被盖住的背景** —— 先给主窗口铺一块**明显的绿底** `#0F7B0F`，展开前抓一张
  主窗口内容当背景真值，展开后再抓一张，断言面板四角像素**基本等于背景真值里的同一个点
  （只会因紧贴的投影更暗一点，0~25 级）**，且离「卡片色 / 窗口底色」都足够远。只用窗口底色
  当参照的话，「四角填了窗口底色」这个错误实现也会通过（两者颜色太近）；
  ⚠️ `panel_image()` 默认裁的是**面板**那块（不含投影留白），所以基准图取样要往里让
  `(pad, top)`；要连投影一起看就传 `with_shadow=True`；
- **画布四周留出等宽的投影余地** —— 画布 = 面板 + 四周各 `_shadow_pad`（`_shadow_top` 就等于
  `_shadow_pad`，四边对称）。旧版顶边只探到字段底沿（`min(_shadow_pad, GAP)`），那是为了
  不让柔光盖到字段身上 —— 现在面板本来就把字段盖住了，这条约束作废；
- **选中那一行的中线压在字段中线上** —— 面板顶边 == 字段中线 − 那一行在面板里的中线，
  于是面板向上伸出字段之外、把字段整个盖住（这是用户要的「覆盖原来的显示区域」）；
  冒烟里字段特意 `pady=90` 往下摆够，不然面板会被「夹进客户区」而量到夹过之后的位置；
- **四周有柔和投影** —— 在面板竖直中线上从画布左沿往面板取一串亮度：**贴边最暗 → 半程居中
  → 外沿回到背景**（证明是渐变而不是一块平色，也没有在画布边沿被切出硬边）；
- **每个下拉面板都完整落在客户区里** —— 面板是子控件，超出去会被窗口裁掉（不像独立窗口
  能盖到外面），175% 缩放下 4/4 都必须过；
- **子控件自己的 `Configure` 不会误收面板 / 主窗口的 `Configure` 会收** —— 用一个只带
  `widget` 属性的假事件分别以字段和主窗口的身份各调一次 `_on_host_configure()`；
- **点主窗口别处会收起面板** —— 面板现在跟其它控件是同一个窗口里的兄弟，全靠绑在顶层
  窗口上的 `<Button-1>` 那条（Tk 的 bindtag 让子控件的事件也会走到它）；
- **字段四条描边整条一样深** —— 抓真窗口像素（只查 `_field_img` 查不出来，见上文）；
- **选项文字没有被色块盖住** —— 把 `_hover` 设成 -1 / 0 / 1 各重绘一次，抓三次最终图，
  数每一行里「离面板底、色块、竖条三种颜色都足够远」的像素。
  ⚠️ 不能用「暗像素」当判据：**深色模式下文字是亮的**，第一版就是这么误报的。
  另外判「有没有色块」的探针要**贴着色块上沿取**（`y0 + 3`）—— 文字和蓝竖条都竖直居中，
  取行中线会把文字误判成色块；
- **竖条与选项文字之间有可见缝隙** —— 量最终图：竖条右沿到文字第一列墨迹。
  ⚠️ 判「墨迹」要用**偏离该行背景色**，不能按「离哪种颜色最近」分类 —— **抗锯齿的中性灰
  `(107,107,107)` 离蓝 `(0,103,192)` 比离近黑 `(26,26,26)` 还近**，会把文字边缘误判成竖条；
- **只在主窗口处于前台时才算「没走失」** —— 直接把 `win32.foreground` 换成「主窗口 / 别人
  的 / `None`」三种再问 `_host_is_foreground()`（面板是子控件，只要比一个句柄了）；
- **按住不放期间面板一直活着** —— 点选项的时序：`<Button-1>` 之后**泵 250ms 真实时间**
  （把 idle 队列清空），面板必须还在；松手（`<ButtonRelease-1>`）之后才收。
  ⚠️ 泵的时候要**把 `win32.foreground` 钉成自己**：自动化跑时主窗口多半不在前台，
  「失去前台就收起」那条 60ms 定时器会抢先把面板收掉，断言就测不到本来要测的东西。
  ⚠️ 这条**不能**只在 `event_generate` 之后立刻看一眼 —— 那一刻面板确实还在
  （`after_idle` 还没跑），第一版断言就是这么假通过的。

### 关掉的面板「一部分又显示出来」：快照污染 + 收起回弹（2026-10-18 第二轮）

用户报的原话是「刚刚关闭的菜单一部分会意外重新显示」。截图上是一条 46×247 的竖带、
两侧带着柔光 —— 形状像「面板的一小段」而不是整块菜单。查下来是**两个独立原因**叠在一起。

#### 原因一：背景快照把别人的面板拍进去了

下拉的画布底图是 `_backdrop()` 抓的一帧主窗口内容，**露出带以外**看到的就是这张图。
而 `PrintWindow(PW_RENDERFULLCONTENT)` 打的是主窗口的**内层子窗口**，会把所有子窗口一起
画进去 —— 包括**另一块还开着的面板画布**。于是 B 打开时抓到的「空背景」里明晃晃躺着 A，
之后 B 的每一次展开 / 收起都拿这张图当遮挡层 → A 的菜单被一字不差地**重播**一遍。

真机上点另一个字段时正好撞上：**Tk 先派发控件自己的绑定（打开新的），再派发 toplevel 的
绑定（收起旧的）**，抓快照那一刻旧面板还摆在那儿。

修法两件，都在 `_open()` 里、**抓图之前**：

- `_dismiss_other_panels()`：遍历模块级 `_LIVE_COMBOS`（`weakref.WeakSet`，窗口销毁后自动
  消失），把**同一个 toplevel** 里还开着 / 正在收的面板立刻 `_close(animate=False)`；
- `_note_patch()` / `_patch_backdrop()`：把面板**打开时**抓的那张 `empty`（那时它底下
  什么都没有，天然干净）记下来，等下一次抓快照时按矩形求交、贴回去。

为什么非要「贴回去」而不是「让系统重画一遍」：`destroy()` / `place_forget()` 之后
`PrintWindow` 返回的仍是窗口**上一次合成**的表面，实测 `update_idletasks()`、
`RedrawWindow`（内层 / 外层句柄、带 rect 或整窗）、`UpdateWindow`、`DwmFlush()`、
`sleep 50ms` **一个都刷不新**，只有 `root.update()`（跑一轮事件循环）行。而在 `_open()`
里调 `update()` 会把正在派发的事件再派发一遍，不能干。

#### 原因二：收起的第一帧跳成「全开」

`_frame_collapse()` 原来是 `_reveal_band(alpha)` + `mix=eased`（`alpha = 1 - soft_in_out`），
`s=1` 就是全开。展开没播完就关闭时（点得快、连点字段），第一帧直接喂 `s=1`：

    展开 25ms 后关：关之前 带 (82,119) mix 0.887 → 收起第一帧 带 (0,318) mix 0.0

屏幕上就是「刚关掉的菜单又整个冒了一下」。修法：`_close()` 里把当时的进度记进
`_collapse_from` / `_collapse_mix`，收起从这里接着走。带高恰好 = `s × 满高`（见
`_reveal_band`），所以 `s` 直接由带高反解，不用另外存一份。

#### 写在测试里要注意的

- 三条新断言（`smoke_ui.py` 的 `[1b]` 尾）：旧面板立刻收掉 / 重叠区的快照等于旧面板
  打开时存的干净背景 / **两块都收起来后屏幕与关之前逐像素一致**。把 `_dismiss_other_panels()`
  和 `_patch_backdrop()` 临时注掉再跑，三条**全部变红**（屏幕那条的差异 bbox 正是用户报的
  那一块），说明判据抓得住。
- ⚠️ **不能拿「现抓一张干净基准」来逐像素比**。同一个窗口前后两次 `PrintWindow` 都不保证
  是同一帧：实测后一次会返回**更旧**状态的表面（整排后加的控件凭空不见），差异只落在
  字段描边那 3 列上（最大 30 级、224 个像素），看着像「右缘一条 1~2px 的细线」，很容易
  误判成错位。第一版断言就是这么红了两天。能逐像素比的只有**我们自己存下来的那张图**。

### 点开下拉要多久：`_open()` 的同步预算（2026-10-19）

用户报「打开效果不平滑、偶发闪烁打开」。**「闪」不是画出来的错** —— 逐帧抓真屏幕
（一轮 34~57 帧覆盖整段 110ms）查过：带外始终等于背景、没有一帧是空的。真正的原因是
**点下去到面板开始动之间那段同步等待太长**，长到 100ms 上下，用户先感到一顿、再看到面板
涌出来，就成了「闪」。

改之前 `_open()` 的账单（中位 **107ms**，最坏 136ms，每次点开都要付）：

| 段 | 耗时 | 说明 |
|---|---|---|
| `win32.grab` → `PrintWindow` | **26~36ms** | 让窗口重新渲染一整帧 1085×805 |
| 整窗 `RGBA → RGB` 转换 | **9~13ms** | 抓完整窗再裁，等于白白解码五倍大的图 |
| `icons.rounded_rect`（面板底板） | **17~27ms** | 4× 超采样 + LANCZOS，268×262 |
| `icons.soft_shadow`（四周投影） | **9~11ms** | 高斯模糊 |
| `icons.rounded_rect`（选项块） | **4ms** | |
| 图元 / `place` / `_paint` 首帧 | ~10ms | Tk 那一层，省不掉 |

三处改动，都是「同样一张图，别每次重算」：

1. **`grab` 改走 `BitBlt`（从窗口自己的 DC 搬像素）**，而且**只抓要盖住的那一块**。
   实测 `PrintWindow` 全窗 **34ms** vs `BitBlt` 全窗 **5.9ms** vs `BitBlt` 只抓 390×384
   **1.1ms**，三者**逐像素完全相同**；被别的窗口压住时两种方式拿到的也都是**自己的**
   内容（走 DWM 重定向表面，不是屏幕像素）。`PrintWindow` 留作兜底：窗口还没被合成出来
   时 `BitBlt` 会得到全黑，那时候再让窗口自己画一遍。
2. **`Image.frombuffer` 用 `BGRX` 直读**，不再「RGBA 共享图 + `.convert("RGB")`」：
   整窗那一步实测 4~13ms → **1ms**。（`frombuffer` 出来的是只读共享图，必须 `.copy()`。）
3. **`rounded_rect` / `soft_shadow` 加参数缓存**（`icons._RECT_CACHE` / `_SHADOW_CACHE`，
   详见函数注释）。这几张图只看「尺寸 + 颜色 + DPI」，一次会话里几乎不变 —— 第一次
   40ms，之后 **0ms**。拿到的图是**只读**的，别就地改。

结果：`_open()` **107ms → 10~17ms**（首次 43ms，是缓存冷的代价）。展开帧间隔中位
**12.3 → 11.3ms**、>15ms 的帧 **7/124 → 0/136**（每帧混图只混带子那一条，见 `_paint`）。

> ⚠️ 这段是**点击事件里同步跑的**，所以它的每一毫秒都是「点下去没反应」的时间。
> 以后再往里加东西（新图、新查询、新 IO），先想清楚这笔账；60ms 是一条体面的线。

### 合成真鼠标：`tools/check_option_click.py`

    python tools/check_option_click.py

「点下拉选项不能顺带点到下面盖着的按钮」的**完整**回归。用
`SetCursorPos` + `mouse_event` 合成真鼠标：按下 → 跑事件循环 → 松开，覆盖 5 个场景
（点选项 / 点面板留白 / 按住一项拖到按钮上再松手 / 连点字段两下 / 按住一项拖很远再松手），
每个都要求「按钮的 command 没被顺带触发」，并会先断言**鼠标确实压得到按钮上**
（压不到就是假通过）。退出码非 0 表示有漏点击。

为什么不能并进 `smoke_ui`：这个 bug 的两层根因都在 `event_generate` 的盲区里 ——
「一次点击是两个独立事件」（问题出在按下与松手**之间**）和「release 投给谁由 OS 按指针
位置决定」（`event_generate` 是直接塞给某个控件）。`smoke_ui` 里那条确定性断言守的是
同一个不变量，这边守的是端到端真事件。

⚠️ **合成鼠标事件由 OS 按指针位置投递，所以测试窗口必须在前台**。脚本是后台起的，前台
往往是浏览器 / 编辑器 —— 那时点击全落到别人窗口里，被测窗口「什么都没发生」，
「按钮没被顺带触发」这类断言于是**假通过**（比失败还糟，2026-10-18 就被它骗了一次）。
所以开跑前先 `win32.force_foreground`（`AttachThreadInput` 到前台线程 + `SetForegroundWindow`，
绕开前台锁）抢前台，抢不到就**直接作废、不下结论**；抢到之后再用「点字段能弹出面板」预检
一次，确认合成事件真的进得来。⚠️ 那个函数里比较句柄要用 `top_hwnd()` / `foreground()` 给的
**整数值** —— `c_void_p(...) == int` 永远是 False，会让「明明抢到了」被判成失败。

当前解释器缺 tkinter 时会自动在同机其它 Python 里找一个重新执行自己
（找不到就报错退出，不会假装通过）。

**这个脚本值得先跑**：`_bind`、`_canvas`、DPI 漏缩放这三个问题都是它抓出来的，
离线静态分析一个都发现不了。行距这类「画出来的东西」也直接量 Canvas 图元的实际坐标
（`canvas.coords`），而不是回头再信一遍常量。

### 描边质量：`tools/check_borders.py`

    python tools/check_borders.py [--appearance light|dark]

把窗口建起来，遍历所有「带描边 / 带圆角且自己有色」的控件并记下它们相对客户区的矩形，
再用 `PrintWindow` 抓成位图，然后逐控件：

- 沿四条边的**中点**往里取 5 个像素，与「内侧 7px 处的底色」比色（最大单通道差 > 12
  算另一种颜色），某条边一个都没找到就判缺边 —— 阈值不能设太高，深色模式下描边
  `#3F3F3F` 对底色 `#2D2D2D` 只差 18；
- 在四个角上各取 14×14 数**颜色种数**，< 8 种说明圆角是硬阶梯。

**要跳过透明容器**（纯布局用的 `CTkFrame`、开关外壳）：它们没有「面」，角上本来就是
均匀背景色，数出来恒为 1，不排除就是一堆假报。

**打包注意**：`customtkinter` 的主题与图标是**数据文件**（`customtkinter/assets/`），
所以 `build_exe.bat` 必须用 `--collect-all customtkinter`（以及 `darkdetect` / `pywinstyles`）；
只写 `--hidden-import` 会缺文件、窗口样式丢失。

**回归验证**：把改动前的 `mcrender_gui.py`（git HEAD）与新的 `mcrender/app.py` 并排加载，
对 `is_archive` / `pack_kinds` / `manifest_info` / `find_recipes` / `find_matching_rp` / `unpack_archive` /
`Generator.out_root` / `Generator._relative` / `load_config` / `save_config` 跑同一批输入，
结果**逐项一致**（唯一差异是有意新增的 `appearance` 键）；并确认 `import mcrender.app` 不需要 tkinter、
`import mcrender_gui` 不会牵连 customtkinter。

## 标题栏图标不清晰（本轮修复）

**现象**：窗口左上角标题栏那个工作台图标糊成一团，看不出 3×3 格子。

**两层原因，缺一不可**：

1. **ico 的 16/24/32 档本身就糊。** 图标是 9 个圆角格子 + **透明缝**拼成的
   （`assets/icon.png`，256²）。缝只有 **5px 宽**（`alpha < 8` 的部分；算上抗锯齿边缘约
   7px），缩到各档是 `5 * size / 256`：16 档 **0.31 物理像素**、24 档 0.47、32 档 0.62 ——
   降采样时 alpha 被平均成半透明，格子就黏成一团。48 档有 0.94px、64 档以上 1.25px+，
   所以只有小尺寸那三档出事。
2. **Windows 只拿到 16 档，还要再放大 1.75 倍。** Tk 的 `wm iconbitmap` 是把 ico 的
   **16×16** 那一档塞进**窗口类**图标 —— 实测 `GetClassLongPtr(hwnd, GCLP_HICONSM)`
   拿回来的位图就是 16×16。本机 175% 缩放下标题栏图标要 28px，于是 16 档被**拉大**，
   糊上加糊。

**修法**（三处，对应上面两张截图里的三层效果）：

- **`tools/make_icon.py` 重写**：改成从现成的 `assets/icon.png` 生成 ico，并且**按尺寸
  分别出档** —— 缝窄于 0.9 物理像素的档，先 `MinFilter` 腐蚀 alpha（缝等宽变宽）再
  LANCZOS 缩；48 档以上缝本来够宽，原样不动。走 `append_images` 而**不是** `sizes=`，
  因为后者会让 Pillow 从原图重新缩一遍、把刚撑好的缝又缩回去。
  ⚠️ 这个脚本以前**渲染的是另一张图**（`crafting_table` 的方块纹理，没有 X 徽标），
  一跑就把图标换掉 —— 现在默认不碰 png，要重画得显式加 `--render`。
  缝宽是**量出来的**：只采「正好穿过两条竖缝」的那些行再取众数 —— 直接对所有透明段取
  中位数会量出偏小的假值（横缝行整行透明、圆角又贡献碎段，实测 8px 的缝量成 5px）。
- **`win32.apply_window_icon`**：按窗口 DPI `LoadImage`（让系统自己挑档）+ `WM_SETICON`
  设成**窗口**级图标（窗口级优先于类级），Windows 就会去用 28 档，不再放大 16 档。
  ⚠️ 窗口还没 map 时 `GetDpiForWindow` 会返回 0，得一路回退到 `GetDpiForSystem`，
  否则按 96 算出 16px，等于白设一遍。HICON 不 `DestroyIcon`（窗口在用，进程退出回收）。
- **`window.py` 的标题区图标**顺手一起修：`CTkImage` 内部只做一遍 `image.resize(size)`
  且**不指定重采样**，Pillow 对 RGBA 默认 BICUBIC（白缝最亮只有 226/255，LANCZOS 是
  255）。改成自己按**物理**像素先缩好（LANCZOS），再把 CTkImage 的目标尺寸给成逻辑值
  让它算出同一个物理值 —— 那一遍就退化成恒等缩放。

**效果**：`docs/icon_titlebar_compare.png` 是三层递进 —— ①改前（16 档被放大）→ ②只修 ico
（仍是 16 档，但 16 档本身清楚了）→ ③全修（28 档直接渲染）。实测窗口图标位图 **16 → 28**。

**验证**：冒烟 **92/92**；`check_option_click` / `check_borders`（19 控件）/
`check_ui_names` / `preview_ui_combo` / `preview_ui_icons` / `preview_ui_anim` 全过。
