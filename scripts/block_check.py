#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
block_check.py —— 生成前的方块名白名单校验（失败前置）

为什么必须在"生成前"做：
    1) mcschematic 不做任何校验，任何字符串都会被写进 .schem
    2) Litematica 遇到游戏里不存在的方块名时**静默替换成空气、不报错**
    两条叠加 = 错了也看不出来。所以必须在生成前拦。

可校验对象：
    .json（spec / 风格预设）  .py（build 脚本）  纯文本（.txt/.md）
    也支持直接传 blockstate 字符串

用法：
    python block_check.py spec.json
    python block_check.py build_castle.py
    python block_check.py --text "minecraft:oak_stairs[facing=north,half=bottom]"
    python block_check.py --selfcheck

退出码：0 = 全部合法；1 = 存在非法项（生成流程应据此中止）
"""

from __future__ import annotations

import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from palette_1_21_1 import (  # noqa: E402
    MC_VERSION,
    load_registry,
    suggest_block,
    split_blockstate,
    validate_blockstate,
)

# 匹配 minecraft:xxx 或 minecraft:xxx[prop=val,...]
BLOCK_RE = re.compile(r"minecraft:[a-z0-9_]+(?:\[[^\]\n]*\])?")

SKIP_SUFFIXES = (".schem", ".schematic", ".nbt", ".png", ".zip", ".jar")


def scan_text(text: str) -> list[tuple[int, str]]:
    """返回 [(行号, blockstate 字符串), ...]，按出现顺序去重。"""
    out: list[tuple[int, str]] = []
    seen: set[str] = set()
    for lineno, line in enumerate(text.splitlines(), 1):
        for m in BLOCK_RE.finditer(line):
            s = m.group(0)
            if s in seen:
                continue
            seen.add(s)
            out.append((lineno, s))
    return out


def check_blocks(items: list[tuple[int, str]]) -> list[tuple[int, str, str]]:
    """返回 [(行号, blockstate, 错误说明), ...]"""
    bad = []
    for lineno, s in items:
        ok, msg = validate_blockstate(s)
        if not ok:
            bad.append((lineno, s, msg))
    return bad


def check_file(path: str) -> tuple[int, list[tuple[int, str, str]]]:
    if not os.path.isfile(path):
        raise FileNotFoundError(path)
    with open(path, encoding="utf-8", errors="replace") as f:
        text = f.read()
    items = scan_text(text)
    return len(items), check_blocks(items)


def _selfcheck() -> int:
    reg = load_registry()
    if not reg:
        print("[FAIL] 未找到方块注册表，请先运行 extract_block_registry.py")
        return 1
    print(f"注册表: {reg.get('block_count')} 个方块（{MC_VERSION}）")

    positives = [
        "minecraft:stone_bricks",
        "minecraft:oak_stairs[facing=north,half=bottom]",
        "minecraft:tuff_brick_wall[north=low,east=none,south=none,west=none,up=true]",
        "minecraft:copper_bulb[lit=true,powered=false]",
        "minecraft:crafter[crafting=true,orientation=north_up,triggered=false]",
        "minecraft:oak_door[facing=north,half=lower,hinge=left,open=false,powered=false]",
        "minecraft:stone_brick_slab[type=top,waterlogged=false]",
        "minecraft:heavy_core",
        "minecraft:trial_spawner",
        "minecraft:vault",
    ]
    negatives = [
        "minecraft:stone_brick",                   # 拼写漏了 s
        "minecraft:pale_oak_planks",               # 1.21.4 才加入
        "minecraft:resin_bricks",                  # 1.21.4 才加入
        "minecraft:oak_stairs[facing=up]",         # facing 取值非法
        "minecraft:stone_bricks[facing=north]",    # 该方块无属性
        "minecraft:armor_stand",                   # 实体，不是方块
        "minecraft:painting",                      # 实体，不是方块
        "<gradient:black:gray>stone</gradient>",   # 尖括号标签
        "Stone_Bricks",                            # 大小写错误
    ]

    print("\n--- 正例（应全部通过）---")
    pfail = 0
    for p in positives:
        ok, msg = validate_blockstate(p)
        if not ok:
            pfail += 1
        print(f"  {'OK  ' if ok else '[X] '} {p}" + ("" if ok else f"   <- {msg}"))

    print("\n--- 反例（应全部拦下）---")
    npass = 0
    for n in negatives:
        ok, msg = validate_blockstate(n)
        if not ok:
            npass += 1
            sug = suggest_block(n)
            sug = f"  建议: {sug}" if sug else ""
            print(f"  拦下 OK   {n}{sug}")
        else:
            print(f"  [X] 漏放  {n}")

    print(f"\n正例通过 {len(positives) - pfail}/{len(positives)} | 反例拦截 {npass}/{len(negatives)}")
    return 0 if (pfail == 0 and npass == len(negatives)) else 1


def main() -> int:
    ap = argparse.ArgumentParser(description="生成前方块名白名单校验")
    ap.add_argument("paths", nargs="*", help="要校验的文件（.json / .py / .txt）")
    ap.add_argument("--text", help="直接校验一段文本")
    ap.add_argument("--selfcheck", action="store_true", help="跑内置正反例自检")
    args = ap.parse_args()

    if args.selfcheck:
        return _selfcheck()

    targets: list[str] = []
    if args.text:
        targets.append(("--text", args.text))
    for p in args.paths:
        if p.lower().endswith(SKIP_SUFFIXES):
            print(f"[SKIP] {p}（二进制/产物文件）")
            continue
        targets.append((p, None))

    if not targets:
        ap.print_help()
        return 0

    total_bad = 0
    for name, text in targets:
        try:
            if text is None:
                n, bad = check_file(name)
            else:
                items = scan_text(text)
                n, bad = len(items), check_blocks(items)
        except FileNotFoundError:
            print(f"[FAIL] 文件不存在：{name}")
            total_bad += 1
            continue
        print(f"[{name}] 发现 {n} 个方块引用，非法 {len(bad)} 个")
        for lineno, s, msg in bad:
            print(f"    L{lineno}: {s}")
            print(f"          -> {msg}")
        total_bad += len(bad)

    print()
    if total_bad:
        print(f"[FAIL] 共 {total_bad} 个非法方块引用 —— 请先修正再生成（否则会被静默替换成空气）")
        return 1
    print("[OK] 全部合法，可以生成")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
