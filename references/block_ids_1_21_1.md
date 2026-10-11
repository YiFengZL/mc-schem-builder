# MC 1.21.1 建筑方块速查

> 数据来源：从本机 `client-1.21.1-official.jar` 的 `assets/minecraft/blockstates/` 提取，
> 共 **1062 个方块**（其中 544 个带 blockstate 属性）。
> 完整注册表见 `block_registry_1_21_1.json`；本文件是给人看的常用集。

代码中可用的分类常量：`from palette_1_21_1 import PALETTE`（21 个类别，246 条常用方块，已全部校验通过）。

---

## 1. 带朝向的方块：必须显式给属性

这是最常见的翻车点 —— **不写属性会全部朝向默认方向**，屋顶/楼梯会很难看。

用构造器（`from palette_1_21_1 import stairs, slab, wall, door, trapdoor`）：

| 形态 | 构造器 | 合法属性值 |
|---|---|---|
| 楼梯 | `stairs(base, facing, half, shape=None)` | `facing`: north/south/east/west；`half`: top/bottom；`shape`: straight/inner_left/inner_right/outer_left/outer_right |
| 台阶 | `slab(base, type_)` | `type`: top/bottom/double |
| 墙 | `wall(base, north, east, south, west, up)` | 四向: none/low/tall；`up`: true/false |
| 门 | `door(base, facing, half, hinge, open_, powered)` | `half`: upper/lower；`hinge`: left/right |
| 活板门 | `trapdoor(base, facing, half, open_, powered, waterlogged)` | `half`: top/bottom |

示例：

```python
stairs("minecraft:stone_brick_stairs", facing="north", half="bottom")
slab("minecraft:stone_brick_slab", "top")
wall("minecraft:cobblestone_wall", north="low", up="true")
door("minecraft:oak_door", facing="north", half="lower", hinge="left")
```

> `shape`（楼梯的转角形态）通常**不用手写** —— 放置后游戏会自动计算。

---

## 2. 按风格分组的常用材质（`PALETTE` 的类别）

| 类别 | 内容 |
|---|---|
| `stone_basic` | stone / cobblestone / mossy_cobblestone / smooth_stone / stone_bricks / mossy_stone_bricks / cracked_stone_bricks / chiseled_stone_bricks |
| `stone_granite` / `stone_diorite` / `stone_andesite` | 花岗岩 / 闪长岩 / 安山岩及其抛光版 |
| `deepslate` | deepslate / cobbled_deepslate / polished_deepslate / deepslate_bricks / deepslate_tiles / chiseled_deepslate |
| **`tuff_121`** | **1.21 新增**：polished_tuff / tuff_bricks / chiseled_tuff / chiseled_tuff_bricks + 各自 slab/stairs/wall |
| `sandstone` | 砂岩 / 红砂岩及其切制、平滑、雕纹版 |
| `brick_mud` | bricks / packed_mud / mud_bricks 系列 |
| `nether` | nether_bricks / red_nether_bricks / blackstone 系列 / basalt / gilded_blackstone / magma_block |
| `quartz_prismarine` | 石英系列 / 海晶石系列 / sea_lantern |
| `end_purpur` | end_stone / end_stone_bricks / purpur / obsidian / crying_obsidian |
| **`copper_121`** | **1.21 新增**：chiseled_copper / copper_grate / copper_bulb / copper_door / copper_trapdoor 及四阶段氧化态 |
| `metal_blocks` | iron/gold/diamond/emerald/netherite/lapis/redstone/coal/amethyst 块 + iron_bars + chain |
| `clay_terracotta` | 17 色陶瓦 + 混凝土 + 混凝土粉末 |
| `glass` | glass / glass_pane / tinted_glass + 染色玻璃与板 |
| `wool_carpet` | 羊毛与地毯 |
| `lighting` | torch / lantern / soul_lantern / campfire / glowstone / shroomlight / sea_lantern / redstone_lamp / end_rod / lightning_rod / 蛙明灯 / candle / copper_bulb |
| `nature` | grass_block / dirt / podzol / mycelium / gravel / sand / clay / moss_block / vine / lily_pad / 杜鹃叶 / snow / ice 系列 |
| `functional` | crafting_table / bookshelf / chiseled_bookshelf / lectern / 各类工作台 / barrel / chest / furnace / anvil / beacon / conduit / bell / flower_pot / ladder / scaffolding / hay_block / note_block / jukebox / composter + **1.21 新增 crafter / trial_spawner / vault / heavy_core** |
| `banners` | 各色旗帜与墙旗 |

### 树种全集（`PALETTE["wood_species"]`，配合 `wood_variants(species)` 使用）

```
oak, spruce, birch, jungle, acacia, dark_oak, mangrove, cherry, bamboo, crimson, warped
```

`wood_variants("oak")` 返回该树种的全套变体（log / stripped_log / wood / planks / slab /
stairs / fence / fence_gate / door / trapdoor / sign / button / pressure_plate / leaves）。

> ⚠️ **1.21.1 没有 `pale_oak`**（苍白橡木）—— 那是 1.21.4 才加入的。
> ⚠️ `crimson` / `warped` 是下界木，没有 `leaves`（是菌光体等）。
> ⚠️ `bamboo` 没有常规的 log/wood，是 `bamboo_block` / `stripped_bamboo_block`。

---

## 3. 1.21.1 中**不存在**的方块（写了会静默变空气）

| 方块 | 加入版本 |
|---|---|
| `pale_oak_*`（木、木板、楼梯…） | 1.21.4 |
| `resin_*`（树脂砖、树脂块…） | 1.21.4 |
| `copper_chest`、铜傀儡相关 | 更晚版本 |

其余易误用的**实体**（不是方块）：`armor_stand`、`painting`、`item_frame`、`glow_item_frame`、
`item_display` / `block_display` / `text_display`、`interaction`、`marker`、`end_crystal`。

---

## 4. 自查命令

```bash
# 校验任意文件里的方块引用
python scripts/block_check.py spec.json
python scripts/block_check.py build_x.py

# 校验单个 blockstate
python scripts/palette_1_21_1.py --check "minecraft:oak_stairs[facing=north,half=bottom]"

# 列出某个类别
python scripts/palette_1_21_1.py --list

# 校验全部常用集 + 构造器 + 反例
python scripts/block_check.py --selfcheck
```

校验器会给出拼写建议，例如：

```
minecraft:stone_brick  → 目标版本 1.21.1 中不存在方块 minecraft:stone_brick，
                         是否想写 minecraft:stone_bricks？
minecraft:armor_stand  → armor_stand 不是方块：盔甲架是实体（entity）
```
