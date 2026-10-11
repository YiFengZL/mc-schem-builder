#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gen_schem.py —— 轨道 A：spec.json 直出 .schem（含按包围盒分片）

流程：
    spec.json → validate_spec 前置校验 → 风格 builder（调 build_lib 基元）
    → VoxelGrid → 分片规划 → 导出 .schem + 报告 JSON

设计约束：
  * 生成前必跑校验，任何非法方块/越界参数都直接中止（不生成半成品）
  * 分片阈值按**含空气包围盒**判定（不是实体方块数）——Litematica 的加载压力来自包围盒
  * 分片时明确告知片数与每片基点，绝不静默截断

用法：
    python gen_schem.py --spec spec.json
    python gen_schem.py --spec spec.json --out-dir mc_builds/my_castle
    python gen_schem.py --spec spec.json --dry-run        # 只算不写盘
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import mcschematic  # noqa: E402

import geom_lib as geom  # noqa: E402  声明式几何合成层（圆/椭圆/球面/曲面/曲线…）

from build_lib import (  # noqa: E402
    AIR, DEFAULT_MAX_DIM, VoxelGrid, _rect_ring, battlements, carve_gate,
    cone_roof, connect_fence_like, door_frame, eave_band, fill_ground,
    gable_roof, hip_roof, pillar, plan_slices, railing_run, round_tower,
    slice_grid, stairs_flight, wall_ring, weather_blend, window_run,
)
from palette_1_21_1 import (  # noqa: E402
    DATA_VERSION, MC_VERSION, MCS_VERSION_ENUM, SPONGE_VERSION, slab as _slab,
)
from spec_schema import load_style, validate_spec  # noqa: E402

SCHEM_VERSION = getattr(mcschematic.Version, MCS_VERSION_ENUM)


# ============================================================================
# 风格 builder（轨道 A 与轨道 B 共用这些基元）
# ============================================================================
def build_castle(p: dict) -> VoxelGrid:
    """中世纪城堡外墙：墙基 + 墙体 + 城垛 + 四角圆塔（锥顶）+ 拱形城门。"""
    size = int(p["size"])
    x1 = size - 1
    bh, wh = int(p["base_height"]), int(p["wall_height"])
    g = VoxelGrid()

    corners = [(0, 0), (0, x1), (x1, 0), (x1, x1)] if p["tower_enable"] else []
    r = int(p["tower_radius"]) if p["tower_enable"] else 0

    def in_tower(x: int, z: int) -> bool:
        return any((x - cx) ** 2 + (z - cz) ** 2 <= r * r for cx, cz in corners)

    if bh > 0:
        wall_ring(g, 0, 0, x1, x1, 0, bh - 1, p["base_block"], 1)
    wall_ring(g, 0, 0, x1, x1, bh, wh - 1, p["wall_block"], 1)

    if p["battlement_enable"]:
        for dy in range(int(p["battlement_height"])):
            battlements(g, 0, 0, x1, x1, wh + dy, p["battlement_block"],
                        period=2, exclude=(in_tower if corners else None))

    if p["tower_enable"]:
        th = int(p["tower_height"])
        for (cx, cz) in corners:
            round_tower(g, cx, cz, r, 0, th - 1, p["tower_block"],
                        hollow=True, thickness=1, clear_interior=True)
            if p["tower_roof_enable"]:
                cone_roof(g, cx, cz, r, th, p["roof_block"],
                          slope=p["roof_slope"], hollow=True)

    if p["gate_enable"]:
        side, gw, gh = p["gate_side"], int(p["gate_width"]), int(p["gate_height"])
        c = x1 // 2
        coord = {"south": 0, "north": x1, "west": 0, "east": x1}[side]
        carve_gate(g, side, c, gw, gh, coord, depth=1, arch=bool(p["gate_arch"]))

    if not p["hollow"]:
        fill_ground(g, 0, 0, x1, x1, 0, p["base_block"])
    return g


def build_wall(p: dict) -> VoxelGrid:
    """围墙：墙基 + 墙体 + 压顶 + 可选城垛、扶壁柱、门洞。"""
    L, W = int(p["length"]), int(p["width"])
    x1, z1 = L - 1, W - 1
    t = int(p["thickness"])
    bh, wh = int(p["base_height"]), int(p["wall_height"])
    g = VoxelGrid()

    if bh > 0:
        wall_ring(g, 0, 0, x1, z1, 0, bh - 1, p["base_block"], t)
    wall_ring(g, 0, 0, x1, z1, bh, wh - 1, p["wall_block"], t)

    if p["cap_enable"]:
        for (x, z) in _rect_ring(0, 0, x1, z1, t):
            for dx in range(t):
                for dz in range(t):
                    g.set(x, wh, z, p["cap_block"])

    if p["battlement_enable"]:
        for dy in range(2):
            battlements(g, 0, 0, x1, z1, wh + 1 + dy, p["wall_block"],
                        period=2, thickness=t)

    gate_lo = gate_hi = None
    if p["gate_enable"]:
        side, gw, gh = p["gate_side"], int(p["gate_width"]), int(p["gate_height"])
        span_len = L if side in ("south", "north") else W
        c = (span_len - 1) // 2
        coord = {"south": 0, "north": z1, "west": 0, "east": x1}[side]
        carve_gate(g, side, c, gw, gh, coord, depth=t, arch=bool(p["gate_arch"]))
        if side in ("south", "north"):
            gate_lo = c - (gw - 1) // 2
            gate_hi = gate_lo + gw - 1

    if p["pillar_enable"]:
        iv = int(p["pillar_interval"])
        pb = p["pillar_block"]
        top = wh
        for x in range(0, L, iv):
            if gate_lo is not None and gate_lo <= x <= gate_hi:
                continue
            for y in range(0, top + 1):
                g.set(x, y, -1, pb)
                g.set(x, y, W, pb)
        for z in range(0, W, iv):
            for y in range(0, top + 1):
                g.set(-1, y, z, pb)
                g.set(L, y, z, pb)
    return g


def build_tower(p: dict) -> VoxelGrid:
    """独立塔楼：基座 + 塔身 + 腰线 + 窗 + 门 + 顶部（锥顶/平顶/雉堞）。"""
    r = int(p["radius"])
    h = int(p["height"])
    t = int(p["thickness"])
    bh = int(p["base_height"])
    g = VoxelGrid()

    if bh > 0:
        round_tower(g, 0, 0, r, 0, bh - 1, p["base_block"],
                    hollow=True, thickness=t, clear_interior=True)
    round_tower(g, 0, 0, r, bh, h - 1, p["wall_block"],
                hollow=True, thickness=t, clear_interior=True)

    band = int(p["band_interval"])
    if band > 0:
        for y in range(band, h, band):
            round_tower(g, 0, 0, r, y, y, p["accent_block"],
                        hollow=True, thickness=t, clear_interior=False)

    if p["window_enable"]:
        wi, wh2 = int(p["window_interval"]), int(p["window_height"])
        y = max(wi, bh + 1)
        while y + wh2 - 1 < h:
            for yy in range(y, y + wh2):
                for (dx, dz) in ((r, 0), (-r, 0), (0, r), (0, -r)):
                    g.set(dx, yy, dz, p["window_block"])
            y += wi

    if p["door_enable"]:
        dh = int(p["door_height"])
        side = p["door_side"]
        for y in range(min(dh, h)):
            for d in range(t):
                if side == "south":
                    g.carve(0, y, -r + d)
                elif side == "north":
                    g.carve(0, y, r - d)
                elif side == "west":
                    g.carve(-r + d, y, 0)
                else:
                    g.carve(r - d, y, 0)

    rt = p["roof_type"]
    if rt == "cone":
        top = cone_roof(g, 0, 0, r, h, p["roof_block"], slope=p["roof_slope"], hollow=True)
        extra = int(p["roof_height"])
        if extra > 0:
            for i in range(1, extra + 1):
                g.set(0, top + i, 0, p["accent_block"])
    elif rt == "flat":
        round_tower(g, 0, 0, r, h, h, p["accent_block"], hollow=False)
    elif rt == "battlement":
        round_tower(g, 0, 0, r, h, h, p["accent_block"], hollow=False)
        for y in (h + 1, h + 2):
            for dx in range(-r, r + 1):
                for dz in range(-r, r + 1):
                    if dx * dx + dz * dz <= r * r and (dx + dz) % 2 == 0:
                        g.set(dx, y, dz, p["wall_block"])
    return g


def build_bridge(p: dict) -> VoxelGrid:
    """拱桥：桥面 + 桥墩 + 椭圆拱券 + 可选栏杆。"""
    L, W = int(p["span"]), int(p["deck_width"])
    dy, dt = int(p["deck_y"]), int(p["deck_thickness"])
    ac, pw = int(p["arch_count"]), int(p["pier_width"])
    ratio = float(p["arch_thickness"])
    z0, z1 = 0, W - 1
    g = VoxelGrid()

    net_total = L - (ac + 1) * pw
    net = net_total // ac
    if net < 2:
        raise ValueError(f"单拱净跨过小（{net}）；请减少拱数或增大跨度")

    spans: list[tuple[str, int, int]] = []
    pos = 0
    for i in range(ac + 1):
        spans.append(("pier", pos, pos + pw - 1))
        pos += pw
        if i < ac:
            spans.append(("arch", pos, pos + net - 1))
            pos += net
    spans[-1] = ("pier", spans[-1][1], L - 1)

    for kind, lo, hi in spans:
        blk = p["pier_block"] if kind == "pier" else p["arch_block"]
        for x in range(lo, hi + 1):
            for z in range(z0, z1 + 1):
                for y in range(0, dy):
                    g.set(x, y, z, blk)

    rx = net / 2.0
    ry = min(net * ratio, float(max(1, dy - dt)))
    ymax = dy - dt - 1                       # 原循环 range(0, dy-dt) 的整数上界
    for kind, lo, hi in spans:
        if kind != "arch" or ymax < 0 or ry <= 0:
            continue
        cx = (lo + hi) / 2.0
        # 椭圆拱券 = Ellipse2 ∩ 限高矩形，沿 z（桥宽）拉伸后挖空
        arch2 = geom.Ellipse2(cx, 0.0, rx, ry) & geom.Rect2(lo, 0, hi, ymax)
        geom.carve(g, geom.Prism(arch2, "xy", z0, z1))

    for x in range(L):
        for z in range(z0, z1 + 1):
            for k in range(dt):
                g.set(x, dy + k, z, p["deck_block"])

    if p["railing_enable"]:
        rh = int(p["railing_height"])
        for x in range(L):
            for k in range(rh):
                g.set(x, dy + dt + k, z0, p["railing_block"])
                g.set(x, dy + dt + k, z1, p["railing_block"])
    return g


def build_modern_villa(p: dict) -> VoxelGrid:
    """
    现代别墅外壳：错层体块（可选顶部内收退台）+ 层间楼板与腰线 + 成排窗户（窗框/窗台）
    + 入口门廊 + 结构柱网 + 挑檐平顶/可上人露台（栏杆）+ 可选做旧。内部为空。

    相比旧版：整条玻璃窗带改为按开间排窗（B3 window_run），四角柱改为柱网
    （pillar_interval），露台女儿墙改为栏杆（B3 railing_run），并新增入口与退台。
    """
    L, W = int(p["length"]), int(p["width"])
    fh, nf = int(p["floor_height"]), int(p["floors"])
    H = fh * nf
    oh = int(p["overhang"])
    vin = max(0, int(p["volume_inset"]))
    vfl = max(1, int(p["volume_floors"]))
    # 退台：顶部 vfl 层内收 vin 格。split_y 之上用内收 footprint。
    split_f = max(0, nf - vfl) if vin > 0 else nf
    split_y = split_f * fh
    g = VoxelGrid()

    def fp(y: int) -> tuple[int, int, int, int]:
        """该高度处的墙体 footprint (x0,z0,x1,z1)；退台以上内收 vin 格。"""
        if vin > 0 and y >= split_y:
            return vin, vin, L - 1 - vin, W - 1 - vin
        return 0, 0, L - 1, W - 1

    # 墙体：分两段画（退台处自然形成台面，无需额外挖补）
    if split_y > 0:
        wall_ring(g, 0, 0, L - 1, W - 1, 0, split_y - 1, p["wall_block"], 1)
    if split_y < H:
        x0, z0, x1, z1 = fp(split_y)
        wall_ring(g, x0, z0, x1, z1, split_y, H - 1, p["wall_block"], 1)
        if split_y > 0:
            # 下层屋面的外露环带 = 完整 footprint 减内收 footprint，恰为厚 vin 的墙圈
            for (x, z) in _rect_ring(0, 0, L - 1, W - 1, vin):
                g.set(x, split_y, z, p["trim_block"])

    # 层间楼板 + 腰线
    for f in range(1, nf):
        y = f * fh
        x0, z0, x1, z1 = fp(y)
        if x1 - x0 >= 2 and z1 - z0 >= 2:
            g.fill_box((x0 + 1, y, z0 + 1), (x1 - 1, y, z1 - 1), p["floor_slab_block"])
        eave_band(g, x0, z0, x1, z1, y, p["accent_block"], projection=0, thickness=1)

    if not p["hollow"]:
        g.fill_box((1, 0, 1), (L - 2, 0, W - 2), p["floor_slab_block"])

    # 入口（一层）—— 一层始终是完整 footprint
    ent_side = str(p["entrance_side"])
    if p["entrance_enable"]:
        wc, ub, span = _fp_side(ent_side, 0, 0, L - 1, W - 1)
        cu = ub + (span - 1) // 2
        door_frame(g, ent_side, wc, center=cu, y0=0,
                   width=int(p["entrance_width"]), height=int(p["entrance_height"]),
                   depth=1, door_block=p["entrance_door_block"],
                   frame_block=p["entrance_frame_block"], hinge=str(p["entrance_hinge"]))
        steps = int(p["entrance_steps"])
        if steps > 0:
            _entrance_steps(g, ent_side, cu, int(p["entrance_width"]), steps,
                            p["entrance_step_block"], p["accent_block"], W, L)

    # 成排窗户：逐层逐面，按该层 footprint 排布
    if p["window_enable"]:
        ww, wh = int(p["window_width"]), int(p["window_height"])
        sp, mg = int(p["window_spacing"]), int(p["window_margin"])
        frame = p["window_frame"] if p["window_frame_enable"] else None
        sill = p["window_sill"] if p["window_sill_enable"] else None
        for f in range(nf):
            y0 = f * fh + 1
            fpx = fp(y0)
            for side in _SIDES:
                if f == 0 and p["entrance_enable"] and side == ent_side:
                    continue          # 一层入口面不排窗，避免吃掉门洞
                wc, ub, span = _fp_side(side, *fpx)
                count, off = _window_layout(span, ww, sp, mg)
                window_run(g, side, wc, start=ub + off, y0=y0, count=count, spacing=sp,
                           width=ww, height=wh, depth=1, glass_block=p["glass_block"],
                           frame_block=frame, sill_block=sill)

    # 柱网：沿墙圈每 interval 格一根竖向立柱（不覆盖窗户）
    interval = int(p["pillar_interval"])
    if p["pillar_enable"]:
        glass = p["glass_block"]
        for (ya, yb, fpx) in ((0, split_y - 1, (0, 0, L - 1, W - 1)),
                              (max(split_y, 0), H - 1, fp(max(split_y, 0)))):
            if ya > yb:
                continue
            x0, z0, x1, z1 = fpx
            pos: set[tuple[int, int]] = set()
            for x in range(x0, x1 + 1):
                if (x - x0) % interval == 0:
                    pos.add((x, z0)); pos.add((x, z1))
            for z in range(z0, z1 + 1):
                if (z - z0) % interval == 0:
                    pos.add((x0, z)); pos.add((x1, z))
            for y in range(ya, yb + 1):
                for (x, z) in pos:
                    if g.get(x, y, z) != glass:
                        g.set(x, y, z, p["pillar_block"])

    # 屋顶：挑檐屋面板 + 平顶压边 / 露台栏杆
    rx0, rz0, rx1, rz1 = fp(H)
    dx0, dz0, dx1, dz1 = rx0 - oh, rz0 - oh, rx1 + oh, rz1 + oh
    for x in range(dx0, dx1 + 1):
        for z in range(dz0, dz1 + 1):
            g.set(x, H, z, p["trim_block"])
    eave_band(g, dx0, dz0, dx1, dz1, H, p["terrace_block"], projection=0, thickness=1)
    if p["roof_type"] == "terrace":
        ry = H + 1
        pe = max(2, interval if p["pillar_enable"] else 3)
        for (ax, az), (bx, bz) in (((dx0, dz0), (dx1, dz0)), ((dx0, dz1), (dx1, dz1)),
                                   ((dx0, dz0), (dx0, dz1)), ((dx1, dz0), (dx1, dz1))):
            railing_run(g, (ax, az), (bx, bz), ry, p["terrace_block"],
                        p["glass_block"], post_every=pe)
    else:
        for (x, z) in _rect_ring(dx0, dz0, dx1, dz1, 1):
            g.set(x, H + 1, z, p["terrace_block"])

    connect_fence_like(g)   # 玻璃板收尾补连接属性（整块玻璃时无副作用）

    # 做旧：只替换墙体主材，确定性哈希（同坐标恒同结果，可安全 diff）
    if p["weather_enable"]:
        base, aged = p["wall_block"], p["weather_aged_block"]
        ratio = float(p["weather_ratio"])
        for (x, y, z) in list(g._cells.keys()):
            if g.get(x, y, z) == base:
                g.set(x, y, z, weather_blend(x, y, z, base, aged, 0, H, ratio=ratio))
    return g


# ----------------------------------------------------------------------------
# modern_house 辅助（side 约定与 carve_gate / _face_xyz 一致：south = z 最小面）
# ----------------------------------------------------------------------------
_SIDES = ("south", "north", "west", "east")
# 门前台阶的「上行方向」= 朝内（与 outward 相反），值即 build_lib 的 dir 名
_SIDE_INWARD_DIR = {"south": "south", "north": "north", "west": "east", "east": "west"}


def _fp_side(side: str, x0: int, z0: int, x1: int, z1: int) -> tuple[int, int, int]:
    """
    给定墙体 footprint，返回该面的 (wall_coord, u_base, span)。

    wall_coord 是传给 window_run / door_frame / carve_gate 的固定坐标；
    u_base 是沿墙方向的起始坐标（退台体块不是从 0 开始时必须加上）；
    span 是该面的可布置长度。
    """
    if side == "south":
        return z0, x0, x1 - x0 + 1
    if side == "north":
        return z1, x0, x1 - x0 + 1
    if side == "west":
        return x0, z0, z1 - z0 + 1
    return x1, z0, z1 - z0 + 1


def _window_layout(span: int, width: int, spacing: int, margin: int) -> tuple[int, int]:
    """算一排窗的 (扇数, 起始 u)，使窗排整体居中对称。"""
    avail = span - 2 * margin
    count = max(1, (avail + spacing) // (width + spacing))
    total = count * width + (count - 1) * spacing
    return count, (span - total) // 2


def _entrance_steps(g: VoxelGrid, side: str, center_u: int, width: int, steps: int,
                    block: str, under_block: str, W: int, L: int) -> None:
    """
    门前台阶：自门（y=0）向外逐级下降。

    最高一级落在**墙外一格**（south 侧为 z=-1），而不是墙面 z=0 —— 否则会覆盖
    门洞底部的门板方块（实测：会把 oak_door 下半替换成台阶）。
    """
    dlo = center_u - (width - 1) // 2
    y0 = -(steps - 1)
    if side == "south":
        sx, sz = dlo, -steps
    elif side == "north":
        sx, sz = dlo, (W - 1) + steps
    elif side == "west":
        sx, sz = -steps, dlo
    else:                                    # east
        sx, sz = (L - 1) + steps, dlo
    stairs_flight(g, sx, sz, y0, steps, block, dir=_SIDE_INWARD_DIR[side],
                  width=width, under_block=under_block)


def build_modern_house(p: dict) -> VoxelGrid:
    """
    现代民居外壳：多层体块（可选顶部内收退台）+ 层间腰线/楼板 + 成排窗户 + 入口门廊
    （可选车库、柱网、做旧）+ 双坡/四坡/平顶（可选栏杆代替实心压边）。

    屋顶沿用 build_lib B1 语义：**只出屋面壳**，不填屋面下方实体。
    窗户/腰线/栏杆由 B3 基元生成；做旧由 B4 的确定性哈希完成（同坐标恒同结果）。
    """
    L, W = int(p["length"]), int(p["width"])
    fh, nf = int(p["floor_height"]), int(p["floors"])
    H = fh * nf
    oh = int(p["roof_overhang"])
    vin = max(0, int(p["volume_inset"]))
    vfl = max(1, int(p["volume_floors"]))
    # 退台：顶部 vfl 层内收 vin 格。split_y 之上用内收 footprint。
    split_f = max(0, nf - vfl) if vin > 0 else nf
    split_y = split_f * fh
    g = VoxelGrid()

    def fp(y: int) -> tuple[int, int, int, int]:
        """该高度处的墙体 footprint (x0,z0,x1,z1)；退台以上内收 vin 格。"""
        if vin > 0 and y >= split_y:
            return vin, vin, L - 1 - vin, W - 1 - vin
        return 0, 0, L - 1, W - 1

    # 墙体：分两段画（退台处自然形成台面，无需额外挖补）
    if split_y > 0:
        wall_ring(g, 0, 0, L - 1, W - 1, 0, split_y - 1, p["wall_block"], 1)
    if split_y < H:
        x0, z0, x1, z1 = fp(split_y)
        wall_ring(g, x0, z0, x1, z1, split_y, H - 1, p["wall_block"], 1)
        if split_y > 0:
            # 下层屋面的外露环带 = 完整 footprint 减内收 footprint，恰为厚 vin 的墙圈
            for (x, z) in _rect_ring(0, 0, L - 1, W - 1, vin):
                g.set(x, split_y, z, p["roof_flat_block"])

    # 层间：内部一层楼板 + 外圈腰线
    for f in range(1, nf):
        y = f * fh
        x0, z0, x1, z1 = fp(y)
        if x1 - x0 >= 2 and z1 - z0 >= 2:
            g.fill_box((x0 + 1, y, z0 + 1), (x1 - 1, y, z1 - 1), p["floor_slab_block"])
        eave_band(g, x0, z0, x1, z1, y, p["accent_block"], projection=0, thickness=1)

    # 一层大洞口（入口 / 车库）先挖；这些面的一层不再排窗，避免洞口被窗吃掉
    # 一层始终是完整 footprint（退台约束保证 volume_floors < floors）
    skip_ground: set[str] = set()

    if p["entrance_enable"]:
        side = str(p["entrance_side"])
        wc, ub, span = _fp_side(side, 0, 0, L - 1, W - 1)
        cu = ub + (span - 1) // 2
        door_frame(g, side, wc, center=cu, y0=0,
                   width=int(p["entrance_width"]), height=int(p["entrance_height"]),
                   depth=1, door_block=p["entrance_door_block"],
                   frame_block=p["entrance_frame_block"], hinge=str(p["entrance_hinge"]))
        skip_ground.add(side)
        steps = int(p["entrance_steps"])
        if steps > 0:
            _entrance_steps(g, side, cu, int(p["entrance_width"]), steps,
                            p["entrance_step_block"], p["accent_block"], W, L)

    if p["garage_enable"]:
        side = str(p["garage_side"])
        wc, ub, span = _fp_side(side, 0, 0, L - 1, W - 1)
        carve_gate(g, side, ub + (span - 1) // 2, int(p["garage_width"]),
                   int(p["garage_height"]), wc, depth=1, arch=False)
        skip_ground.add(side)

    # 成排窗户：逐层逐面，按该层 footprint 排布
    if p["window_enable"]:
        ww, wh = int(p["window_width"]), int(p["window_height"])
        sp, mg = int(p["window_spacing"]), int(p["window_margin"])
        frame = p["window_frame"] if p["window_frame_enable"] else None
        sill = p["window_sill"] if p["window_sill_enable"] else None
        for f in range(nf):
            y0 = f * fh + 1
            fpx = fp(y0)
            for side in _SIDES:
                if f == 0 and side in skip_ground:
                    continue
                wc, ub, span = _fp_side(side, *fpx)
                count, off = _window_layout(span, ww, sp, mg)
                window_run(g, side, wc, start=ub + off, y0=y0, count=count, spacing=sp,
                           width=ww, height=wh, depth=1, glass_block=p["window_glass"],
                           frame_block=frame, sill_block=sill)

    # 柱网：沿墙圈每 interval 格一根竖向立柱（不覆盖窗户）
    interval = int(p["pillar_interval"])
    if p["pillar_enable"]:
        glass = p["window_glass"]
        for (ya, yb, fpx) in ((0, split_y - 1, (0, 0, L - 1, W - 1)),
                              (max(split_y, 0), H - 1, fp(max(split_y, 0)))):
            if ya > yb:
                continue
            x0, z0, x1, z1 = fpx
            pos: set[tuple[int, int]] = set()
            for x in range(x0, x1 + 1):
                if (x - x0) % interval == 0:
                    pos.add((x, z0)); pos.add((x, z1))
            for z in range(z0, z1 + 1):
                if (z - z0) % interval == 0:
                    pos.add((x0, z)); pos.add((x1, z))
            for y in range(ya, yb + 1):
                for (x, z) in pos:
                    if g.get(x, y, z) != glass:
                        g.set(x, y, z, p["pillar_block"])

    # 屋顶：铺在最顶层的 footprint 上
    rt = str(p["roof_type"])
    rx0, rz0, rx1, rz1 = fp(H)
    if rt == "gable":
        gable_roof(g, rx0, rz0, rx1, rz1, H, p["roof_block"],
                   ridge_axis=str(p["roof_ridge_axis"]), overhang=oh,
                   gable_block=p["gable_block"])
    elif rt == "hip":
        hip_roof(g, rx0, rz0, rx1, rz1, H, p["roof_block"], overhang=oh)
    else:                                        # flat：屋面板 + 栏杆 / 实心压边
        blk = p["roof_flat_block"]
        dx0, dz0, dx1, dz1 = rx0 - oh, rz0 - oh, rx1 + oh, rz1 + oh
        for x in range(dx0, dx1 + 1):
            for z in range(dz0, dz1 + 1):
                g.set(x, H, z, blk)
        if p["roof_railing_enable"]:
            ry = H + 1
            pe = max(2, interval if p["pillar_enable"] else 3)
            for (ax, az), (bx, bz) in (((dx0, dz0), (dx1, dz0)), ((dx0, dz1), (dx1, dz1)),
                                       ((dx0, dz0), (dx0, dz1)), ((dx1, dz0), (dx1, dz1))):
                railing_run(g, (ax, az), (bx, bz), ry, p["roof_railing_block"],
                            p["window_glass"], post_every=pe)
        else:
            for (x, z) in _rect_ring(dx0, dz0, dx1, dz1, 1):
                g.set(x, H + 1, z, blk)

    connect_fence_like(g)   # 玻璃板类收尾补连接属性（整块玻璃时无副作用）

    # 做旧：只替换墙体主材，确定性哈希（同坐标恒同结果，可安全 diff）
    if p["weather_enable"]:
        base, aged = p["wall_block"], p["weather_aged_block"]
        ratio = float(p["weather_ratio"])
        for (x, y, z) in list(g._cells.keys()):
            if g.get(x, y, z) == base:
                g.set(x, y, z, weather_blend(x, y, z, base, aged, 0, H, ratio=ratio))
    return g


# ----------------------------------------------------------------------------
# 新风格（几何合成层专用）：螺旋塔 / 自由曲面屋面 / 斜向结构
# ----------------------------------------------------------------------------
def build_spiral_tower(p: dict) -> VoxelGrid:
    """
    螺旋塔：圆塔身（圆柱壳）+ 外挂**螺旋坡道**（空间闭合曲线）+ 腰线 + 窗 + 门
    + 锥顶 / 平顶 / 雉堞。内部为空。

    螺旋坡道是本风格的核心，几何由 geom_lib 的 **参数方程空间曲线** 定义
    （`geom.helix` -> `geom.SpaceCurve`）：换圈数/起点高度/偏移只需改 t 的参数，
    不必手写三角函数循环，也不会在接缝处断线（管状半径自动保证连通）。
    """
    r = int(p["radius"])
    h = int(p["height"])
    t = int(p["thickness"])
    bh = int(p["base_height"])
    g = VoxelGrid()

    if bh > 0:
        round_tower(g, 0, 0, r, 0, bh - 1, p["base_block"],
                    hollow=True, thickness=t, clear_interior=True)
    round_tower(g, 0, 0, r, bh, h - 1, p["wall_block"],
                hollow=True, thickness=t, clear_interior=True)

    band = int(p["band_interval"])
    if band > 0:
        for y in range(bh + band, h, band):
            round_tower(g, 0, 0, r, y, y, p["accent_block"],
                        hollow=True, thickness=t, clear_interior=False)

    if p["window_enable"]:
        wi, wh2 = int(p["window_interval"]), int(p["window_height"])
        y = max(bh + wi, bh + 1)
        while y + wh2 - 1 < h:
            for yy in range(y, y + wh2):
                for (dx, dz) in ((r, 0), (-r, 0), (0, r), (0, -r)):
                    g.set(dx, yy, dz, p["window_block"])
            y += wi

    if p["door_enable"]:
        dh = min(int(p["door_height"]), h)
        side = str(p["door_side"])
        for y in range(dh):
            for d in range(t):
                if side == "south":
                    g.carve(0, y, -r + d)
                elif side == "north":
                    g.carve(0, y, r - d)
                elif side == "west":
                    g.carve(-r + d, y, 0)
                else:
                    g.carve(r - d, y, 0)

    if p["ramp_enable"]:
        rp = r + float(p["ramp_offset"]) + 0.5
        curve = geom.helix(0, 0, rp, float(bh), float(max(bh, h - 1)),
                           float(p["ramp_turns"]), phase=float(p["ramp_phase"]))
        geom.fill(g, geom.SpaceCurve(curve, 0.0, 1.0, samples=int(p["ramp_samples"]),
                                     radius=float(p["ramp_width"]) / 2.0),
                  p["ramp_block"])

    rt = str(p["roof_type"])
    if rt == "cone":
        cone_roof(g, 0, 0, r, h, p["roof_block"], slope=str(p["roof_slope"]), hollow=True)
    elif rt == "battlement":
        round_tower(g, 0, 0, r, h, h, p["accent_block"], hollow=False)
        for y in (h + 1, h + 2):
            for dx in range(-r, r + 1):
                for dz in range(-r, r + 1):
                    if dx * dx + dz * dz <= r * r and (dx + dz) % 2 == 0:
                        g.set(dx, y, dz, p["wall_block"])
    else:
        round_tower(g, 0, 0, r, h, h, p["accent_block"], hollow=False)

    connect_fence_like(g)
    return g


def build_curved_roof(p: dict) -> VoxelGrid:
    """
    自由曲面屋面：矩形墙身 + 由**二元函数曲面** y = f(x, z) 生成的屋面
    （dome 穹 / saddle 鞍 / wave 波浪）+ 可选柱网 + 楼板。内部为空。

    屋面由 geom_lib 的 `FunctionSurface` 定义：换曲线类型只换 f，不动循环。
    """
    L, W = int(p["length"]), int(p["width"])
    wh, bh = int(p["wall_height"]), int(p["base_height"])
    x1, z1 = L - 1, W - 1
    g = VoxelGrid()

    if bh > 0:
        wall_ring(g, 0, 0, x1, z1, 0, bh - 1, p["base_block"], 1)
    wall_ring(g, 0, 0, x1, z1, bh, wh - 1, p["wall_block"], 1)

    if p["pillar_enable"]:
        iv = int(p["pillar_interval"])
        for x in range(1, L - 1, iv):
            for z in range(1, W - 1, iv):
                pillar(g, x, z, bh, wh - 1, p["pillar_block"])

    cx, cz = (L - 1) / 2.0, (W - 1) / 2.0
    rise = float(p["roof_rise"])
    ctype = str(p["curve_type"])

    def f(x: int, z: int) -> float:
        u = (x - cx) / max(1.0, cx)          # -1..1
        v = (z - cz) / max(1.0, cz)
        if ctype == "saddle":
            return wh + rise * (u * u - v * v)
        if ctype == "wave":
            return wh + rise * math.cos(math.pi * u / 2.0) * math.cos(math.pi * v / 2.0)
        return wh + rise * math.sqrt(max(0.0, 1.0 - (u * u + v * v)))    # dome

    geom.fill(g, geom.FunctionSurface(f, 0, x1, 0, z1,
                                      thickness=int(p["roof_thickness"])),
              p["roof_block"])

    if not p["hollow"]:
        g.fill_box((1, 0, 1), (x1 - 1, 0, z1 - 1), p["floor_block"])
    connect_fence_like(g)
    return g


def build_slant_house(p: dict) -> VoxelGrid:
    """
    斜向结构：四面**倾斜墙体**（平行四边形，逐层水平偏移 lean 格）围合的体块
    + 楼板 + 平顶（可选栏杆）/ 双坡顶。内部为空。

    墙面几何由 geom_lib 的 `Parallelogram2`（剪切矩形，沿 z 拉伸）定义：
    倾斜量是一个参数，不必在循环里手算每层的偏移量。
    """
    L, W, H = int(p["length"]), int(p["width"]), int(p["height"])
    lean = int(p["lean"])
    x1, z1 = L - 1, W - 1
    g = VoxelGrid()

    # 南/北墙：每层一行（沿 x 斜切）；西/东墙：沿 z 贯通的斜板
    for solid in (
        geom.Prism(geom.Parallelogram2(0, 0, x1, H - 1, lean), "xy", 0, 0),
        geom.Prism(geom.Parallelogram2(0, 0, x1, H - 1, lean), "xy", z1, z1),
        geom.Prism(geom.Parallelogram2(0, 0, 0, H - 1, lean), "xy", 0, z1),
        geom.Prism(geom.Parallelogram2(x1, 0, x1, H - 1, lean), "xy", 0, z1),
    ):
        geom.fill(g, solid, p["wall_block"])

    if not p["hollow"]:
        g.fill_box((0, 0, 0), (x1, 0, z1), p["floor_block"])

    # 屋顶铺在最顶层的斜 footprint 上（顶层被 lean*(H) 平移）
    sx = lean * H
    oh = int(p["roof_overhang"])
    if str(p["roof_type"]) == "gable":
        gable_roof(g, sx, 0, x1 + sx, z1, H, p["roof_block"],
                   ridge_axis=str(p["roof_ridge_axis"]), overhang=oh)
    else:
        dx0, dz0, dx1, dz1 = sx - oh, -oh, x1 + sx + oh, z1 + oh
        for x in range(dx0, dx1 + 1):
            for z in range(dz0, dz1 + 1):
                g.set(x, H, z, p["roof_block"])
        if p["railing_enable"]:
            for (ax, az), (bx, bz) in (((dx0, dz0), (dx1, dz0)), ((dx0, dz1), (dx1, dz1)),
                                       ((dx0, dz0), (dx0, dz1)), ((dx1, dz0), (dx1, dz1))):
                railing_run(g, (ax, az), (bx, bz), H + 1, p["railing_block"],
                            p["glass_block"], post_every=3)

    connect_fence_like(g)
    return g


BUILDERS = {
    "castle": build_castle,
    "wall": build_wall,
    "tower": build_tower,
    "bridge": build_bridge,
    "modern_villa": build_modern_villa,
    "modern_house": build_modern_house,
    "spiral_tower": build_spiral_tower,
    "curved_roof": build_curved_roof,
    "slant_house": build_slant_house,
}


# ============================================================================
# 导出
# ============================================================================
def export(g: VoxelGrid, out_dir: str, basename: str, max_dim: int,
           dry_run: bool = False) -> dict:
    os.makedirs(out_dir, exist_ok=True)
    b = g.bounds()
    if b is None:
        raise ValueError("网格为空，没有可导出的方块")
    slices = plan_slices(g, max_dim)
    files: list[dict] = []

    if len(slices) == 1:
        if not dry_run:
            g.to_mcschematic((b[0], b[1], b[2])).save(out_dir, basename, SCHEM_VERSION)
        files.append({
            "file": os.path.join(out_dir, f"{basename}.schem"),
            "bbox": list(b), "blocks": g.count(), "origin": [b[0], b[1], b[2]],
        })
    else:
        for i, box in enumerate(slices):
            name = f"{basename}_p{i + 1:02d}"
            sub = slice_grid(g, box, origin=(box[0], box[1], box[2]))
            if not dry_run:
                sub.to_mcschematic((0, 0, 0)).save(out_dir, name, SCHEM_VERSION)
            # 分片内容未必贴到该片最小角（十字/环形等稀疏平面）。
            # mcschematic 保存时按**内容最小角**归一化，所以基点必须记「内容角点」，
            # 否则按标称片区角点粘贴会整体错位（且 verify_schem 的尺寸断言会误报）。
            sb = sub.bounds()
            if sb is None:
                org, bb = [box[0], box[1], box[2]], list(box)
            else:
                org = [box[0] + sb[0], box[1] + sb[1], box[2] + sb[2]]
                bb = [org[0], org[1], org[2],
                      box[0] + sb[3], box[1] + sb[4], box[2] + sb[5]]
            files.append({
                "file": os.path.join(out_dir, f"{name}.schem"),
                "bbox": bb, "blocks": sub.count(),
                "origin": org,
            })

    return {
        "mc_version": MC_VERSION,
        "data_version": DATA_VERSION,
        "sponge_version": SPONGE_VERSION,
        "mcschematic_enum": MCS_VERSION_ENUM,
        "size": list(g.size()),
        "bbox": list(b),
        "blocks_total": g.count(),
        "slice_count": len(files),
        "slice_max_dim": max_dim,
        "outputs": files,
        "materials": g.histogram(),
    }


def run(spec_path: str, out_dir: str | None = None, dry_run: bool = False,
        max_dim: int | None = None) -> dict:
    with open(spec_path, encoding="utf-8") as f:
        spec = json.load(f)

    style_name = spec.get("style")
    try:
        style_def = load_style(str(style_name))
    except FileNotFoundError as e:
        raise SystemExit(f"[FAIL] {e}")

    ok, errors, warnings, params = validate_spec(spec, style_def)
    if warnings:
        for w in warnings:
            print(f"[WARN] {w}")
    if not ok:
        print("[FAIL] spec 校验未通过，已中止生成：")
        for e in errors:
            print(f"   X {e}")
        raise SystemExit(1)

    builder = BUILDERS.get(style_def.get("builder", style_name))
    if builder is None:
        if style_def.get("builder"):
            raise SystemExit(
                f"[FAIL] 风格 {style_name} 声明的 builder {style_def['builder']!r} 未注册")
        raise SystemExit(
            f"[FAIL] 风格 {style_name} 只声明参数、没有风格 builder（属轨道 B 专用）："
            f"请改用 scaffold.py --spec {spec_path} --out build_x.py 走轨道 B")

    t0 = time.time()
    grid = builder(params)
    t_build = time.time() - t0

    out = spec.get("output", {}) or {}
    dir_ = out_dir or out.get("dir") or f"mc_builds/{spec.get('name')}"
    dir_ = os.path.abspath(dir_)
    md = int(max_dim or out.get("slice_max_dim") or DEFAULT_MAX_DIM)
    base = out.get("basename") or spec.get("name")

    t1 = time.time()
    report = export(grid, dir_, str(base), md, dry_run=dry_run)
    t_export = time.time() - t1

    report.update({
        "ok": True,
        "name": spec.get("name"),
        "style": style_name,
        "spec": os.path.abspath(spec_path),
        "dry_run": dry_run,
        "timing": {"build_s": round(t_build, 3), "export_s": round(t_export, 3)},
    })

    if not dry_run:
        rp = os.path.join(dir_, f"{base}.report.json")
        with open(rp, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        report["report_file"] = rp

    return report


def main() -> int:
    ap = argparse.ArgumentParser(description="轨道 A：spec 直出 .schem")
    ap.add_argument("--spec", required=True, help="spec.json 路径")
    ap.add_argument("--out-dir", help="输出目录（覆盖 spec.output.dir）")
    ap.add_argument("--dry-run", action="store_true", help="只计算不写盘")
    ap.add_argument("--max-dim", type=int, help="单片包围盒边长上限（默认 256）")
    ap.add_argument("--json", action="store_true", help="打印完整报告 JSON")
    args = ap.parse_args()

    report = run(args.spec, args.out_dir, args.dry_run, args.max_dim)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    print(f"[OK] 风格 {report['style']} / {report['name']}")
    print(f"     MC {report['mc_version']}  DataVersion={report['data_version']}  Sponge v{report['sponge_version']}")
    bb = report["bbox"]
    print(f"     包围盒 x {bb[0]}..{bb[3]} | y {bb[1]}..{bb[4]} | z {bb[2]}..{bb[5]}"
          f"   → 含空气尺寸 {report['size'][0]}×{report['size'][1]}×{report['size'][2]}")
    print(f"     方块总数 {report['blocks_total']}")
    if report["slice_count"] > 1:
        print(f"     [分片] 已按 {report['slice_max_dim']}³ 阈值切成 {report['slice_count']} 片：")
        for s in report["outputs"]:
            print(f"        {os.path.basename(s['file'])}  基点 {s['origin']}  {s['blocks']} 方块")
    else:
        print(f"     输出 {os.path.basename(report['outputs'][0]['file'])}")
    print(f"     耗时 几何 {report['timing']['build_s']}s / 导出 {report['timing']['export_s']}s")
    if report.get("dry_run"):
        print("     (dry-run：未写盘)")
    print("\n     材质分布（前 12）：")
    for k, v in list(report["materials"].items())[:12]:
        print(f"        {v:>8}  {k}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
