#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
extract_block_registry.py —— 从 Minecraft 客户端 jar 提取**权威**方块注册表

为什么需要它：
    mcschematic 库**不做任何方块名校验**（实测结论），任何字符串都会被原样写进 .schem。
    而 Litematica 遇到游戏里不存在的方块名时会**静默替换成空气**、不报错——事后无法察觉。
    所以唯一可靠的防线，是拿游戏本体自己的注册表做前置校验。

数据来源：
    <minecraft>/libraries/net/minecraft/client/<ver>/client-<ver>-official.jar
    内的 assets/minecraft/blockstates/<block>.json
    - 文件名     = 方块 ID
    - variants  键 = 合法的 blockstate 属性组合（如 "facing=north,half=bottom"）
    - multipart 的 when 条件同样会被收集

用法：
    python extract_block_registry.py                     # 自动探测本机 1.21.1
    python extract_block_registry.py --jar <jar路径>
    python extract_block_registry.py --mc-dir <minecraft目录> --version 1.21.1
    python extract_block_registry.py --out <输出json>

输出：
    JSON 注册表 {mc_version, data_version, blocks:{id:{prop:[values]}}}，
    供 block_check.py / palette_*.py 加载做精确校验。
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import zipfile

# blockstate 里出现的属性值 -> 收集
SKIP_DIRS = ("assets/minecraft/blockstates/",)


def find_client_jar(mc_dir: str, version: str) -> str | None:
    """在标准启动器目录里定位官方客户端 jar（优先 -official）。"""
    pats = [
        os.path.join(mc_dir, "libraries", "net", "minecraft", "client", version, f"client-{version}-official.jar"),
        os.path.join(mc_dir, "libraries", "net", "minecraft", "client", version, f"client-{version}.jar"),
        os.path.join(mc_dir, "versions", version, f"{version}.jar"),
    ]
    for p in pats:
        if os.path.isfile(p):
            return p
    # 退化：模糊搜索
    hits = glob.glob(os.path.join(mc_dir, "libraries", "net", "minecraft", "client", version, "*.jar"))
    return hits[0] if hits else None


def default_mc_dir() -> str:
    appdata = os.environ.get("APPDATA", "")
    if appdata:
        p = os.path.join(appdata, ".minecraft")
        if os.path.isdir(p):
            return p
    return os.path.expanduser("~/.minecraft")


def parse_props_from_key(key: str) -> dict[str, set[str]]:
    """把 'facing=north,half=bottom' 解析成 {'facing':{'north'}, 'half':{'bottom'}}"""
    out: dict[str, set[str]] = {}
    for part in key.split(","):
        part = part.strip()
        if not part or "=" not in part:
            continue
        k, v = part.split("=", 1)
        out.setdefault(k.strip(), set()).add(v.strip())
    return out


def merge(dst: dict[str, set[str]], src: dict[str, set[str]]) -> None:
    for k, vs in src.items():
        dst.setdefault(k, set()).update(vs)


def collect_variants(node, acc: dict[str, set[str]]) -> None:
    """
    递归处理 variants / multipart 两种 blockstate 结构。

    variants 形式:  {"facing=north,half=bottom": {模型定义}}   ← 键是属性串，值是模型
    multipart 形式: {"when": {"north": "true"}, "apply": {}}   ← 值才是属性
    """
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "apply":
                continue  # apply 内部是模型引用，没有方块属性
            if k == "when":
                if isinstance(v, dict):
                    for pk, pv in v.items():
                        if pk in ("OR", "AND"):
                            items = pv if isinstance(pv, list) else [pv]
                            for it in items:
                                if isinstance(it, dict):
                                    for kk, vv in it.items():
                                        if isinstance(vv, str):
                                            acc.setdefault(kk, set()).add(vv)
                                        elif isinstance(vv, list):
                                            acc.setdefault(kk, set()).update(map(str, vv))
                        else:
                            if isinstance(pv, str):
                                acc.setdefault(pk, set()).add(pv)
                            elif isinstance(pv, list):
                                acc.setdefault(pk, set()).update(map(str, pv))
                continue
            if isinstance(v, dict):
                # variants 分支：键 k 就是 'facing=north,half=bottom,shape=straight'
                # （值是模型定义 {'model':...,'x':..}，不再往下递归）
                merge(acc, parse_props_from_key(k))
            elif isinstance(v, (list, dict)):
                collect_variants(v, acc)
    elif isinstance(node, list):
        for it in node:
            collect_variants(it, acc)


def _multipart_override(block: str) -> dict[str, list[str]] | None:
    """
    multipart 类方块的**完整值域**补全。

    原因：blockstates json 对 multipart 方块只描述"需要额外渲染模型"的状态，
    例如 cobblestone_wall 只出现 up=true，但游戏里 up=false 完全合法。
    直接用提取结果做校验会误报，故按 MC 状态规范补全已知形态。
    """
    b = block.removeprefix("minecraft:")
    SIX = ["true", "false"]
    if b.endswith("_wall"):
        return {"north": ["none", "low", "tall"], "east": ["none", "low", "tall"],
                "south": ["none", "low", "tall"], "west": ["none", "low", "tall"],
                "up": SIX, "waterlogged": SIX}
    if b.endswith("_fence"):
        return {"north": SIX, "east": SIX, "south": SIX, "west": SIX, "waterlogged": SIX}
    if b.endswith("_pane") or b == "iron_bars":
        return {"north": SIX, "east": SIX, "south": SIX, "west": SIX, "waterlogged": SIX}
    if b in ("vine", "glow_lichen", "sculk_vein"):
        return {"north": SIX, "east": SIX, "south": SIX, "west": SIX, "up": SIX, "down": SIX}
    return None


BOOL = ["false", "true"]

# vanilla 的 blockstates 只为"会产生不同模型"的状态建键，因此**不影响模型**的属性
# 会被整条截断（实测：oak_door 缺 powered、oak_trapdoor 缺 powered/waterlogged）。
# 这些属性在游戏里完全合法，这里按 MC 状态规范补回，避免校验器误报。
HIDDEN_PROPS_SUFFIX: dict[str, dict[str, list[str]]] = {
    "_door": {"powered": BOOL},
    "_trapdoor": {"powered": BOOL, "waterlogged": BOOL},
    "_fence_gate": {"in_wall": BOOL, "powered": BOOL},
    "_slab": {"waterlogged": BOOL},
    "_stairs": {"waterlogged": BOOL},
    "_button": {"powered": BOOL},
    "_pressure_plate": {"powered": BOOL},
    "_sign": {"waterlogged": BOOL},
    "_fence": {"waterlogged": BOOL},
    "_wall": {"waterlogged": BOOL},
    "_pane": {"waterlogged": BOOL},
    "_candle": {"lit": BOOL, "waterlogged": BOOL},
    "_candle_cake": {"lit": BOOL},
    # 树叶：blockstate 只有单条 "variants":{"":...}（距模型的 persistent/distance 不改变模型，
    # 被 vanilla 整条截断）。这三个属性 1.19+ 均存在，缺了会导致 校验器误报 + 粘贴后树叶衰减。
    "_leaves": {"persistent": BOOL, "distance": [str(i) for i in range(1, 8)], "waterlogged": BOOL},
}

HIDDEN_PROPS_EXACT: dict[str, dict[str, list[str]]] = {
    "iron_bars": {"waterlogged": BOOL},
    "hopper": {"enabled": BOOL},
    "lantern": {"hanging": BOOL, "waterlogged": BOOL},
    "soul_lantern": {"hanging": BOOL, "waterlogged": BOOL},
    "lever": {"powered": BOOL},
    "lightning_rod": {"powered": BOOL},
    "campfire": {"lit": BOOL, "signal_fire": BOOL, "waterlogged": BOOL},
    "soul_campfire": {"lit": BOOL, "signal_fire": BOOL, "waterlogged": BOOL},
    "chest": {"waterlogged": BOOL},
    "trapped_chest": {"waterlogged": BOOL},
    "ender_chest": {"waterlogged": BOOL},
    "lectern": {"has_book": BOOL, "powered": BOOL},
    "bell": {"powered": BOOL},
    "conduit": {"waterlogged": BOOL},
    "piston": {"extended": BOOL},
    "sticky_piston": {"extended": BOOL},
    "observer": {"powered": BOOL},
    "repeater": {"locked": BOOL, "powered": BOOL},
    "comparator": {"locked": BOOL, "powered": BOOL},
    "dispenser": {"triggered": BOOL},
    "dropper": {"triggered": BOOL},
    "note_block": {"powered": BOOL},
    "daylight_detector": {"inverted": BOOL, "powered": BOOL},
    "sculk_sensor": {"powered": BOOL},
    "calibrated_sculk_sensor": {"powered": BOOL},
    "tripwire_hook": {"powered": BOOL, "attached": BOOL},
    "tripwire": {"powered": BOOL, "attached": BOOL, "disarmed": BOOL},
    "ladder": {"waterlogged": BOOL},
    "chain": {"waterlogged": BOOL},
    "scaffolding": {"waterlogged": BOOL},
    "respawn_anchor": {"charges": ["0", "1", "2", "3", "4"]},
    "bee_nest": {"honey_level": ["0", "1", "2", "3", "4", "5"]},
    "beehive": {"honey_level": ["0", "1", "2", "3", "4", "5"]},
    "water_cauldron": {"level": ["1", "2", "3"]},
    "lava_cauldron": {"level": ["1", "2", "3"]},
    "powder_snow_cauldron": {"level": ["1", "2", "3"]},
    "composter": {"level": [str(i) for i in range(9)]},
}


def _apply_hidden_props(block: str, props: dict[str, list[str]]) -> None:
    b = block.removeprefix("minecraft:")
    for suffix, extra in HIDDEN_PROPS_SUFFIX.items():
        if b.endswith(suffix):
            for k, vs in extra.items():
                props.setdefault(k, list(vs))
    if b in HIDDEN_PROPS_EXACT:
        for k, vs in HIDDEN_PROPS_EXACT[b].items():
            props.setdefault(k, list(vs))


def extract(jar_path: str, version: str, data_version: int | None) -> dict:
    blocks: dict[str, dict] = {}
    if not os.path.isfile(jar_path):
        raise FileNotFoundError(f"找不到客户端 jar: {jar_path}")

    n_variants = n_multipart = 0
    with zipfile.ZipFile(jar_path) as z:
        members = [n for n in z.namelist() if n.startswith(SKIP_DIRS[0]) and n.endswith(".json")]
        for m in members:
            block = os.path.basename(m)[: -len(".json")]
            try:
                data = json.loads(z.read(m).decode("utf-8"))
            except Exception:
                continue
            acc: dict[str, set[str]] = {}
            is_multipart = "multipart" in data
            if "variants" in data:
                collect_variants(data["variants"], acc)
                n_variants += 1
            if is_multipart:
                collect_variants(data["multipart"], acc)
                n_multipart += 1
            props = {k: sorted(v) for k, v in sorted(acc.items()) if v}

            # strict：variants 穷举了全部组合，值域可信；multipart 值域不完整
            strict = not is_multipart
            if is_multipart:
                ov = _multipart_override(block)
                if ov is not None:
                    props = {k: list(v) for k, v in ov.items()}
                    strict = True   # 已按状态规范补全，可严格校验

            # 补回被 vanilla 截断的隐藏属性
            _apply_hidden_props(block, props)

            # 布尔属性值域补全：MC 中布尔型 BlockStateProperty 的域恒为 true/false，
            # 若提取结果只剩单一值，说明被截断，补全。
            for k, vs in list(props.items()):
                if set(vs) <= {"true", "false"} and len(vs) < 2:
                    props[k] = ["false", "true"]

            props = {k: sorted(set(v)) for k, v in sorted(props.items()) if v}
            blocks[f"minecraft:{block}"] = {"strict": strict, "props": props}

    # 全局属性域：属性名 -> 全体合法值并集。
    # 用途：某方块若因 vanilla 截断而缺属性，可用全局域做"宽容校验"，避免误报。
    all_props: dict[str, set[str]] = {}
    for e in blocks.values():
        for k, vs in e["props"].items():
            all_props.setdefault(k, set()).update(vs)

    return {
        "mc_version": version,
        "data_version": data_version,
        "source_jar": os.path.basename(jar_path),
        "block_count": len(blocks),
        "variants_blocks": n_variants,
        "multipart_blocks": n_multipart,
        "all_props": {k: sorted(v) for k, v in sorted(all_props.items())},
        "blocks": blocks,
    }
def main() -> int:
    ap = argparse.ArgumentParser(description="从 Minecraft 客户端 jar 提取方块注册表")
    ap.add_argument("--jar", help="客户端 jar 路径（省略则自动探测）")
    ap.add_argument("--mc-dir", default=default_mc_dir(), help=".minecraft 目录")
    ap.add_argument("--version", default="1.21.1", help="Minecraft 版本")
    ap.add_argument("--data-version", type=int, default=None, help="DataVersion（可选，默认从 palette 常量读取）")
    ap.add_argument("--out", default=None, help="输出 JSON 路径")
    args = ap.parse_args()

    jar = args.jar or find_client_jar(args.mc_dir, args.version)
    if not jar:
        print(f"[FAIL] 未找到 {args.version} 的客户端 jar，请用 --jar 指定", file=sys.stderr)
        return 2

    dv = args.data_version
    if dv is None:
        try:
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            from palette_1_21_1 import DATA_VERSION  # type: ignore
            dv = DATA_VERSION
        except Exception:
            dv = None

    reg = extract(jar, args.version, dv)
    out = args.out or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "..", "references", f"block_registry_{args.version.replace('.', '_')}.json"
    )
    out = os.path.abspath(out)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(reg, f, ensure_ascii=False, indent=1, sort_keys=True)

    with_props = sum(1 for v in reg["blocks"].values() if v["props"])
    strict_n = sum(1 for v in reg["blocks"].values() if v["strict"] and v["props"])
    print(f"[OK] jar      : {jar}")
    print(f"[OK] mc版本   : {reg['mc_version']}  DataVersion={reg['data_version']}")
    print(f"[OK] 方块总数 : {reg['block_count']}")
    print(f"     带属性方块: {with_props}（可严格校验 {strict_n}）")
    print(f"     结构来源  : variants {reg['variants_blocks']} / multipart {reg['multipart_blocks']}")
    print(f"[OK] 已写入   : {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
