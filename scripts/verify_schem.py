#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
verify_schem.py —— .schem 回读断言 + 版本间体素级 diff

两种模式
--------
--assert   三类断言，任一失败即退出码 1：
              格式层  gzip 头 / Sponge Version / DataVersion / 尺寸与 BlockData 长度自洽
              完整性层 调色板每一项都存在于目标版本（拦"静默变空气"）
              规格层   与 report.json 声明的尺寸、方块总数一致

--diff     迭代类修改的**硬门禁**：把新旧 .schem 解码回体素逐点比对，
           输出变更集，并要求变更全部落在声明的预期区域内。
               越界变更数 != 0  →  判定失败
           这把"其余部分逐点不变"从人眼检查变成机器断言。

用法
----
    python verify_schem.py --assert out/castle.schem
    python verify_schem.py --assert out/castle.schem --report out/castle.report.json
    python verify_schem.py --diff v1/castle.schem v2/castle.schem --expect-bbox 0,18,0,62,25,62
    python verify_schem.py --diff old.schem new.schem --expect-bbox-file expect.json
    python verify_schem.py --dump out/castle.schem --slice-y 10        # 看某一层平面图
"""

from __future__ import annotations

import argparse
import gzip
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from palette_1_21_1 import (  # noqa: E402
    DATA_VERSION, MC_VERSION, SPONGE_VERSION, validate_blockstate,
)

AIR = "minecraft:air"


# ============================================================================
# 解码
# ============================================================================
def _read_varint(data, pos: int) -> tuple[int, int]:
    val = 0
    shift = 0
    n = len(data)
    while True:
        if pos >= n:
            raise ValueError("BlockData 中的 varint 被截断")
        b = data[pos] & 0xFF
        pos += 1
        val |= (b & 0x7F) << shift
        if not (b & 0x80):
            break
        shift += 7
        if shift > 35:
            raise ValueError("varint 过长，文件可能不是 Sponge 格式")
    return val, pos


def decode_schem(path: str) -> tuple[dict[tuple[int, int, int], str], dict]:
    """
    解码 .schem（Sponge v2）为 {(x,y,z): blockstate}。
    坐标是**文件坐标**（0 起）；配合 report 的 bbox 可换算回构建坐标。

    返回 (voxels, meta)，meta 含 width/height/length/palette/data_version/version/offset。
    索引顺序遵循 Sponge 规范：x + z*W + y*W*L
    """
    if not os.path.isfile(path):
        raise FileNotFoundError(path)

    with open(path, "rb") as f:
        head = f.read(2)
    gzipped = head == b"\x1f\x8b"
    try:
        import nbtlib
    except ImportError as e:
        raise SystemExit(f"[FAIL] 需要 nbtlib 才能解码 .schem：{e}\n       请先 pip install nbtlib") from e

    root = nbtlib.load(path)
    keys = set(root.keys())

    def pick(*names, default=None):
        for n in names:
            if n in root:
                return root[n]
        return default

    width = int(pick("Width", default=0))
    height = int(pick("Height", default=0))
    length = int(pick("Length", default=0))
    data_version = pick("DataVersion")
    version = pick("Version", "SchematicVersion")
    offset = pick("Offset")
    pal_tag = pick("Palette", "Blocks")

    if width <= 0 or height <= 0 or length <= 0:
        raise ValueError(f"{path} 缺少合法的 Width/Height/Length")

    if pal_tag is None:
        raise ValueError(f"{path} 缺少 Palette")

    # Palette: name -> id
    idx2name: dict[int, str] = {}
    for name, tag in pal_tag.items():
        idx2name[int(tag)] = str(name)

    block_data = pick("BlockData")
    if block_data is None:
        raise ValueError(f"{path} 缺少 BlockData")

    raw = bytes((b & 0xFF) for b in block_data)
    voxels: dict[tuple[int, int, int], str] = {}
    pos = 0
    total = width * height * length
    for i in range(total):
        idx, pos = _read_varint(raw, pos)
        name = idx2name.get(idx)
        if name is None:
            raise ValueError(f"调色板索引 {idx} 越界（palette 只有 {len(idx2name)} 项）")
        if name == AIR:
            continue
        x = i % width
        z = (i // width) % length
        y = i // (width * length)
        voxels[(x, y, z)] = name

    meta = {
        "path": os.path.abspath(path),
        "gzipped": gzipped,
        "width": width, "height": height, "length": length,
        "version": int(version) if version is not None else None,
        "data_version": int(data_version) if data_version is not None else None,
        "offset": [int(v) for v in offset] if offset is not None else None,
        "palette": {i: n for i, n in sorted(idx2name.items())},
        "blockdata_bytes": len(raw),
        "non_air": len(voxels),
        "total_cells": total,
    }
    return voxels, meta


def _load_report(schem_path: str, explicit: str | None = None) -> dict | None:
    """
    找对应的 .report.json。

    分片产物的文件名带 `_pNN` 后缀（如 big_castle_p01.schem），
    但 report 是按 basename 命名的（big_castle.report.json），所以要回退匹配。
    """
    import re
    if explicit:
        if not os.path.isfile(explicit):
            return None
        with open(explicit, encoding="utf-8") as f:
            return json.load(f)

    stem = os.path.splitext(schem_path)[0]
    cands = [stem + ".report.json"]
    m = re.match(r"^(.*)_p\d+$", stem)
    if m:
        cands.append(m.group(1) + ".report.json")
    for c in cands:
        if os.path.isfile(c):
            with open(c, encoding="utf-8") as f:
                return json.load(f)
    return None


def _report_bbox_for(report: dict, schem_path: str) -> list[int] | None:
    """从 report 里找出与该文件对应的 bbox（分片时按文件名匹配）。"""
    if not report:
        return None
    base = os.path.basename(schem_path)
    for o in report.get("outputs", []):
        if os.path.basename(o.get("file", "")) == base:
            return list(o.get("bbox") or [])
    return list(report.get("bbox") or []) or None


def to_build_coords(voxels: dict, bbox: list[int] | None) -> dict:
    """文件坐标 -> 构建坐标（加上 bbox 左下）。bbox 缺失时原样返回。"""
    if not bbox:
        return voxels
    ox, oy, oz = bbox[0], bbox[1], bbox[2]
    return {(x + ox, y + oy, z + oz): b for (x, y, z), b in voxels.items()}


# ============================================================================
# --assert
# ============================================================================
def run_assert(schem_path: str, report_path: str | None, as_json: bool) -> int:
    checks: list[tuple[str, bool, str]] = []

    def chk(layer: str, ok: bool, msg: str) -> None:
        checks.append((layer, ok, msg))

    try:
        voxels, meta = decode_schem(schem_path)
    except SystemExit:
        raise
    except Exception as e:
        print(f"[FAIL] 无法解码 {schem_path}：{e}")
        return 1

    # ---------- 格式层 ----------
    chk("格式", meta["gzipped"], f"gzip 头存在（{'是' if meta['gzipped'] else '否'}）")
    chk("格式", meta["version"] == SPONGE_VERSION,
        f"Sponge Version = {meta['version']}（期望 {SPONGE_VERSION}）")
    chk("格式", meta["data_version"] == DATA_VERSION,
        f"DataVersion = {meta['data_version']}（期望 {DATA_VERSION}，即 MC {MC_VERSION}）")
    chk("格式", meta["blockdata_bytes"] > 0,
        f"BlockData 有 {meta['blockdata_bytes']} 字节")
    chk("格式", 0 in meta["palette"] and meta["palette"][0] == AIR,
        f"palette[0] = {meta['palette'].get(0)}（应为 air）")

    # ---------- 完整性层 ----------
    bad_pal: list[str] = []
    for _, name in sorted(meta["palette"].items()):
        if name == AIR:
            continue
        ok, _msg = validate_blockstate(name)
        if not ok:
            bad_pal.append(name)
    chk("完整性", not bad_pal,
        f"调色板 {len(meta['palette'])} 项全部存在于 MC {MC_VERSION}"
        if not bad_pal else f"调色板存在 {len(bad_pal)} 个非法方块：{bad_pal[:5]}")

    # ---------- 规格层 ----------
    rep = _load_report(schem_path, report_path)
    if rep:
        bbox = _report_bbox_for(rep, schem_path)
        if bbox:
            w = bbox[3] - bbox[0] + 1
            h = bbox[4] - bbox[1] + 1
            L = bbox[5] - bbox[2] + 1
            chk("规格", (w, h, L) == (meta["width"], meta["height"], meta["length"]),
                f"尺寸与 report 一致 {meta['width']}×{meta['height']}×{meta['length']} "
                f"(期望 {w}×{h}×{L})")
        exp_blocks = None
        for o in rep.get("outputs", []):
            if os.path.basename(o.get("file", "")) == os.path.basename(schem_path):
                exp_blocks = o.get("blocks")
        if exp_blocks is None:
            exp_blocks = rep.get("blocks_total")
        chk("规格", exp_blocks == meta["non_air"],
            f"非空气方块数 {meta['non_air']} 与 report 一致（期望 {exp_blocks}）")
    else:
        chk("规格", True, "未找到 report.json，跳过规格层比对（仅做格式与完整性检查）")

    # ---------- 输出 ----------
    n_fail = sum(1 for _, ok, _ in checks if not ok)
    if as_json:
        print(json.dumps({
            "file": os.path.abspath(schem_path), "ok": n_fail == 0,
            "checks": [{"layer": l, "ok": o, "msg": m} for l, o, m in checks],
            "meta": {k: v for k, v in meta.items() if k != "palette"},
        }, ensure_ascii=False, indent=2))
        return 0 if n_fail == 0 else 1

    print(f"文件 : {schem_path}")
    print(f"尺寸 : {meta['width']} × {meta['height']} × {meta['length']}   非空气 {meta['non_air']} 方块")
    print(f"调色板: {len(meta['palette'])} 项")
    print()
    cur = None
    for layer, ok, msg in checks:
        if layer != cur:
            print(f"── {layer}层 ──")
            cur = layer
        print(f"  {'OK  ' if ok else '[X] '} {msg}")
    print()
    if n_fail:
        print(f"[FAIL] {n_fail}/{len(checks)} 项断言未通过")
        return 1
    print(f"[OK] 全部 {len(checks)} 项断言通过")
    return 0


# ============================================================================
# --diff
# ============================================================================
def _parse_bbox(text: str) -> list[int]:
    parts = [p.strip() for p in text.replace("，", ",").split(",") if p.strip()]
    if len(parts) != 6:
        raise ValueError(f"bbox 需要 6 个整数 x0,y0,z0,x1,y1,z1，收到 {text!r}")
    return [int(p) for p in parts]


def run_diff(old_path: str, new_path: str, expect_bbox: list[int] | None,
             report_old: str | None, report_new: str | None, as_json: bool,
             show_limit: int = 20) -> int:
    ov, ometa = decode_schem(old_path)
    nv, nmeta = decode_schem(new_path)

    ob = _report_bbox_for(_load_report(old_path, report_old) or {}, old_path)
    nb = _report_bbox_for(_load_report(new_path, report_new) or {}, new_path)
    ov = to_build_coords(ov, ob)
    nv = to_build_coords(nv, nb)

    added = {k: v for k, v in nv.items() if k not in ov}
    removed = {k: v for k, v in ov.items() if k not in nv}
    changed = {k: (ov[k], nv[k]) for k in ov.keys() & nv.keys() if ov[k] != nv[k]}
    changed_keys = set(changed)

    all_keys = set(added) | set(removed) | changed_keys
    if all_keys:
        xs = [k[0] for k in all_keys]; ys = [k[1] for k in all_keys]; zs = [k[2] for k in all_keys]
        cbbox = [min(xs), min(ys), min(zs), max(xs), max(ys), max(zs)]
    else:
        cbbox = None

    out_of_range: list[tuple[int, int, int]] = []
    if expect_bbox and all_keys:
        ex0, ey0, ez0, ex1, ey1, ez1 = expect_bbox
        for (x, y, z) in all_keys:
            if not (ex0 <= x <= ex1 and ey0 <= y <= ey1 and ez0 <= z <= ez1):
                out_of_range.append((x, y, z))

    ok = True
    reasons = []
    if expect_bbox and out_of_range:
        ok = False
        reasons.append(f"越界变更 {len(out_of_range)} 处（必须为 0）")
    if expect_bbox and cbbox:
        ex0, ey0, ez0, ex1, ey1, ez1 = expect_bbox
        if not (ex0 <= cbbox[0] and cbbox[3] <= ex1 and ey0 <= cbbox[1] and cbbox[4] <= ey1
                and ez0 <= cbbox[2] and cbbox[5] <= ez1):
            ok = False
            reasons.append("变更包围盒超出预期区域")

    if as_json:
        print(json.dumps({
            "ok": ok, "old": os.path.abspath(old_path), "new": os.path.abspath(new_path),
            "added": len(added), "removed": len(removed), "changed": len(changed),
            "change_bbox": cbbox, "expect_bbox": expect_bbox,
            "out_of_range": len(out_of_range), "reasons": reasons,
            "samples": {
                "added": [[list(k), v] for k, v in list(added.items())[:show_limit]],
                "removed": [[list(k), v] for k, v in list(removed.items())[:show_limit]],
                "changed": [[list(k), a, b] for k, (a, b) in list(changed.items())[:show_limit]],
            },
        }, ensure_ascii=False, indent=2))
        return 0 if ok else 1

    print(f"旧 : {old_path}   ({ometa['non_air']} 方块)")
    print(f"新 : {new_path}   ({nmeta['non_air']} 方块)")
    print()
    print("变更集")
    print(f"  新增 {len(added)}   删除 {len(removed)}   替换 {len(changed)}")
    if cbbox:
        print(f"  变更包围盒 x {cbbox[0]}..{cbbox[3]} | y {cbbox[1]}..{cbbox[4]} | z {cbbox[2]}..{cbbox[5]}")
    if expect_bbox:
        print(f"  预期区域   x {expect_bbox[0]}..{expect_bbox[3]} | y {expect_bbox[1]}..{expect_bbox[4]}"
              f" | z {expect_bbox[2]}..{expect_bbox[5]}")
        print(f"  越界变更   {len(out_of_range)}" + ("   ← 必须为 0" if out_of_range else ""))
    if added:
        print(f"  新增样例（前 {min(show_limit, len(added))}）：")
        for k, v in list(added.items())[:show_limit]:
            print(f"     + {k}  {v}")
    if removed:
        print(f"  删除样例（前 {min(show_limit, len(removed))}）：")
        for k, v in list(removed.items())[:show_limit]:
            print(f"     - {k}  {v}")
    if changed:
        print(f"  替换样例（前 {min(show_limit, len(changed))}）：")
        for k, (a, b) in list(changed.items())[:show_limit]:
            print(f"     ~ {k}  {a} -> {b}")
    print()
    if not ok:
        print("[FAIL] " + "；".join(reasons))
        return 1
    if not all_keys:
        print("[OK] 两份文件完全相同（0 处变更）")
        return 0
    print("[OK] 变更全部落在预期区域内（越界 0）")
    return 0


# ============================================================================
# --dump（分层平面图，便于肉眼确认结构）
# ============================================================================
def run_dump(schem_path: str, y: int) -> int:
    voxels, meta = decode_schem(schem_path)
    W, L = meta["width"], meta["length"]
    if y < 0 or y >= meta["height"]:
        print(f"[FAIL] y={y} 超出 0..{meta['height'] - 1}")
        return 1
    print(f"y = {y} 平面图（{W}×{L}，# = 有方块，. = 空）")
    for z in range(L):
        row = "".join("#" if (x, y, z) in voxels else "." for x in range(W))
        print(f"  {row}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=".schem 断言与 diff")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--assert", dest="assert_path", metavar="SCHEM", help="断言模式")
    g.add_argument("--diff", nargs=2, metavar=("OLD", "NEW"), help="diff 模式")
    g.add_argument("--dump", metavar="SCHEM", help="打印某层平面图")
    ap.add_argument("--report", help="断言模式：显式指定 report.json")
    ap.add_argument("--report-old")
    ap.add_argument("--report-new")
    ap.add_argument("--expect-bbox", help='预期变更区域 "x0,y0,z0,x1,y1,z1"')
    ap.add_argument("--expect-bbox-file", help="从 JSON 文件读 expect_bbox（可为 spec.json 的 expected_change 字段）")
    ap.add_argument("--slice-y", type=int, default=0, help="--dump 的层号")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.assert_path:
        return run_assert(args.assert_path, args.report, args.json)

    if args.dump:
        return run_dump(args.dump, args.slice_y)

    bbox = None
    if args.expect_bbox:
        bbox = _parse_bbox(args.expect_bbox)
    elif args.expect_bbox_file:
        with open(args.expect_bbox_file, encoding="utf-8") as f:
            d = json.load(f)
        raw = d.get("expected_change") or d.get("expect_bbox") or d.get("bbox")
        bbox = _parse_bbox(raw) if isinstance(raw, str) else (list(map(int, raw)) if raw else None)

    return run_diff(args.diff[0], args.diff[1], bbox,
                    args.report_old, args.report_new, args.json)


if __name__ == "__main__":
    raise SystemExit(main())
