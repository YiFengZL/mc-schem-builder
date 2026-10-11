#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
palette_1_21_1.py —— MC Java 1.21.1 版本化方块表 + blockstate 校验

本文件是"版本对齐"的唯一真相源。换 MC 版本时，复制本文件改常量与常用集，
并用 extract_block_registry.py 重新生成注册表即可。

包含三部分：
  1) 版本常量（实测锁定，非记忆值）
  2) 建筑常用方块分类表（供模型选材，按风格分组）
  3) 校验 API（加载注册表 → 精确校验 blockstate 字符串 + 拼写建议）

实测依据（2026-09，本机 Python 3.13 + mcschematic）：
  - mcschematic.Version.JE_1_21_1 存在，导出 DataVersion = 3955
  - Sponge 格式 Version = 2
  - mcschematic 不做方块名校验，任何字符串照写 → 故必须有本文件兜底

自检：
  python palette_1_21_1.py --selfcheck
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import sys

# ----------------------------------------------------------------------------
# 1. 版本常量（实测锁定）
# ----------------------------------------------------------------------------
MC_VERSION = "1.21.1"
DATA_VERSION = 3955          # 实测：mcschematic.Version.JE_1_21_1 == 3955
SPONGE_VERSION = 2           # 实测：生成文件 Version = Int(2)
MCS_VERSION_ENUM = "JE_1_21_1"   # mcschematic.Version.JE_1_21_1
REGISTRY_FILENAME = "block_registry_1_21_1.json"

# ----------------------------------------------------------------------------
# 2. 建筑常用方块分类表
#    说明：带 * 的是"需显式写朝向/半砖位"的形态，见 builders 段。
# ----------------------------------------------------------------------------
PALETTE: dict[str, list[str]] = {
    # ---- 石材 / 砌体 ----
    "stone_basic": [
        "minecraft:stone", "minecraft:cobblestone", "minecraft:mossy_cobblestone",
        "minecraft:smooth_stone", "minecraft:stone_bricks", "minecraft:mossy_stone_bricks",
        "minecraft:cracked_stone_bricks", "minecraft:chiseled_stone_bricks",
        "minecraft:infested_stone_bricks",
    ],
    "stone_granite": ["minecraft:granite", "minecraft:polished_granite"],
    "stone_diorite": ["minecraft:diorite", "minecraft:polished_diorite"],
    "stone_andesite": ["minecraft:andesite", "minecraft:polished_andesite"],
    "deepslate": [
        "minecraft:deepslate", "minecraft:cobbled_deepslate", "minecraft:polished_deepslate",
        "minecraft:deepslate_bricks", "minecraft:cracked_deepslate_bricks",
        "minecraft:deepslate_tiles", "minecraft:cracked_deepslate_tiles",
        "minecraft:chiseled_deepslate",
    ],
    # 1.21 新增的 tuff 家族（1.21.1 完整可用，实测已确认）
    "tuff_121": [
        "minecraft:tuff", "minecraft:polished_tuff", "minecraft:tuff_bricks",
        "minecraft:chiseled_tuff", "minecraft:chiseled_tuff_bricks",
        "minecraft:tuff_slab", "minecraft:tuff_stairs", "minecraft:tuff_wall",
        "minecraft:tuff_brick_slab", "minecraft:tuff_brick_stairs", "minecraft:tuff_brick_wall",
        "minecraft:polished_tuff_slab", "minecraft:polished_tuff_stairs", "minecraft:polished_tuff_wall",
    ],
    "sandstone": [
        "minecraft:sandstone", "minecraft:chiseled_sandstone", "minecraft:cut_sandstone",
        "minecraft:smooth_sandstone", "minecraft:red_sandstone",
        "minecraft:chiseled_red_sandstone", "minecraft:cut_red_sandstone",
        "minecraft:smooth_red_sandstone",
    ],
    "brick_mud": [
        "minecraft:bricks", "minecraft:packed_mud", "minecraft:mud_bricks",
        "minecraft:mud_brick_slab", "minecraft:mud_brick_stairs", "minecraft:mud_brick_wall",
    ],
    "nether": [
        "minecraft:nether_bricks", "minecraft:cracked_nether_bricks", "minecraft:chiseled_nether_bricks",
        "minecraft:red_nether_bricks", "minecraft:netherrack", "minecraft:basalt",
        "minecraft:polished_basalt", "minecraft:smooth_basalt", "minecraft:blackstone",
        "minecraft:polished_blackstone", "minecraft:polished_blackstone_bricks",
        "minecraft:cracked_polished_blackstone_bricks", "minecraft:chiseled_polished_blackstone",
        "minecraft:gilded_blackstone", "minecraft:magma_block", "minecraft:glowstone",
    ],
    "quartz_prismarine": [
        "minecraft:quartz_block", "minecraft:chiseled_quartz_block", "minecraft:quartz_pillar",
        "minecraft:smooth_quartz", "minecraft:quartz_bricks",
        "minecraft:prismarine", "minecraft:prismarine_bricks", "minecraft:dark_prismarine",
        "minecraft:sea_lantern",
    ],
    "end_purpur": [
        "minecraft:end_stone", "minecraft:end_stone_bricks", "minecraft:purpur_block",
        "minecraft:purpur_pillar", "minecraft:obsidian", "minecraft:crying_obsidian",
    ],
    # ---- 1.21 新增铜家族 ----
    "copper_121": [
        "minecraft:copper_block", "minecraft:exposed_copper", "minecraft:weathered_copper",
        "minecraft:oxidized_copper", "minecraft:cut_copper", "minecraft:exposed_cut_copper",
        "minecraft:weathered_cut_copper", "minecraft:oxidized_cut_copper",
        "minecraft:chiseled_copper", "minecraft:exposed_chiseled_copper",
        "minecraft:weathered_chiseled_copper", "minecraft:oxidized_chiseled_copper",
        "minecraft:cut_copper_stairs", "minecraft:cut_copper_slab",
        "minecraft:copper_grate", "minecraft:exposed_copper_grate",
        "minecraft:weathered_copper_grate", "minecraft:oxidized_copper_grate",
        "minecraft:copper_bulb", "minecraft:copper_door", "minecraft:copper_trapdoor",
    ],
    # ---- 金属 / 宝石块 ----
    "metal_blocks": [
        "minecraft:iron_block", "minecraft:gold_block", "minecraft:diamond_block",
        "minecraft:emerald_block", "minecraft:netherite_block", "minecraft:lapis_block",
        "minecraft:redstone_block", "minecraft:coal_block", "minecraft:amethyst_block",
        "minecraft:raw_iron_block", "minecraft:raw_gold_block", "minecraft:raw_copper_block",
        "minecraft:iron_bars", "minecraft:chain",
    ],
    "clay_terracotta": [
        "minecraft:terracotta", "minecraft:white_terracotta", "minecraft:orange_terracotta",
        "minecraft:magenta_terracotta", "minecraft:light_blue_terracotta", "minecraft:yellow_terracotta",
        "minecraft:lime_terracotta", "minecraft:pink_terracotta", "minecraft:gray_terracotta",
        "minecraft:light_gray_terracotta", "minecraft:cyan_terracotta", "minecraft:purple_terracotta",
        "minecraft:blue_terracotta", "minecraft:brown_terracotta", "minecraft:green_terracotta",
        "minecraft:red_terracotta", "minecraft:black_terracotta",
        "minecraft:white_concrete", "minecraft:gray_concrete", "minecraft:black_concrete",
        "minecraft:white_concrete_powder", "minecraft:gray_concrete_powder",
    ],
    "glass": [
        "minecraft:glass", "minecraft:glass_pane", "minecraft:tinted_glass",
        "minecraft:white_stained_glass", "minecraft:light_gray_stained_glass",
        "minecraft:gray_stained_glass", "minecraft:black_stained_glass",
        "minecraft:cyan_stained_glass", "minecraft:blue_stained_glass",
        "minecraft:white_stained_glass_pane", "minecraft:gray_stained_glass_pane",
        "minecraft:black_stained_glass_pane", "minecraft:iron_bars",
    ],
    "wool_carpet": [
        "minecraft:white_wool", "minecraft:light_gray_wool", "minecraft:gray_wool",
        "minecraft:black_wool", "minecraft:red_wool", "minecraft:brown_wool",
        "minecraft:white_carpet", "minecraft:red_carpet", "minecraft:gray_carpet",
        "minecraft:black_carpet",
    ],
    # ---- 木材（1.21.1 树种全集，无 pale_oak —— 那是 1.21.4 才加的）----
    "wood_species": [
        "oak", "spruce", "birch", "jungle", "acacia", "dark_oak",
        "mangrove", "cherry", "bamboo", "crimson", "warped",
    ],
    # ---- 照明 ----
    "lighting": [
        "minecraft:torch", "minecraft:wall_torch", "minecraft:soul_torch",
        "minecraft:lantern", "minecraft:soul_lantern", "minecraft:campfire",
        "minecraft:soul_campfire", "minecraft:glowstone", "minecraft:shroomlight",
        "minecraft:sea_lantern", "minecraft:redstone_lamp", "minecraft:end_rod",
        "minecraft:lightning_rod", "minecraft:ochre_froglight",
        "minecraft:verdant_froglight", "minecraft:pearlescent_froglight",
        "minecraft:candle", "minecraft:white_candle", "minecraft:copper_bulb",
    ],
    # ---- 植被 / 自然 ----
    "nature": [
        "minecraft:grass_block", "minecraft:dirt", "minecraft:coarse_dirt",
        "minecraft:podzol", "minecraft:mycelium", "minecraft:farmland",
        "minecraft:gravel", "minecraft:sand", "minecraft:red_sand", "minecraft:clay",
        "minecraft:moss_block", "minecraft:moss_carpet", "minecraft:vine",
        "minecraft:lily_pad", "minecraft:azalea_leaves", "minecraft:flowering_azalea_leaves",
        "minecraft:glow_lichen", "minecraft:sculk", "minecraft:snow_block",
        "minecraft:powder_snow", "minecraft:ice", "minecraft:packed_ice", "minecraft:blue_ice",
    ],
    # ---- 功能 / 家具 ----
    "functional": [
        "minecraft:crafting_table", "minecraft:bookshelf", "minecraft:chiseled_bookshelf",
        "minecraft:lectern", "minecraft:cartography_table", "minecraft:fletching_table",
        "minecraft:smithing_table", "minecraft:loom", "minecraft:stonecutter",
        "minecraft:grindstone", "minecraft:barrel", "minecraft:chest",
        "minecraft:trapped_chest", "minecraft:ender_chest", "minecraft:furnace",
        "minecraft:blast_furnace", "minecraft:smoker", "minecraft:anvil",
        "minecraft:cauldron", "minecraft:beacon", "minecraft:conduit", "minecraft:bell",
        # 注意：armor_stand / item_frame / painting 是**实体**不是方块，不可写入 .schem
        "minecraft:flower_pot", "minecraft:ladder",
        "minecraft:scaffolding", "minecraft:hay_block", "minecraft:target",
        "minecraft:note_block", "minecraft:jukebox", "minecraft:composter",
        # 1.21 新增
        "minecraft:crafter", "minecraft:trial_spawner", "minecraft:vault", "minecraft:heavy_core",
    ],
    "banners": [
        "minecraft:white_banner", "minecraft:red_banner", "minecraft:blue_banner",
        "minecraft:black_banner", "minecraft:gray_banner", "minecraft:yellow_banner",
        "minecraft:white_wall_banner", "minecraft:red_wall_banner",
    ],
}

# PALETTE 中不是方块 ID、而是"前缀/树种"的类别，校验时跳过
NON_BLOCK_CATEGORIES = {"wood_species"}

# 常被误当成方块的**实体**——.schem 只能存方块，写这些会被静默替换成空气
ENTITY_HINTS: dict[str, str] = {
    "armor_stand": "盔甲架是实体（entity）",
    "item_frame": "物品展示框是实体",
    "glow_item_frame": "发光物品展示框是实体",
    "painting": "画是实体",
    "leash_knot": "拴绳结是实体",
    "end_crystal": "末地水晶是实体",
    "lightning_bolt": "闪电是实体",
    "interaction": "交互实体（无碰撞箱，仅用于数据包）",
    "marker": "标记实体",
    "item_display": "展示实体（item_display）",
    "block_display": "展示实体（block_display）",
    "text_display": "展示实体（text_display）",
    "ominous_item_spawner": "不祥物品生成器是实体",
    "experience_orb": "经验球是实体",
    "falling_block": "下落实体",
    "tnt": "原版 tnt 是方块；被误报时请确认拼写",
    "boat": "船是实体",
    "minecart": "矿车是实体",
}

# vanilla blockstates 会整条截断的"方向无关"属性 —— 这些允许宽容放行。
# 不在此列的属性（facing / half / type / shape / axis / rotation ...）在 vanilla 里
# 是穷举的，不属于截断，写了就必须严格校验。
TOLERANT_PROPS = {
    "waterlogged", "powered", "open", "lit", "in_wall", "hanging", "signal_fire",
    "has_book", "triggered", "locked", "extended", "inverted", "attached",
    "disarmed", "enabled", "snowy", "conditional", "up", "down", "occupied",
}

# 需要显式朝向的形态（不写就会全朝默认方向，是常见翻车点）
ORIENTED_FORMS = {
    "stairs": ["facing", "half", "shape"],
    "slab": ["type"],
    "wall": ["north", "east", "south", "west", "up"],
    "door": ["facing", "half", "hinge", "open", "powered"],
    "trapdoor": ["facing", "half", "open", "powered", "waterlogged"],
    "fence_gate": ["facing", "open", "powered", "in_wall"],
    "pane": ["waterlogged"],
    "button": ["face", "facing", "powered"],
    "pressure_plate": ["powered"],
}

# ----------------------------------------------------------------------------
# 3. 便利构造器（避免手写属性字符串时拼错）
# ----------------------------------------------------------------------------
_STAIR_DIRS = ("north", "south", "east", "west")
_SLAB_TYPES = ("top", "bottom", "double")
_WALL_SHAPES = ("none", "low", "tall")


def stairs(block: str, facing: str = "north", half: str = "bottom", shape: str | None = None) -> str:
    """楼梯：stairs('minecraft:stone_bricks', 'north', 'bottom')"""
    if facing not in _STAIR_DIRS:
        raise ValueError(f"facing 必须是 {_STAIR_DIRS} 之一，收到 {facing!r}")
    if half not in ("top", "bottom"):
        raise ValueError(f"half 必须是 top/bottom，收到 {half!r}")
    s = f"{block}[facing={facing},half={half}"
    if shape:
        s += f",shape={shape}"
    return s + "]"


def slab(block: str, type_: str = "bottom") -> str:
    """台阶：slab('minecraft:stone_bricks', 'top')"""
    if type_ not in _SLAB_TYPES:
        raise ValueError(f"type 必须是 {_SLAB_TYPES} 之一，收到 {type_!r}")
    return f"{block}[type={type_}]"


def wall(block: str, north="none", east="none", south="none", west="none", up="true") -> str:
    """墙：wall('minecraft:cobblestone', north='low')"""
    for name, v in (("north", north), ("east", east), ("south", south), ("west", west)):
        if v not in _WALL_SHAPES:
            raise ValueError(f"{name} 必须是 {_WALL_SHAPES} 之一，收到 {v!r}")
    up = str(up).lower()
    if up not in ("true", "false"):
        raise ValueError("up 必须是 true/false")
    return f"{block}[north={north},east={east},south={south},west={west},up={up}]"


def door(block: str, facing="north", half="lower", hinge="left", open_="false", powered="false") -> str:
    if facing not in _STAIR_DIRS:
        raise ValueError(f"facing 必须是 {_STAIR_DIRS} 之一")
    if half not in ("upper", "lower"):
        raise ValueError("half 必须是 upper/lower")
    if hinge not in ("left", "right"):
        raise ValueError("hinge 必须是 left/right")
    return (f"{block}[facing={facing},half={half},hinge={hinge},"
            f"open={str(open_).lower()},powered={str(powered).lower()}]")


def trapdoor(block: str, facing="north", half="top", open_="false", powered="false", waterlogged="false") -> str:
    if facing not in _STAIR_DIRS:
        raise ValueError(f"facing 必须是 {_STAIR_DIRS} 之一")
    if half not in ("top", "bottom"):
        raise ValueError("half 必须是 top/bottom")
    return (f"{block}[facing={facing},half={half},open={str(open_).lower()},"
            f"powered={str(powered).lower()},waterlogged={str(waterlogged).lower()}]")


def planks(species: str) -> str:
    return f"minecraft:{species}_planks"


def log(species: str, stripped: bool = False, axis: str = "y") -> str:
    base = f"minecraft:{'stripped_' if stripped else ''}{species}_log"
    return f"{base}[axis={axis}]" if axis else base


# ----------------------------------------------------------------------------
# 3b. 连接型 / 装饰型构造器（B5）
#     这些方块的属性在 vanilla blockstate 里被截断，手写极易漏项；
#     漏了不会报错，但游戏里会表现为「栅栏不连、门不认向」。
# ----------------------------------------------------------------------------
def _b(v) -> str:
    return "true" if v else "false"


def fence(block: str, north=False, east=False, south=False, west=False,
          waterlogged=False) -> str:
    """栅栏：四个方向表示是否与相邻方块相连（fence('minecraft:oak_fence', east=True)）。"""
    return (f"{block}[north={_b(north)},east={_b(east)},"
            f"south={_b(south)},west={_b(west)},waterlogged={_b(waterlogged)}]")


def fence_gate(block: str, facing="north", open_=False, in_wall=False, powered=False) -> str:
    """栅栏门：fence_gate('minecraft:oak_fence_gate', facing='east')"""
    if facing not in _STAIR_DIRS:
        raise ValueError(f"facing 必须是 {_STAIR_DIRS} 之一，收到 {facing!r}")
    return f"{block}[facing={facing},in_wall={_b(in_wall)},open={_b(open_)},powered={_b(powered)}]"


def pane(block: str, north=False, east=False, south=False, west=False,
         waterlogged=False) -> str:
    """玻璃板 / 铁栏杆（属性与栅栏同形）：pane('minecraft:glass_pane', east=True, west=True)"""
    return fence(block, north, east, south, west, waterlogged)


def button(block: str, face="wall", facing="north", powered=False) -> str:
    """按钮：face=floor/wall/ceiling（wall 时 facing 才有意义）。"""
    if face not in ("floor", "wall", "ceiling"):
        raise ValueError("face 必须是 floor/wall/ceiling")
    if facing not in _STAIR_DIRS:
        raise ValueError(f"facing 必须是 {_STAIR_DIRS} 之一，收到 {facing!r}")
    return f"{block}[face={face},facing={facing},powered={_b(powered)}]"


def pressure_plate(block: str, powered=False) -> str:
    return f"{block}[powered={_b(powered)}]"


def ladder(block: str = "minecraft:ladder", facing="north", waterlogged=False) -> str:
    """梯子：facing 表示梯子**贴附面**在哪个方向（与楼梯 facing 语义不同）。"""
    if facing not in _STAIR_DIRS:
        raise ValueError(f"facing 必须是 {_STAIR_DIRS} 之一，收到 {facing!r}")
    return f"{block}[facing={facing},waterlogged={_b(waterlogged)}]"


def wall_torch(block: str = "minecraft:wall_torch", facing="north") -> str:
    """墙上的火把：独立方块 id（minecraft:wall_torch），不是 torch[...]。"""
    if facing not in _STAIR_DIRS:
        raise ValueError(f"facing 必须是 {_STAIR_DIRS} 之一，收到 {facing!r}")
    return f"{block}[facing={facing}]"


def leaves(species: str, persistent: bool = True, distance: int = 1,
           waterlogged: bool = False) -> str:
    """
    树叶：**默认 persistent=true**。

    原因：非 persistent（默认 false）的树叶在粘贴后会按 distance 自然衰减消失，
    投影里看着满树，进游戏一会儿就秃了。作图请一律用 persistent=true。
    """
    d = int(distance)
    if not (1 <= d <= 7):
        raise ValueError("distance 必须在 1..7（距最近原木的距离）")
    return (f"minecraft:{species}_leaves[distance={d},"
            f"persistent={_b(persistent)},waterlogged={_b(waterlogged)}]")


# ----------------------------------------------------------------------------
# 4. 注册表加载与校验
# ----------------------------------------------------------------------------
_REGISTRY: dict | None = None


def registry_path() -> str:
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.abspath(os.path.join(here, "..", "references", REGISTRY_FILENAME))


def load_registry(path: str | None = None, required: bool = False) -> dict:
    """加载方块注册表。缺失时返回空表（校验退化为'仅基础格式检查'）。"""
    global _REGISTRY
    if _REGISTRY is not None and path is None:
        return _REGISTRY
    p = path or registry_path()
    if not os.path.isfile(p):
        if required:
            raise FileNotFoundError(
                f"未找到方块注册表 {p}\n"
                f"请先运行：python extract_block_registry.py --version {MC_VERSION}"
            )
        _REGISTRY = {}
        return _REGISTRY
    with open(p, encoding="utf-8") as f:
        _REGISTRY = json.load(f)
    return _REGISTRY


def split_blockstate(s: str) -> tuple[str, dict[str, str]]:
    """'minecraft:oak_stairs[facing=north,half=bottom]' -> ('minecraft:oak_stairs', {...})"""
    s = s.strip()
    if s.endswith("]") and "[" in s:
        base, _, props = s.partition("[")
        props = props[:-1]
        d: dict[str, str] = {}
        for part in props.split(","):
            part = part.strip()
            if not part:
                continue
            k, _, v = part.partition("=")
            d[k.strip()] = v.strip()
        return base, d
    return s, {}


def all_block_ids() -> list[str]:
    reg = load_registry()
    return sorted(reg.get("blocks", {}).keys())


def suggest_block(name: str, cutoff: float = 0.72) -> str | None:
    """对拼错的方块名给出最接近的合法候选。"""
    base, _ = split_blockstate(name)
    ids = all_block_ids()
    if not ids:
        return None
    pool = ids + [i.replace("minecraft:", "") for i in ids]
    m = difflib.get_close_matches(base, pool, n=1, cutoff=cutoff)
    if not m:
        return None
    cand = m[0] if m[0].startswith("minecraft:") else f"minecraft:{m[0]}"
    return cand


def validate_blockstate(name: str, strict_props: bool = True) -> tuple[bool, str]:
    """
    校验一个完整 blockstate 字符串。
    返回 (是否合法, 说明)。注册表缺失时只做基础格式检查并明确标注。
    """
    if not isinstance(name, str) or not name.strip():
        return False, "方块名为空"
    raw = name.strip()
    if "<" in raw or ">" in raw:
        return False, f"含尖括号标签（禁止写法）：{raw}"
    if raw != raw.lower():
        return False, f"方块名必须全小写：{raw}"
    base, props = split_blockstate(raw)
    if ":" not in base:
        return False, f"缺少命名空间前缀 'minecraft:'：{base}"

    reg = load_registry()
    blocks = reg.get("blocks")
    if not blocks:
        return True, "注册表缺失，仅通过基础格式检查（建议先运行 extract_block_registry.py）"

    if base not in blocks:
        b = base.removeprefix("minecraft:")
        if b in ENTITY_HINTS:
            return False, f"{b} 不是方块：{ENTITY_HINTS[b]}——建筑中请用方块替代（如萤石/灯笼），或用 Litematica 之外的实体方案"
        sug = suggest_block(base)
        extra = f"，是否想写 {sug}？" if sug else ""
        return False, f"目标版本 {MC_VERSION} 中不存在方块 {base}{extra}"

    if not props:
        return True, "OK"

    entry = blocks[base]
    allowed = entry.get("props", {}) if isinstance(entry, dict) else entry
    strict = entry.get("strict", True) if isinstance(entry, dict) else True
    global_props = reg.get("all_props", {})

    for k, v in props.items():
        if k not in allowed:
            # vanilla 会整条截断"不影响模型"的属性（见 extract 的 HIDDEN_PROPS）。
            # 仅对**方向无关的通用属性**做宽容放行；facing/half/type/shape 这类
            # 方向属性在 vanilla 里是穷举的，不属于截断情形，写了就是错，必须拦。
            if k in TOLERANT_PROPS and k in global_props:
                if v not in global_props[k]:
                    return False, f"属性 {k} 取值非法 {v!r}；合法值：{global_props[k]}"
                continue
            keys = ", ".join(sorted(allowed)) or "（该方块无属性）"
            return False, f"{base} 不支持属性 {k}；可用属性：{keys}"
        if strict and v not in allowed[k]:
            return False, f"{base} 的属性 {k} 取值非法 {v!r}；合法值：{allowed[k]}"
    if not strict:
        return True, f"OK（{base} 为连接型方块，属性值域未严格校验）"
    return True, "OK"


def palette_by_category() -> dict[str, list[str]]:
    return {k: list(v) for k, v in PALETTE.items()}


def wood_variants(species: str) -> dict[str, str]:
    """返回某树种的常用变体 ID 字典。"""
    p = f"minecraft:{species}"
    return {
        "log": f"{p}_log", "stripped_log": f"{p}_stripped_log",
        "wood": f"{p}_wood", "stripped_wood": f"{p}_stripped_wood",
        "planks": f"{p}_planks", "slab": f"{p}_slab", "stairs": f"{p}_stairs",
        "fence": f"{p}_fence", "fence_gate": f"{p}_fence_gate",
        "door": f"{p}_door", "trapdoor": f"{p}_trapdoor",
        "sign": f"{p}_sign", "button": f"{p}_button",
        "pressure_plate": f"{p}_pressure_plate", "leaves": f"{p}_leaves",
    }


# ----------------------------------------------------------------------------
# 自检
# ----------------------------------------------------------------------------
def _selfcheck() -> int:
    print(f"MC 版本      : {MC_VERSION}")
    print(f"DataVersion  : {DATA_VERSION}")
    print(f"Sponge 版本  : {SPONGE_VERSION}")
    print(f"mcschematic  : Version.{MCS_VERSION_ENUM}")
    reg = load_registry()
    if not reg:
        print(f"[WARN] 未找到注册表 {registry_path()}")
        print("       请运行：python extract_block_registry.py")
        return 1
    print(f"注册表       : {reg.get('block_count')} 个方块（源 {reg.get('source_jar')}）")

    bad: list[tuple[str, str]] = []
    total = 0
    for cat, items in PALETTE.items():
        if cat in NON_BLOCK_CATEGORIES:
            print(f"   (跳过前缀集类别 {cat}：{len(items)} 项，供 wood_variants() 使用)")
            continue
        for b in items:
            total += 1
            ok, msg = validate_blockstate(b)
            if not ok:
                bad.append((f"{cat}:{b}", msg))
    print(f"常用集校验   : {total} 条，非法 {len(bad)} 条")
    for name, msg in bad:
        print(f"   [X] {name} -> {msg}")

    # 朝向构造器自检（用真正的对应形态方块）
    probes = [
        stairs("minecraft:stone_brick_stairs", "north", "bottom"),
        slab("minecraft:stone_brick_slab", "top"),
        wall("minecraft:cobblestone_wall", north="low"),
        trapdoor("minecraft:oak_trapdoor"),
        door("minecraft:oak_door", half="lower"),
        # B5 连接型 / 装饰型构造器
        fence("minecraft:oak_fence", east=True, west=True),
        fence_gate("minecraft:oak_fence_gate", facing="east"),
        pane("minecraft:glass_pane", east=True, west=True),
        pane("minecraft:iron_bars", north=True),
        button("minecraft:oak_button", face="wall", facing="north"),
        pressure_plate("minecraft:oak_pressure_plate"),
        ladder(),
        wall_torch(facing="north"),
        leaves("oak"),
    ]
    probe_fail = 0
    for p in probes:
        ok, msg = validate_blockstate(p)
        if not ok:
            probe_fail += 1
        print(f"   构造器 {p} -> {'OK' if ok else '[X] ' + msg}")

    # 反例自检：这些必须被拦下
    negatives = [
        "minecraft:stone_brick",              # 拼写错（应为 stone_bricks）
        "minecraft:pale_oak_planks",          # 1.21.4 才有，1.21.1 不存在
        "minecraft:oak_stairs[facing=up]",    # facing 取值非法
        "minecraft:stone_bricks[facing=north]",  # 该方块无属性
        "<gradient>oak</gradient>",           # 尖括号标签
        "minecraft:oak_leaves[persistent=yes]",  # B5：布尔值写错
        "minecraft:oak_leaves[distance=9]",      # B5：distance 超出 1..7
        "minecraft:oak_fence[north=maybe]",      # B5：连接属性取值非法
    ]
    neg_pass = 0
    for n in negatives:
        ok, _ = validate_blockstate(n)
        if not ok:
            neg_pass += 1
    print(f"反例拦截     : {neg_pass}/{len(negatives)} 被正确拦下")

    return 0 if (not bad and probe_fail == 0 and neg_pass == len(negatives)) else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="1.21.1 调色板与 blockstate 校验")
    ap.add_argument("--selfcheck", action="store_true", help="校验常用集与构造器")
    ap.add_argument("--check", metavar="BLOCKSTATE", help="校验单个 blockstate")
    ap.add_argument("--list", action="store_true", help="列出全部类别")
    args = ap.parse_args()
    if args.check:
        ok, msg = validate_blockstate(args.check)
        print(f"{'OK' if ok else 'FAIL'}: {msg}")
        return 0 if ok else 1
    if args.list:
        for cat, items in PALETTE.items():
            print(f"[{cat}] ({len(items)})")
            print("   " + ", ".join(items))
        return 0
    return _selfcheck()


if __name__ == "__main__":
    raise SystemExit(main())
