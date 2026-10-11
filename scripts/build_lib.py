#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
build_lib.py —— 共享几何基元库（轨道 A / 轨道 B 共用）

设计要点
--------
1. **体素中间层**：几何逻辑操作 VoxelGrid（dict[坐标]->调色板索引），
   而不是直接调 mcschematic。好处：可查询/可切片/可 diff/可渲染/可去重，
   且不必为了读回一个方块而构造整个 mcschematic 对象。
2. **负坐标自由**：归一化推迟到导出时统一做（实测 mcschematic 本身支持负坐标并自动平移）。
3. **一次导入，两条轨道**：轨道 A（风格直出）与轨道 B（自定义 build()）调同一批基元，
   同一个几何错误不会被修两次。

坐标约定
--------
    x -> 宽（东西）    y -> 高（上下）    z -> 长（南北）
    与 mcschematic / Litematica 一致，已实测确认。

自检：
    python build_lib.py --selfcheck
"""

from __future__ import annotations

import argparse
import math
import os
import sys
from typing import Iterable, Sequence

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import geom_lib as geom  # noqa: E402  声明式几何合成层（本库的地基）

from palette_1_21_1 import (  # noqa: E402
    MC_VERSION, MCS_VERSION_ENUM, SPONGE_VERSION,
    door as _door, slab as _slab, split_blockstate, stairs as _stairs,
    trapdoor as _trapdoor, validate_blockstate, wall as _wall,
)

AIR = "minecraft:air"

Coord = tuple[int, int, int]


# ============================================================================
# 体素网格
# ============================================================================
class VoxelGrid:
    """方块体素容器。内部用 dict 存储（构建期灵活），导出时才转 numpy / mcschematic。"""

    __slots__ = ("_cells", "_palette", "_pal_idx", "_bbox", "_validated")

    def __init__(self, validate: bool = False):
        self._cells: dict[Coord, int] = {}
        self._palette: list[str] = [AIR]
        self._pal_idx: dict[str, int] = {AIR: 0}
        self._bbox: list[int] | None = None   # [minx,miny,minz,maxx,maxy,maxz]
        self._validated = validate

    # ---------- 单点 ----------
    def _index(self, block: str) -> int:
        i = self._pal_idx.get(block)
        if i is None:
            if self._validated:
                ok, msg = validate_blockstate(block)
                if not ok:
                    raise ValueError(f"非法方块 {block!r}：{msg}")
            i = len(self._palette)
            self._palette.append(block)
            self._pal_idx[block] = i
        return i

    def set(self, x: int, y: int, z: int, block: str) -> None:
        idx = self._index(block)
        self._cells[(x, y, z)] = idx
        self._grow(x, y, z)

    def set_if_air(self, x: int, y: int, z: int, block: str) -> None:
        if (x, y, z) not in self._cells:
            self.set(x, y, z, block)

    def get(self, x: int, y: int, z: int) -> str:
        i = self._cells.get((x, y, z))
        return AIR if i is None else self._palette[i]

    def carve(self, x: int, y: int, z: int) -> None:
        """挖空（移除方块）。"""
        self._cells.pop((x, y, z), None)

    def _grow(self, x: int, y: int, z: int) -> None:
        b = self._bbox
        if b is None:
            self._bbox = [x, y, z, x, y, z]
        else:
            if x < b[0]: b[0] = x
            if y < b[1]: b[1] = y
            if z < b[2]: b[2] = z
            if x > b[3]: b[3] = x
            if y > b[4]: b[4] = y
            if z > b[5]: b[5] = z

    # ---------- 批量 ----------
    def fill_box(self, p0: Coord, p1: Coord, block: str,
                 hollow: bool = False, thickness: int = 1) -> None:
        """实心/空心长方体（含端点）。hollow=True 时只保留 thickness 厚的壳。

        几何集合由 geom_lib.Box 显式定义（实心/空心/框架三态）；此处只负责写方块。
        """
        shape = geom.Box(p0, p1, mode="hollow" if hollow else "solid",
                         thickness=max(1, thickness))
        idx = self._index(block)
        for (x, y, z) in shape.cells():
            self._cells[(x, y, z)] = idx
        self._bbox_grow_all(*shape.bounds())

    def _bbox_grow_all(self, x0, y0, z0, x1, y1, z1) -> None:
        b = self._bbox
        if b is None:
            self._bbox = [x0, y0, z0, x1, y1, z1]
        else:
            b[0] = min(b[0], x0); b[1] = min(b[1], y0); b[2] = min(b[2], z0)
            b[3] = max(b[3], x1); b[4] = max(b[4], y1); b[5] = max(b[5], z1)

    def clear_box(self, p0: Coord, p1: Coord) -> None:
        """把长方体区域挖空（设为空气）。"""
        x0, y0, z0 = (min(p0[i], p1[i]) for i in range(3))
        x1, y1, z1 = (max(p0[i], p1[i]) for i in range(3))
        for x in range(x0, x1 + 1):
            for y in range(y0, y1 + 1):
                for z in range(z0, z1 + 1):
                    self._cells.pop((x, y, z), None)

    # ---------- 统计 / 导出 ----------
    def count(self) -> int:
        """非空气方块数。"""
        return len(self._cells)

    def bounds(self) -> tuple[int, int, int, int, int, int] | None:
        """实际方块包围盒 (x0,y0,z0,x1,y1,z1)；空网格返回 None。"""
        if not self._cells:
            return None
        xs = [k[0] for k in self._cells]
        ys = [k[1] for k in self._cells]
        zs = [k[2] for k in self._cells]
        return (min(xs), min(ys), min(zs), max(xs), max(ys), max(zs))

    def size(self) -> tuple[int, int, int]:
        """含空气包围盒尺寸 (W,H,L)。分片阈值按此计算，不按实体方块数。"""
        b = self.bounds()
        if b is None:
            return (0, 0, 0)
        return (b[3] - b[0] + 1, b[4] - b[1] + 1, b[5] - b[2] + 1)

    def palette(self) -> list[str]:
        return list(self._palette)

    def histogram(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for i in self._cells.values():
            b = self._palette[i]
            out[b] = out.get(b, 0) + 1
        return dict(sorted(out.items(), key=lambda kv: -kv[1]))

    def to_numpy(self):
        """
        导出 (arr, palette, origin)：
            arr     : uint16 三维数组，值为调色板索引，shape=(W,H,L)
            palette : 索引 -> blockstate（air 恒为 0）
            origin  : (x0,y0,z0) 基点
        """
        import numpy as np
        b = self.bounds()
        if b is None:
            return np.zeros((0, 0, 0), dtype=np.uint16), list(self._palette), (0, 0, 0)
        x0, y0, z0, x1, y1, z1 = b
        w, h, L = x1 - x0 + 1, y1 - y0 + 1, z1 - z0 + 1
        arr = np.zeros((w, h, L), dtype=np.uint16)
        for (x, y, z), i in self._cells.items():
            arr[x - x0, y - y0, z - z0] = i
        return arr, list(self._palette), (x0, y0, z0)

    def to_mcschematic(self, base: Coord | None = None):
        """转成 mcschematic 对象。base 指定放置基点（默认按包围盒归一化到 0,0,0）。"""
        import mcschematic
        schem = mcschematic.MCSchematic()
        b = self.bounds()
        if b is None:
            return schem
        ox, oy, oz = base if base is not None else (b[0], b[1], b[2])
        for (x, y, z), i in self._cells.items():
            schem.setBlock((x - ox, y - oy, z - oz), self._palette[i])
        return schem

    def copy(self) -> "VoxelGrid":
        g = VoxelGrid(validate=False)
        g._cells = dict(self._cells)
        g._palette = list(self._palette)
        g._pal_idx = dict(self._pal_idx)
        g._bbox = list(self._bbox) if self._bbox else None
        return g

    # ---------- 变换（B2） ----------
    def _recompute_bbox(self) -> None:
        self._bbox = None
        for (x, y, z) in self._cells:
            self._grow(x, y, z)

    def translate(self, dx: int, dy: int = 0, dz: int = 0) -> "VoxelGrid":
        """整体平移，返回新网格（blockstate 不变）。"""
        out = VoxelGrid()
        for (x, y, z), i in self._cells.items():
            out._cells[(x + dx, y + dy, z + dz)] = out._index(self._palette[i])
        out._recompute_bbox()
        return out

    def mirror(self, axis: str, pivot: float | None = None) -> "VoxelGrid":
        """
        关于垂直于 axis 的平面做镜像，返回新网格。

        axis：x / y / z；pivot：镜像面在该轴上的坐标，省略时取自身包围盒中面。
        **方块朝向同步翻转**（东西/南北互换、上下半互换、门铰链与楼梯拐角左右互换）。
        """
        if axis not in ("x", "y", "z"):
            raise ValueError(f"axis 只能是 x/y/z，收到 {axis!r}")
        b = self.bounds()
        if b is None:
            return self.copy()
        k = "xyz".index(axis)
        p = pivot if pivot is not None else (b[k] + b[k + 3]) / 2.0
        out = VoxelGrid()
        for (x, y, z), i in self._cells.items():
            c = [x, y, z]
            c[k] = int(round(2 * p - c[k]))
            out.set(c[0], c[1], c[2], _transform_block(self._palette[i], mirror_axis=axis))
        return out

    def rotate_90(self, times: int = 1, pivot: tuple[float, float] | None = None) -> "VoxelGrid":
        """
        绕 y 轴旋转 times×90°，返回新网格。

        俯视顺时针：(x, z) -> (-z, x)，朝向走 east -> south -> west -> north。
        pivot=(px, pz)，省略时取包围盒中心。方块朝向与 axis 属性同步旋转。
        """
        t = int(times) % 4
        if t == 0:
            return self.copy()
        b = self.bounds()
        if b is None:
            return VoxelGrid()
        if pivot is None:
            pivot = ((b[0] + b[3]) / 2.0, (b[2] + b[5]) / 2.0)
        px, pz = pivot
        out = VoxelGrid()
        for (x, y, z), i in self._cells.items():
            rx, rz = x - px, z - pz
            for _ in range(t):
                rx, rz = -rz, rx
            out.set(int(round(px + rx)), y, int(round(pz + rz)),
                    _transform_block(self._palette[i], rot=t))
        return out

    def blit(self, other: "VoxelGrid", to: Coord = (0, 0, 0),
             overwrite: bool = True) -> None:
        """把 other 整体平移 to 后并入本网格；overwrite=False 时只填空位。"""
        dx, dy, dz = to
        for (x, y, z), i in other._cells.items():
            blk = other._palette[i]
            if overwrite:
                self.set(x + dx, y + dy, z + dz, blk)
            else:
                self.set_if_air(x + dx, y + dy, z + dz, blk)


# ============================================================================
# 几何基元
# ============================================================================
def _rect_ring(x0: int, z0: int, x1: int, z1: int, thickness: int) -> Iterable[tuple[int, int]]:
    """矩形墙圈上的 (x,z) 坐标，thickness 为向内加厚的层数。

    几何集合由 geom_lib.Rect2（矩形·边框态）显式定义，逐层顺序与去重保持一致。
    """
    return geom.Rect2(x0, z0, x1, z1, max(1, thickness)).cells2()


def wall_ring(g: VoxelGrid, x0: int, z0: int, x1: int, z1: int,
              y0: int, y1: int, block: str, thickness: int = 1,
              gate: dict | None = None) -> None:
    """
    矩形墙圈（四面墙）。

    gate 形如 {"side": "south", "center": 30, "width": 5, "height": 6}
        side  : north(z1) / south(z0) / west(x0) / east(x1)
        center: 门洞在对应轴上的中心坐标
        width : 净宽（奇数更对称）
        height: 净高（从 y0 起算）
    """
    t = max(1, thickness)
    half = (gate["width"] - 1) / 2 if gate else 0
    gx0 = gx1 = gz0 = gz1 = None
    if gate:
        c, w, h = gate["center"], gate["width"], gate["height"]
        lo, hi = int(round(c - half)), int(round(c + half))
        gy1 = y0 + h - 1
        side = gate["side"]
        if side in ("south", "north"):
            gx0, gx1 = lo, hi
            gz0, gz1 = (z0, z0 + t - 1) if side == "south" else (z1 - t + 1, z1)
            gy0 = y0
        else:
            gz0, gz1 = lo, hi
            gx0, gx1 = (x0, x0 + t - 1) if side == "west" else (x1 - t + 1, x1)
            gy0 = y0
    for y in range(y0, y1 + 1):
        for (x, z) in _rect_ring(x0, z0, x1, z1, t):
            if gate and gx0 is not None and gy0 <= y <= gy1:
                if gx0 <= x <= gx1 and gz0 <= z <= gz1:
                    continue
            g.set(x, y, z, block)


def round_tower(g: VoxelGrid, cx: int, cz: int, radius: int,
                y0: int, y1: int, block: str,
                hollow: bool = True, thickness: int = 1,
                clear_interior: bool = True) -> None:
    """
    圆形塔楼（圆柱壳）。
    radius=3 -> 直径 7（外径 2r+1），与外径判定 dx²+dz² <= r² 一致。

    clear_interior：hollow 时主动**挖空**内腔。用途：塔建在已存在的墙之后时，
    把穿过塔身的墙块清掉，保证塔内是干净的空腔（否则墙体会残留其中）。

    几何集合由 geom_lib 显式定义：外壳 = Prism(Circle2, "xz") 的圆环态，
    内腔 = 同一圆的开圆盘态（严格内，不含内表面）。
    """
    t = max(1, thickness)
    inner = max(0, radius - t) if hollow else None
    geom.fill(g, geom.Prism(
        geom.Circle2(cx, cz, radius, inner=inner, inner_closed=True), "xz", y0, y1), block)
    if hollow and clear_interior:
        geom.carve(g, geom.Prism(
            geom.Circle2(cx, cz, max(0, radius - t), outer_closed=False), "xz", y0, y1))


_DIR_OPPOSITE = {"north": "south", "south": "north", "east": "west", "west": "east"}


def _radial_facing(dx: int, dz: int, slope: str) -> str:
    """
    圆锥/攒尖屋顶单格楼梯的 facing：取偏离圆心的主导轴方向。

    实测依据（client-1.21.1-official.jar）：
      1) vanilla 定义：stairs 的 facing = "the direction the stairs' full-block side faces"，
         即**完整方块那一半（高侧）所在方向 = 上坡方向**，下坡方向是它的反方向；
      2) blockstates/oak_stairs.json 中 `facing=east` 无旋转，其模型高半在 x∈[8,16]（东侧），
         与 1) 的定义一致，构成双向交叉验证。

    slope="outward"：下坡朝外（外低内高，标准锥顶）→ facing 逐格指向圆心
    slope="inward" ：下坡朝内（外高内低）      → facing 逐格指向外侧
    """
    if abs(dx) >= abs(dz):
        outward = "east" if dx > 0 else "west"
    else:
        outward = "south" if dz > 0 else "north"
    return _DIR_OPPOSITE[outward] if slope == "outward" else outward


def cone_roof(g: VoxelGrid, cx: int, cz: int, radius: int, y0: int,
              block: str, slope: str = "outward", hollow: bool = True) -> int:
    """
    锥形/攒尖屋顶：每向上一格半径收缩 1，收到 0 为止。
    返回屋顶最高层的 y。

    每格楼梯的 facing 按径向逐格计算（见 _radial_facing），整圈不再共用一个朝向：
        slope="outward" : 下坡朝外（外低内高，标准锥顶）
        slope="inward"  : 下坡朝内（外高内低）—— 若游戏里看坡向反了，改这个开关即可
    """
    assert slope in ("outward", "inward"), "slope 只能是 outward / inward"
    y = y0
    r = radius
    is_stairs = "stairs" in block
    while r >= 0:
        if r == 0:
            g.set(cx, y, cz, block)
        else:
            r2 = r * r
            in2 = (r - 1) * (r - 1) if r > 1 else -1
            for dx in range(-r, r + 1):
                for dz in range(-r, r + 1):
                    d2 = dx * dx + dz * dz
                    if d2 > r2:
                        continue
                    if hollow and r > 1 and d2 < in2:
                        continue
                    b = (_stairs_with_facing(block, _radial_facing(dx, dz, slope))
                         if is_stairs else block)
                    g.set(cx + dx, y, cz + dz, b)
        y += 1
        r -= 1
    return y - 1


def _stairs_with_facing(block: str, facing: str) -> str:
    base = block.split("[")[0]
    return _stairs(base, facing=facing, half="bottom")


def _oriented(block: str, facing: str) -> str:
    """楼梯类按 facing 定向（facing = 上坡方向 = 完整方块那一半所在方向）；其它方块原样返回。"""
    return _stairs_with_facing(block, facing) if "stairs" in block else block


# ---------------------------------------------------------------------------
# B1 屋顶族
#   统一约定（与 cone_roof 一致，已对照 client-1.21.1-official.jar 验证）：
#     * stairs 的 facing = 完整方块（高侧）所在方向 = 上坡方向，下坡是它的反方向
#     * +z = south，-z = north（Minecraft 世界坐标系）
#     * 屋顶只生成"屋面壳"，不填充屋面下方实体（要实心请自行 fill_box）
# ---------------------------------------------------------------------------
def gable_roof(g: VoxelGrid, x0: int, z0: int, x1: int, z1: int, y0: int,
               block: str, ridge_axis: str = "z", overhang: int = 0,
               gable_block: str | None = None) -> int:
    """
    双坡（人字）屋顶：沿 ridge_axis 一条连续脊线，垂直方向两侧对称下坡。

    ridge_axis="z"：脊线沿 z（南北），向 ±x 下坡
    ridge_axis="x"：脊线沿 x（东西），向 ±z 下坡
    overhang     ：四边各外挑格数（挑檐）；外挑部分落在檐口最低层
    gable_block  ：给定时在**原始**脊线两端补山墙三角（檐口铺到屋面下方），
                   不填则两端留空，由调用方自行砌墙

    返回脊线所在 y。楼梯 facing 指向脊线（上坡），下坡恒朝外。
    """
    if ridge_axis not in ("x", "z"):
        raise ValueError(f"ridge_axis 只能是 'x' / 'z'，收到 {ridge_axis!r}")
    ov = max(0, int(overhang))
    a, c, b, d = x0 - ov, z0 - ov, x1 + ov, z1 + ov
    if ridge_axis == "z":
        slo, shi = a, b                     # 坡向轴 = x
        rlo, rhi = c, d                     # 脊线轴 = z
        pos_dir, neg_dir = "east", "west"   # 坡向轴正/负方向
    else:
        slo, shi = c, d                     # 坡向轴 = z
        rlo, rhi = a, b                     # 脊线轴 = x
        pos_dir, neg_dir = "south", "north"

    for s in range(slo, shi + 1):
        h = min(s - slo, shi - s)
        facing = pos_dir if (s - slo) <= (shi - s) else neg_dir
        blk = _oriented(block, facing)
        y = y0 + h
        for t in range(rlo, rhi + 1):
            if ridge_axis == "z":
                g.set(s, y, t, blk)
            else:
                g.set(t, y, s, blk)

    if gable_block:
        g0, g1 = (z0, z1) if ridge_axis == "z" else (x0, x1)
        for gz in (g0, g1):
            for s in range(slo, shi + 1):
                h = min(s - slo, shi - s)
                for y in range(y0, y0 + h):
                    if ridge_axis == "z":
                        g.set(s, y, gz, gable_block)
                    else:
                        g.set(gz, y, s, gable_block)
    return y0 + (shi - slo) // 2


def hip_roof(g: VoxelGrid, x0: int, z0: int, x1: int, z1: int, y0: int,
             block: str, overhang: int = 0) -> int:
    """
    四坡（庑殿）屋顶：四边同时向内收，短轴先收成正脊，长轴收成一条脊线；
    长宽相等时收成一格尖顶。只生成屋面壳。

    返回最高层 y。楼梯 facing 指向屋面内侧（上坡）。
    """
    ov = max(0, int(overhang))
    X0, Z0, X1, Z1 = x0 - ov, z0 - ov, x1 + ov, z1 + ov
    k = 0
    while True:
        a, c = X0 + k, Z0 + k
        b, d = X1 - k, Z1 - k
        if a > b or c > d:
            break
        y = y0 + k
        for (x, z) in _rect_ring(a, c, b, d, 1):
            if x == a:
                facing = "east"
            elif x == b:
                facing = "west"
            elif z == c:
                facing = "south"     # z 最小侧 = north 面，上坡朝 +z
            else:
                facing = "north"
            g.set(x, y, z, _oriented(block, facing))
        k += 1
    return y0 + k - 1


def barrel_vault(g: VoxelGrid, x0: int, z0: int, x1: int, z1: int, y0: int,
                 block: str, axis: str = "z", thickness: int = 1) -> int:
    """
    筒拱（半圆拱顶）：沿 axis 方向拉出的半圆柱壳。

    axis="z"：拱截面在 x-y 平面、沿 z 延伸，半径由 x 跨度决定
    axis="x"：拱截面在 z-y 平面、沿 x 延伸，半径由 z 跨度决定
    thickness：拱壳厚度（1 = 单层）

    返回拱顶最高层 y。楼梯 facing 指向拱顶中线（上坡）。
    """
    if axis not in ("x", "z"):
        raise ValueError(f"axis 只能是 'x' / 'z'，收到 {axis!r}")
    t = max(1, int(thickness))
    if axis == "z":
        lo, hi, rlo, rhi = x0, x1, z0, z1
    else:
        lo, hi, rlo, rhi = z0, z1, x0, x1
    r = (hi - lo) // 2
    if r < 1:
        raise ValueError("筒拱跨度至少需要 3 格（半径 ≥ 1）")
    c = lo + r
    in2 = (r - t) ** 2
    for d in range(-r, r + 1):
        for dy in range(0, r + 1):
            if not (in2 < d * d + dy * dy <= r * r):
                continue
            dx, dz = (d, 0) if axis == "z" else (0, d)
            blk = _oriented(block, _radial_facing(dx, dz, "outward"))
            y = y0 + dy
            for t2 in range(rlo, rhi + 1):
                if axis == "z":
                    g.set(c + d, y, t2, blk)
                else:
                    g.set(t2, y, c + d, blk)
    return y0 + r


def dome(g: VoxelGrid, cx: int, cz: int, radius: int, y0: int, block: str,
         thickness: int = 1) -> int:
    """
    半球穹顶壳：球心 (cx, y0, cz)，只保留 y ≥ y0 的上半球。

    radius    ：外半径
    thickness ：壳厚（1 = 单层）

    返回穹顶最高层 y。楼梯 facing 水平指向中轴（上坡）。

    几何集合由 geom_lib.Ball 显式定义（球壳 + y 区间裁剪 = 部分球面）。
    """
    r = int(radius)
    if r < 1:
        raise ValueError("穹顶半径至少为 1")
    t = max(1, int(thickness))
    for (x, y, z) in geom.Ball(cx, y0, cz, r, inner=r - t, inner_closed=False,
                              y0=y0, y1=y0 + r).cells():
        g.set(x, y, z, _oriented(block, _radial_facing(x - cx, z - cz, "outward")))
    return y0 + r


def battlements(g: VoxelGrid, x0: int, z0: int, x1: int, z1: int, y: int,
                block: str, period: int = 2, offset: int = 0,
                thickness: int = 1, exclude=None) -> None:
    """
    城垛（雉堞）：沿墙圈每隔 period 格放一个垛块，其余留空。
    period=2 -> 经典"一实一空"。
    用 (x+z+offset) % period 判定，保证四个角与各边连续。

    exclude(x, z) -> bool：返回 True 的位置跳过（例如落在塔楼圆柱内的垛块）
    """
    t = max(1, thickness)
    for (x, z) in _rect_ring(x0, z0, x1, z1, t):
        if exclude is not None and exclude(x, z):
            continue
        if (x + z + offset) % period == 0:
            g.set(x, y, z, block)


def battlements_solid(g: VoxelGrid, x0: int, z0: int, x1: int, z1: int,
                      y: int, block: str, thickness: int = 1) -> None:
    """实心压顶（垛墙基座），通常放在 battlements 下一层。"""
    for (x, z) in _rect_ring(x0, z0, x1, z1, max(1, thickness)):
        g.set(x, y, z, block)


def arch_gate(g: VoxelGrid, x0: int, x1: int, y0: int, y1: int, z: int,
              block: str, depth: int = 1, arch_ratio: float = 0.5) -> None:
    """
    拱形门洞：矩形下部 + 半圆拱上部。

    x0..x1 为门洞净跨，y0..y1 为净高，z 为门所在平面（沿 depth 加厚）。
    拱起点 = 门洞净高的 (1-arch_ratio) 处。

    几何集合由 geom_lib 显式定义并**合成**：
        下部矩形  ∪  (半圆 ∩ 净高矩形)   —— 对应"矩形与圆的拼接/取交"。
    """
    dep = int(depth)
    if dep <= 0:
        return
    span = x1 - x0
    radius = span / 2.0
    cx = (x0 + x1) / 2.0
    arch_h = max(1, int(round(span * arch_ratio / 2)))
    arch_y0 = y1 - arch_h

    clip = geom.Rect2(x0, y0, x1, y1)                       # 净跨 × 净高
    arc = geom.Circle2(cx, arch_y0 + radius, radius) & clip
    if arch_y0 >= y0:
        region = geom.Rect2(x0, y0, x1, arch_y0) | arc
    else:
        region = arc
    geom.carve(g, geom.Prism(region, "xy", z, z + dep - 1))


def carve_gate(g: VoxelGrid, side: str, center: int, width: int, height: int,
               wall_coord: int, depth: int = 1, arch: bool = True) -> None:
    """
    在墙面上挖出门洞（可拱形收口）。

    side       : south / north / west / east
    center     : 门洞在墙面延伸方向上的中心坐标
    width      : 净宽（格）
    height     : 净高（从 y=0 起算，拱顶收口在最上层）
    wall_coord : 墙面所在的固定坐标（south/west 为最小那侧）
    depth      : 墙厚（挖穿层数）
    arch       : True = 上半做半圆拱；False = 纯矩形门洞

    拱的圆心取 y = height-1-r，使最高一层正好收口到 1 格宽，拱形自然。

    几何集合由 geom_lib 显式定义并**合成**：
        矩形（y ≤ cy 部分）∪ (拱圆 ∩ y > cy 部分)，再沿墙厚方向拉伸。
    """
    assert side in ("south", "north", "west", "east"), f"side 非法：{side}"
    lo = center - (width - 1) // 2
    hi = lo + width - 1
    r = width / 2.0
    cx = (lo + hi) / 2.0
    cy = max(0.0, (height - 1) - r)
    dep = max(1, int(depth))

    if arch:
        cyi = int(math.floor(cy))                # y ≤ cy 的整数部分
        region = geom.Rect2(lo, 0, hi, cyi)      # 矩形下部
        if cyi + 1 <= height - 1:                # 拱上部（圆 ∩ 净高矩形）
            region = region | (geom.Circle2(cx, cy, r) & geom.Rect2(lo, cyi + 1, hi, height - 1))
    else:
        region = geom.Rect2(lo, 0, hi, height - 1)

    if side in ("south", "west"):
        solid = geom.Prism(region, "xy" if side == "south" else "yz",
                           wall_coord, wall_coord + dep - 1)
    else:
        solid = geom.Prism(region, "xy" if side == "north" else "yz",
                           wall_coord - dep + 1, wall_coord)
    geom.carve(g, solid)


def pillar(g: VoxelGrid, x: int, z: int, y0: int, y1: int, block: str,
           radius: int = 0) -> None:
    """柱：radius=0 为 1×1，radius=1 为 3×3（圆），依此类推。"""
    if radius == 0:
        for y in range(min(y0, y1), max(y0, y1) + 1):
            g.set(x, y, z, block)
        return
    round_tower(g, x, z, radius, y0, y1, block, hollow=False)


def fill_ground(g: VoxelGrid, x0: int, z0: int, x1: int, z1: int, y: int,
                block: str, thickness: int = 1) -> None:
    """地坪/地基。"""
    for x in range(min(x0, x1), max(x0, x1) + 1):
        for z in range(min(z0, z1), max(z0, z1) + 1):
            for dy in range(thickness):
                g.set(x, y - dy, z, block)


# ============================================================================
# B2 变换族：blockstate 朝向随坐标变换同步重写
#   坐标约定（Minecraft 世界坐标）：+x = east，-x = west，+z = south，-z = north
# ============================================================================
_FACING_CW = {"north": "east", "east": "south", "south": "west", "west": "north"}
_HALF_FLIP = {"top": "bottom", "bottom": "top", "upper": "lower", "lower": "upper"}
_LR_FLIP = {"left": "right", "right": "left"}
_SHAPE_FLIP = {
    "inner_left": "inner_right", "inner_right": "inner_left",
    "outer_left": "outer_right", "outer_right": "outer_left",
}


def _mirror_props(props: dict, axis: str) -> dict:
    """镜像时重写方向属性：x 轴 → 东西互换，z 轴 → 南北互换，y 轴 → 上下半/台阶位互换。"""
    out = dict(props)
    f = out.get("facing")
    if axis == "x" and f in ("east", "west"):
        out["facing"] = "west" if f == "east" else "east"
    elif axis == "z" and f in ("north", "south"):
        out["facing"] = "south" if f == "north" else "north"
    if axis == "y":
        if out.get("half") in _HALF_FLIP:
            out["half"] = _HALF_FLIP[out["half"]]
        if out.get("type") in _HALF_FLIP:
            out["type"] = _HALF_FLIP[out["type"]]
    if out.get("hinge") in _LR_FLIP:
        out["hinge"] = _LR_FLIP[out["hinge"]]
    if out.get("shape") in _SHAPE_FLIP:
        out["shape"] = _SHAPE_FLIP[out["shape"]]
    return out


def _rotate_props(props: dict, times: int) -> dict:
    """绕 y 轴旋转时重写方向属性：facing 走 east→south→west→north，axis 的 x/z 互换。"""
    t = int(times) % 4
    out = dict(props)
    if t == 0:
        return out
    f = out.get("facing")
    if f in _FACING_CW:
        for _ in range(t):
            f = _FACING_CW[f]
        out["facing"] = f
    if t % 2 == 1 and out.get("axis") in ("x", "z"):
        out["axis"] = "z" if out["axis"] == "x" else "x"
    return out


def _transform_block(block: str, *, mirror_axis: str | None = None, rot: int = 0) -> str:
    """
    按坐标变换重写 blockstate 字符串；无方向属性的方块原样返回。

    stairs 的 shape（内/外拐角）：**镜像**下左右互换，**旋转**下保持不变
    —— 形状是相对 facing 定义的，刚体旋转不改变相对关系。
    """
    base, props = split_blockstate(block)
    if not props:
        return block
    if mirror_axis:
        props = _mirror_props(props, mirror_axis)
    if int(rot) % 4:
        props = _rotate_props(props, rot)
    return base + "[" + ",".join(f"{k}={v}" for k, v in props.items()) + "]"


# ============================================================================
# B3 开口 / 细节族
#   side / wall_coord 的约定与 carve_gate 完全一致：
#     south = z 最小那一面，墙身向 +z 延伸；north = z 最大那一面，墙身向 -z 延伸
#     west  = x 最小那一面；east = x 最大那一面
#   d 为「从墙面往墙体内部」的层号（d=0 就是墙面本身）
# ============================================================================
_FACE_SIDES = ("south", "north", "west", "east")
_H_DIRS = {"north": (0, 0, -1), "south": (0, 0, 1), "east": (1, 0, 0), "west": (-1, 0, 0)}
_DIR_VEC = {"north": (0, -1), "south": (0, 1), "east": (1, 0), "west": (-1, 0)}


def _face_xyz(side: str, wall_coord: int, u: int, y: int, d: int) -> Coord:
    """墙面局部坐标 (沿墙方向 u, 高度 y, 深入墙体的层号 d) -> 世界坐标。"""
    if side == "south":
        return (u, y, wall_coord + d)
    if side == "north":
        return (u, y, wall_coord - d)
    if side == "west":
        return (wall_coord + d, y, u)
    return (wall_coord - d, y, u)


def _face_outward(side: str) -> str:
    """墙面**朝外**的法向（墙体外侧指向的方向）。"""
    return {"south": "north", "north": "south", "west": "west", "east": "east"}[side]


def _check_side(side: str) -> None:
    if side not in _FACE_SIDES:
        raise ValueError(f"side 只能是 {_FACE_SIDES} 之一，收到 {side!r}")


def window_run(g: VoxelGrid, side: str, wall_coord: int, start: int, y0: int,
               count: int = 1, spacing: int = 2, width: int = 1, height: int = 2,
               depth: int = 1, glass_block: str = "minecraft:glass",
               frame_block: str | None = None, sill_block: str | None = None) -> None:
    """
    沿墙面开一排窗（窗洞 + 玻璃 + 可选窗框 / 窗台）。

    count 个窗，首个起点 start，间距 spacing；depth = 墙厚（挖穿层数）。
    玻璃贴在最外侧一层、内侧留空腔（凹窗）；frame_block 铺窗洞外圈一圈；
    sill_block 铺窗洞下一层并左右各多 1 格（窗台优先于窗框，会盖掉窗框底边）。
    """
    _check_side(side)
    if width < 1 or height < 1:
        raise ValueError("width / height 至少为 1")
    dep = max(1, int(depth))
    for i in range(int(count)):
        lo = start + i * (width + max(0, int(spacing)))
        for d in range(dep):
            for u in range(lo, lo + width):
                for y in range(y0, y0 + height):
                    g.carve(*_face_xyz(side, wall_coord, u, y, d))
        for u in range(lo, lo + width):
            for y in range(y0, y0 + height):
                g.set(*_face_xyz(side, wall_coord, u, y, 0), glass_block)
        if frame_block:
            for u in range(lo - 1, lo + width + 1):
                for y in (y0 - 1, y0 + height):
                    g.set(*_face_xyz(side, wall_coord, u, y, 0), frame_block)
            for y in range(y0 - 1, y0 + height + 1):
                for u in (lo - 1, lo + width):
                    g.set(*_face_xyz(side, wall_coord, u, y, 0), frame_block)
        if sill_block:
            for u in range(lo - 1, lo + width + 1):
                g.set(*_face_xyz(side, wall_coord, u, y0 - 1, 0), sill_block)


def door_frame(g: VoxelGrid, side: str, wall_coord: int, center: int, y0: int = 0,
               width: int = 1, height: int = 2, depth: int = 1,
               door_block: str | None = None, frame_block: str | None = None,
               hinge: str = "left", door_facing: str | None = None) -> None:
    """
    开门洞；width==1 且 height>=2 且给了 door_block 时，顺带放两格门（下半 + 上半）。

    door_facing 省略时取「墙面朝外的法向」。门的 facing 语义与楼梯不同（不是上坡方向），
    若游戏里看到门开错边，显式传 door_facing 翻转即可。
    """
    _check_side(side)
    if width < 1 or height < 1:
        raise ValueError("width / height 至少为 1")
    lo = center - (width - 1) // 2
    for d in range(max(1, int(depth))):
        for u in range(lo, lo + width):
            for y in range(y0, y0 + height):
                g.carve(*_face_xyz(side, wall_coord, u, y, d))
    if frame_block:
        for u in range(lo - 1, lo + width + 1):
            for y in (y0 - 1, y0 + height):
                g.set(*_face_xyz(side, wall_coord, u, y, 0), frame_block)
        for y in range(y0 - 1, y0 + height + 1):
            for u in (lo - 1, lo + width):
                g.set(*_face_xyz(side, wall_coord, u, y, 0), frame_block)
    if door_block and width == 1 and height >= 2:
        facing = door_facing or _face_outward(side)
        g.set(*_face_xyz(side, wall_coord, lo, y0, 0),
              _door(door_block, facing=facing, half="lower", hinge=hinge))
        g.set(*_face_xyz(side, wall_coord, lo, y0 + 1, 0),
              _door(door_block, facing=facing, half="upper", hinge=hinge))


def stairs_flight(g: VoxelGrid, x0: int, z0: int, y0: int, steps: int, block: str,
                  dir: str = "south", width: int = 1,
                  under_block: str | None = None) -> int:
    """
    直跑楼梯：自 (x0,z0,y0) 起，每上一级沿 dir 前进 1 格、升高 1 格。

    dir 既是上行方向也是楼梯 facing；width 为垂直于 dir 的梯段宽度。
    under_block 给定时把每级下方填实成三角（否则只放楼梯本身，下面空）。
    返回最高一级的 y。
    """
    if dir not in _DIR_VEC:
        raise ValueError(f"dir 只能是 {_DIR_VEC} 之一，收到 {dir!r}")
    steps = max(1, int(steps))
    dx, dz = _DIR_VEC[dir]
    wv = (0, 1) if dz else (1, 0)          # 宽度方向垂直于前进方向
    blk = _oriented(block, dir)
    for k in range(steps):
        y = y0 + k
        bx, bz = x0 + dx * k, z0 + dz * k
        for w in range(max(1, int(width))):
            cx, cz = bx + wv[0] * w, bz + wv[1] * w
            if under_block:
                for yy in range(y0, y):
                    g.set(cx, yy, cz, under_block)
            g.set(cx, y, cz, blk)
    return y0 + steps - 1


def buttress(g: VoxelGrid, side: str, wall_coord: int, u: int, y0: int, y1: int,
             block: str, depth: int = 1, width: int = 1, taper: int = 0) -> None:
    """
    扶壁：贴在墙面**外侧**的竖向扶垛。

    depth = 向外挑出格数；width = 沿墙方向宽度；
    taper = 底部逐层加宽量（最底外挑 depth+taper，向上递减回 depth，做出收分）。
    """
    _check_side(side)
    lo, hi = min(y0, y1), max(y0, y1)
    for y in range(lo, hi + 1):
        extra = max(0, int(taper) - (y - lo))
        for d in range(1, max(1, int(depth)) + extra + 1):   # d 取负 = 往墙外
            for w in range(max(1, int(width))):
                g.set(*_face_xyz(side, wall_coord, u + w, y, -d), block)


def railing_run(g: VoxelGrid, p0: tuple[int, int], p1: tuple[int, int], y: int,
                post_block: str, rail_block: str | None = None,
                post_every: int = 2, height: int = 1) -> None:
    """
    直线栏杆：沿 p0→p1 逐格铺 height 层。

    p0/p1 是 (x, z) 平面坐标，y 为栏杆底高。线段由 geom_lib.Segment3
    （**参数方程** P(t)=p0+t·(p1-p0)）体素化，因此**不再要求轴对齐** ——
    任意方向的直线栏杆都可生成（轴对齐时逐格与旧版完全一致）。

    每 post_every 格（含两端）用 post_block 做立柱，其余用 rail_block（省略则同 post_block）。
    栅栏 / 玻璃板类方块请在其后统一调用 connect_fence_like(g) 补齐连接属性。
    """
    seg = geom.Segment3((p0[0], y, p0[1]), (p1[0], y, p1[1]))
    cells = [(x, z) for (x, _yy, z) in seg.iter_voxels()]
    pe, last = max(1, int(post_every)), len(cells) - 1
    for i, (x, z) in enumerate(cells):
        blk = post_block if (i % pe == 0 or i == last) else (rail_block or post_block)
        for k in range(max(1, int(height))):
            g.set(x, y + k, z, blk)


def eave_band(g: VoxelGrid, x0: int, z0: int, x1: int, z1: int, y: int, block: str,
              projection: int = 1, thickness: int = 1,
              upper_block: str | None = None, upper_inset: int = 1) -> None:
    """
    腰线 / 挑檐带：在 y 处沿矩形墙圈铺一圈水平带，可向外挑出 projection 格。

    upper_block 给定时，在 y+1 再铺一圈「内收 upper_inset」的次级带（两级线脚）。
    """
    ov = max(0, int(projection))
    for (x, z) in _rect_ring(x0 - ov, z0 - ov, x1 + ov, z1 + ov, max(1, int(thickness))):
        g.set(x, y, z, block)
    if upper_block:
        ov2 = max(0, ov - max(0, int(upper_inset)))
        for (x, z) in _rect_ring(x0 - ov2, z0 - ov2, x1 + ov2, z1 + ov2, 1):
            g.set(x, y + 1, z, upper_block)


_FENCE_LIKE_EXACT = ("iron_bars",)


def _is_fence_like(base: str) -> bool:
    b = base.removeprefix("minecraft:")
    return b.endswith("_fence") or b.endswith("_pane") or b in _FENCE_LIKE_EXACT


def connect_fence_like(g: VoxelGrid) -> int:
    """
    扫描全网格，为栅栏 / 玻璃板 / 铁栏杆按**四邻同类方块**补齐 north/east/south/west
    连接属性，返回被改写的格子数。

    为什么必须做：这些方块在 .schem 里是**状态显式存储**的，属性全 false 时游戏里表现为
    孤零零的柱子、不会自动相连。作图请在最后统一调用一次。
    只连「同类方块」，不连普通实心墙（保守，避免把墙体误判成连到栅栏）。
    """
    changed = 0
    for (x, y, z) in list(g._cells.keys()):
        base, props = split_blockstate(g.get(x, y, z))
        if not _is_fence_like(base):
            continue
        touched = False
        for d, (dx, dy, dz) in _H_DIRS.items():
            nb = split_blockstate(g.get(x + dx, y + dy, z + dz))[0]
            want = "true" if _is_fence_like(nb) else "false"
            if props.get(d) != want:
                props[d] = want
                touched = True
        if touched:
            g.set(x, y, z, base + "[" + ",".join(f"{k}={v}" for k, v in props.items()) + "]")
            changed += 1
    return changed


# ============================================================================
# B4 材质族：**确定性**选材 / 做旧
#   绝不使用随机数 —— diff 门禁要求「其余部分逐点不变」，
#   同一坐标必须永远得到同一结果，否则迭代会被判定为越界变更。
# ============================================================================
def _hash01(x: int, y: int, z: int, salt: int = 0) -> float:
    """坐标哈希 -> [0,1) 的确定性伪随机（同坐标恒同值）。"""
    h = ((int(x) * 73856093) ^ (int(y) * 19349663) ^ (int(z) * 83492791)
         ^ (int(salt) * 2654435761)) & 0xFFFFFFFF
    h ^= h >> 15
    h = (h * 2246822519) & 0xFFFFFFFF
    h ^= h >> 13
    h = (h * 3266489917) & 0xFFFFFFFF
    h ^= h >> 16
    return (h & 0xFFFFFF) / float(0x1000000)


def hash_pick(x: int, y: int, z: int, choices: Sequence[str], salt: int = 0) -> str:
    """按坐标哈希从 choices 里确定性取一个方块（分布≈均分，同坐标恒同结果）。"""
    if not choices:
        raise ValueError("choices 不能为空")
    return choices[int(_hash01(x, y, z, salt) * len(choices))]


def weather_blend(x: int, y: int, z: int, base: str, aged: str, y0: int, y1: int,
                  ratio: float = 0.35, salt: int = 0) -> str:
    """
    做旧渐变：越靠近 y0 越旧。ratio = y0 处替换成 aged 的概率，线性衰减到 y1 处为 0。
    同一 (x,y,z) 恒返回同一结果（无随机数），可安全参与 diff 门禁。
    """
    if y1 <= y0:
        p = float(ratio) if y >= y0 else 0.0
    else:
        t = (y - y0) / float(y1 - y0)
        p = float(ratio) * max(0.0, min(1.0, 1.0 - t))
    return aged if _hash01(x, y, z, salt) < p else base


# ============================================================================
# 分片
# ============================================================================
DEFAULT_MAX_DIM = 256   # 单片含空气包围盒边长上限


def _axis_starts(lo: int, hi: int, max_dim: int, overlap: int) -> list[int]:
    """
    单轴分片起点：保证 [lo, hi] 被**完整覆盖**，相邻片留 overlap 格重叠。

    注意不能用 ceil(长度 / max_dim) 当片数：步长是 max_dim - overlap，
    片数必须由"覆盖到 hi 为止"决定，否则会出现静默丢列/丢层。
    """
    step = max(1, max_dim - max(1, overlap))
    starts = [lo]
    while starts[-1] + max_dim <= hi:
        starts.append(starts[-1] + step)
    return starts


def plan_slices(g: VoxelGrid, max_dim: int = DEFAULT_MAX_DIM,
                overlap: int = 1) -> list[tuple[int, int, int, int, int, int]]:
    """
    规划分片。**按含空气包围盒尺寸判定**（不是实体方块数）——
    Litematica 客户端加载压力来自包围盒，空心塔与实心塔同样卡。

    三轴（x/y/z）都参与切分：任一轴超限即切，保证每片包围盒三边 ≤ max_dim；
    切分轴上留 overlap 格重叠避免接缝，且覆盖完整（不丢列、不丢层）。

    返回 [(x0,y0,z0,x1,y1,z1), ...]，顺序为 x → y → z（y 未超限时与旧版 x→z 顺序一致）。
    """
    b = g.bounds()
    if b is None:
        return []
    x0, y0, z0, x1, y1, z1 = b
    w, h, L = x1 - x0 + 1, y1 - y0 + 1, z1 - z0 + 1
    if w <= max_dim and h <= max_dim and L <= max_dim:
        return [b]

    out: list[tuple[int, int, int, int, int, int]] = []
    for sx in _axis_starts(x0, x1, max_dim, overlap):
        ex = min(x1, sx + max_dim - 1)
        for sy in _axis_starts(y0, y1, max_dim, overlap):
            ey = min(y1, sy + max_dim - 1)
            for sz in _axis_starts(z0, z1, max_dim, overlap):
                ez = min(z1, sz + max_dim - 1)
                out.append((sx, sy, sz, ex, ey, ez))
    return out


def slice_grid(g: VoxelGrid, box: tuple[int, int, int, int, int, int],
               origin: Coord | None = None) -> VoxelGrid:
    """按包围盒切出一个子网格。origin 给定时以该点为基点，否则用 box 左下。"""
    x0, y0, z0, x1, y1, z1 = box
    sub = VoxelGrid()
    for (x, y, z), i in g._cells.items():
        if x0 <= x <= x1 and y0 <= y <= y1 and z0 <= z <= z1:
            sub._cells[(x, y, z)] = sub._index(g._palette[i])
    if origin is not None:
        ox, oy, oz = origin
        moved = {}
        for (x, y, z), i in sub._cells.items():
            moved[(x - ox, y - oy, z - oz)] = i
        sub._cells = moved
    sub._bbox = None
    for (x, y, z) in sub._cells:
        sub._grow(x, y, z)
    return sub


# ============================================================================
# 自检
# ============================================================================
def _selfcheck() -> int:
    print(f"build_lib 自检（MC {MC_VERSION} / DataVersion 见 palette）")
    fails: list[str] = []

    def expect(cond: bool, label: str) -> None:
        print(f"  {'OK  ' if cond else '[X] '} {label}")
        if not cond:
            fails.append(label)

    # 1) 长方体
    g = VoxelGrid()
    g.fill_box((0, 0, 0), (4, 4, 4), "minecraft:stone_bricks")
    expect(g.count() == 125, f"实心立方 5³ = {g.count()} (期望 125)")
    expect(g.size() == (5, 5, 5), f"尺寸 {g.size()}")

    # 2) 空心
    g2 = VoxelGrid()
    g2.fill_box((0, 0, 0), (4, 4, 4), "minecraft:stone_bricks", hollow=True)
    expect(g2.count() == 125 - 27, f"空心立方 = {g2.count()} (期望 98)")

    # 3) 圆塔半径
    g3 = VoxelGrid()
    round_tower(g3, 0, 0, 3, 0, 0, "minecraft:stone_bricks", hollow=False)
    span = g3.bounds()
    expect(span[0] == -3 and span[3] == 3, f"半径3 -> x 跨度 {span[0]}..{span[3]} (期望 -3..3，即直径7)")

    # 4) 空心圆塔：中心必须是空气
    g4 = VoxelGrid()
    round_tower(g4, 0, 0, 3, 0, 0, "minecraft:stone_bricks", hollow=True)
    expect(g4.get(0, 0, 0) == AIR, "空心圆塔中心为空气")
    expect(g4.get(3, 0, 0) == "minecraft:stone_bricks", "圆塔外壁 (3,0,0) 为方块")

    # 5) 墙圈 + 城门挖空
    g5 = VoxelGrid()
    wall_ring(g5, 0, 0, 20, 20, 0, 5, "minecraft:stone_bricks",
              gate={"side": "south", "center": 10, "width": 5, "height": 4})
    hole_ok = all(g5.get(x, 1, 0) == AIR for x in range(8, 13))
    expect(hole_ok, "城门 x=8..12 在 z=0 面全高镂空")
    expect(g5.get(0, 1, 0) == "minecraft:stone_bricks", "城门旁墙仍在")

    # 6) 城垛交替
    g6 = VoxelGrid()
    battlements(g6, 0, 0, 20, 20, 6, "minecraft:stone_bricks", period=2)
    has = {g6.get(x, 6, 0) for x in range(21)}
    expect(has == {AIR, "minecraft:stone_bricks"}, f"城垛存在实/空交替 {has}")

    # 7) 拱门
    g7 = VoxelGrid()
    g7.fill_box((0, 0, 0), (10, 14, 2), "minecraft:stone_bricks")
    arch_gate(g7, 3, 7, 0, 8, 0, "minecraft:stone_bricks", depth=1)
    expect(g7.get(5, 0, 0) == AIR, "拱门底部中线被挖空")
    expect(g7.get(3, 0, 0) == AIR, "拱门左侧被挖空")
    expect(g7.get(2, 0, 0) != AIR, "拱门外实心仍保留")
    expect(g7.get(5, 13, 0) != AIR, "拱门上方实心仍保留")

    # 8) 分片规划：片数（y 未超限时与旧版 x→z 顺序一致）
    g8 = VoxelGrid()
    g8.fill_box((0, 0, 0), (1000, 10, 300), "minecraft:stone_bricks")
    slices = plan_slices(g8, max_dim=256)
    expect(len(slices) == math.ceil(1001 / 255) * math.ceil(301 / 255),
           f"1001x11x301 分片数 = {len(slices)}")

    # 9) 分片覆盖完整 + 单片不超限
    #    回归断言：旧版 ① 宽 512 时丢 x=511 整列（步长 255 而片数按 ceil 算）
    #              ② 高度超 256 时完全不切 Y（只沿 XZ 切）
    def slice_problem(sl: list[tuple[int, int, int, int, int, int]],
                      box: tuple[int, int, int, int, int, int], md: int) -> str | None:
        lo, hi = (box[0], box[1], box[2]), (box[3], box[4], box[5])
        for ax in range(3):
            cur = lo[ax]
            for a, b in sorted((s[ax], s[ax + 3]) for s in sl):
                if a > cur:
                    return f"轴{'xyz'[ax]} 出现缺口 {cur}..{a - 1}"
                cur = max(cur, b + 1)
                if cur > hi[ax]:
                    break
            if cur <= hi[ax]:
                return f"轴{'xyz'[ax]} 未覆盖到 {cur}..{hi[ax]}"
        for s in sl:
            if any(s[i + 3] - s[i] + 1 > md for i in range(3)):
                return f"分片超限 {s}"
        return None

    for box, label in (
        ((-300, 0, 0, 300, 2, 2), "宽度 601（负坐标起点）"),
        ((0, 0, 0, 511, 2, 2), "宽度 512（旧版丢列）"),
        ((0, 0, 0, 10, 399, 10), "高度 400（旧版不切 Y）"),
        ((0, 0, 0, 511, 399, 300), "三轴同时超限"),
    ):
        gg = VoxelGrid()
        gg.fill_box(box[:3], box[3:], "minecraft:stone_bricks")
        sl = plan_slices(gg, max_dim=256)
        problem = slice_problem(sl, box, 256)
        expect(problem is None,
               f"分片覆盖完整且不超限：{label}（{len(sl)} 片）" + (f"  <- {problem}" if problem else ""))

    # 9b) 分片切出后内容非空
    sub = slice_grid(g8, slices[0])
    expect(sub.count() > 0, f"首片含方块 {sub.count()}")

    # 10) numpy 往返
    arr, pal, org = g.to_numpy()
    expect(arr.shape == (5, 5, 5), f"numpy shape {arr.shape}")
    back = VoxelGrid()
    for x in range(arr.shape[0]):
        for y in range(arr.shape[1]):
            for z in range(arr.shape[2]):
                back.set(x + org[0], y + org[1], z + org[2], pal[arr[x, y, z]])
    expect(back.count() == g.count(), f"numpy 往返方块数一致 {back.count()} == {g.count()}")

    # 11) 锥形屋顶封顶
    g11 = VoxelGrid()
    top = cone_roof(g11, 0, 0, 3, 0, "minecraft:dark_oak_stairs", slope="outward")
    expect(g11.get(0, top, 0) != AIR, f"锥顶最高层 y={top} 有方块")
    expect(top == 3, f"半径3 的锥顶高度 = {top} (期望 3)")

    # 12) 锥顶楼梯朝向按径向逐格计算
    #     回归断言：旧版整圈共用一个 facing（实测 17 格全 north），3/4 坡面是反的
    def stair_facing(gx: VoxelGrid, x: int, y: int, z: int) -> str | None:
        b = gx.get(x, y, z)
        if "facing=" not in b:
            return None
        return b.split("facing=")[1].split(",")[0].rstrip("]")

    dirs = {stair_facing(g11, x, y, z) for (x, y, z) in g11._cells}
    dirs.discard(None)
    expect(dirs == {"north", "south", "east", "west"}, f"锥顶楼梯朝向覆盖四向 {sorted(dirs)}")
    expect(stair_facing(g11, 0, 0, -3) == "south" and stair_facing(g11, 0, 0, 3) == "north"
           and stair_facing(g11, 3, 0, 0) == "west" and stair_facing(g11, -3, 0, 0) == "east",
           "锥顶 slope=outward：最外圈 facing 指向圆心（下坡朝外）")
    g12c = VoxelGrid()
    cone_roof(g12c, 0, 0, 3, 0, "minecraft:dark_oak_stairs", slope="inward")
    expect(stair_facing(g12c, 0, 0, -3) == "north" and stair_facing(g12c, 0, 0, 3) == "south",
           "锥顶 slope=inward：facing 整体翻转（下坡朝内）")

    # 13) 越界判定：写非法方块（开启校验时应抛错）
    g12 = VoxelGrid(validate=True)
    try:
        g12.set(0, 0, 0, "minecraft:not_a_real_block")
        expect(False, "validate=True 应拦住非法方块")
    except ValueError:
        expect(True, "validate=True 拦住非法方块")

    # 14) B1 屋顶族 · 双坡（人字）屋顶
    g14 = VoxelGrid()
    top14 = gable_roof(g14, 0, 0, 11, 7, 0, "minecraft:dark_oak_stairs", ridge_axis="z")
    expect(top14 == 5, f"双坡 12x8 脊线 y = {top14} (期望 5)")
    expect(g14.get(5, 5, 3) != AIR and g14.get(6, 5, 3) != AIR, "双坡偶宽：脊线为相邻两列")
    expect(g14.get(0, 0, 3) != AIR and g14.get(11, 0, 3) != AIR, "双坡檐口落在两侧最低层")
    expect(g14.get(3, 3, 3) != AIR and g14.get(3, 4, 3) == AIR, "双坡屋面随坡向逐格升高、其上无多余方块")
    expect(stair_facing(g14, 0, 0, 3) == "east" and stair_facing(g14, 11, 0, 3) == "west",
           "双坡 facing 指向脊线（上坡）")
    expect(sum(1 for (_, yy, _) in g14._cells if yy == 5) == 2 * 8, "双坡顶脊恰为 2 列 × 脊线长")

    g15 = VoxelGrid()
    gable_roof(g15, 0, 0, 11, 7, 2, "minecraft:dark_oak_stairs",
               ridge_axis="z", gable_block="minecraft:stone_bricks")
    expect(g15.get(5, 6, 0) == "minecraft:stone_bricks" and g15.get(5, 7, 0) != "minecraft:stone_bricks",
           "山墙三角填到屋面下方、不覆盖屋面")
    expect(g15.get(0, 2, 0) != "minecraft:stone_bricks", "檐口处山墙高度为 0（不填）")

    g16 = VoxelGrid()
    top16 = gable_roof(g16, 0, 0, 11, 7, 0, "minecraft:dark_oak_stairs",
                       ridge_axis="z", overhang=2)
    expect(top16 == 7, f"带挑檐双坡脊线 y = {top16} (期望 7)")
    expect(g16.get(-2, 0, 3) != AIR and g16.get(13, 0, 3) != AIR, "挑檐延伸到外扩边界的最低层")
    expect(g16.get(0, 0, 3) == AIR and g16.get(0, 2, 3) != AIR, "挑檐使墙线处屋面已升高 2 格")

    g17 = VoxelGrid()
    gable_roof(g17, 0, 0, 7, 11, 0, "minecraft:dark_oak_stairs", ridge_axis="x")
    expect(g17.get(3, 5, 5) != AIR and g17.get(3, 5, 6) != AIR, "脊线沿 x 时最高层落在 z 中轴")
    expect(stair_facing(g17, 3, 0, 0) == "south" and stair_facing(g17, 3, 0, 11) == "north",
           "脊线沿 x 时 facing 指向 z 向脊线")

    g18 = VoxelGrid()
    gable_roof(g18, 0, 0, 5, 3, 0, "minecraft:stone_bricks", ridge_axis="z")
    expect(list(g18.histogram()) == ["minecraft:stone_bricks"], "非楼梯方块不附加 facing")

    # 15) B1 屋顶族 · 四坡（庑殿）屋顶
    g19 = VoxelGrid()
    top19 = hip_roof(g19, 0, 0, 11, 7, 0, "minecraft:dark_oak_stairs")
    expect(top19 == 3, f"四坡 12x8 收成正脊 y = {top19} (期望 3)")
    expect(g19.get(5, 0, 3) == AIR, "四坡首层只放檐口圈、内部为空气（壳）")
    expect(g19.get(0, 0, 0) != AIR and g19.get(11, 0, 7) != AIR, "四坡檐口四角闭合")
    expect(g19.get(5, 3, 3) != AIR and g19.get(5, 3, 4) != AIR and g19.get(5, 4, 3) == AIR,
           "四坡长轴先收成一条正脊、脊上无多余方块")
    expect(stair_facing(g19, 5, 0, 0) == "south" and stair_facing(g19, 0, 0, 3) == "east"
           and stair_facing(g19, 11, 0, 3) == "west" and stair_facing(g19, 5, 0, 7) == "north",
           "四坡四面 facing 各自指向屋面内侧")

    g20 = VoxelGrid()
    top20 = hip_roof(g20, 0, 0, 6, 6, 0, "minecraft:dark_oak_stairs")
    expect(top20 == 3, f"四坡 7x7 收成尖顶 y = {top20} (期望 3)")
    expect(g20.get(3, 3, 3) != AIR and g20.get(2, 3, 3) == AIR and g20.get(4, 3, 3) == AIR,
           "正方形四坡顶端收敛为一格尖顶")

    g21 = VoxelGrid()
    hip_roof(g21, 0, 0, 11, 7, 0, "minecraft:dark_oak_stairs", overhang=1)
    expect(g21.get(-1, 0, -1) != AIR and g21.get(12, 0, 8) != AIR, "四坡挑檐落到外扩边界")

    # 16) B1 屋顶族 · 筒拱
    g22 = VoxelGrid()
    top22 = barrel_vault(g22, 0, 0, 6, 9, 0, "minecraft:dark_oak_stairs", axis="z")
    expect(top22 == 3, f"筒拱半径3 拱顶 y = {top22} (期望 3)")
    expect(g22.get(3, 3, 5) != AIR and g22.get(0, 0, 5) != AIR and g22.get(6, 0, 5) != AIR,
           "筒拱截面为半圆：拱顶 1 格、起拱两侧各 1 格")
    expect(g22.get(3, 0, 5) == AIR, "筒拱截面内部为空气（壳）")
    expect(g22.get(3, 3, 0) != AIR and g22.get(3, 3, 9) != AIR, "筒拱沿轴向贯通全长")
    expect(stair_facing(g22, 0, 0, 5) == "east" and stair_facing(g22, 6, 0, 5) == "west",
           "筒拱 facing 指向拱顶中线")

    g23 = VoxelGrid()
    barrel_vault(g23, 0, 0, 9, 6, 0, "minecraft:dark_oak_stairs", axis="x")
    expect(g23.get(5, 3, 3) != AIR and g23.get(5, 0, 0) != AIR and g23.get(5, 0, 6) != AIR,
           "筒拱 axis='x' 时截面落在 z-y 平面")
    expect(g23.get(5, 3, 4) == AIR, "筒拱 axis='x' 拱顶只有一行")

    # 17) B1 屋顶族 · 半球穹顶
    g24 = VoxelGrid()
    top24 = dome(g24, 0, 0, 3, 0, "minecraft:dark_oak_stairs")
    expect(top24 == 3, f"穹顶半径3 顶点 y = {top24} (期望 3)")
    expect(g24.get(0, 3, 0) != AIR, "穹顶顶点存在")
    expect(all(g24.get(x, 0, z) != AIR for (x, z) in ((3, 0), (-3, 0), (0, 3), (0, -3))),
           "穹顶基座环闭合")
    expect(g24.get(0, 0, 0) == AIR and g24.get(4, 0, 0) == AIR, "穹顶内部与半径外均为空气")
    expect(g24.get(0, -1, 0) == AIR, "穹顶不含下半球")
    expect(stair_facing(g24, 3, 0, 0) == "west" and stair_facing(g24, -3, 0, 0) == "east"
           and stair_facing(g24, 0, 0, 3) == "north" and stair_facing(g24, 0, 0, -3) == "south",
           "穹顶 facing 指向中轴")

    # 18) 参数边界：非法 axis 必须报错
    for call, label in (
        (lambda: gable_roof(VoxelGrid(), 0, 0, 4, 4, 0, "minecraft:stone_bricks", ridge_axis="y"),
         "gable_roof 非法 ridge_axis"),
        (lambda: barrel_vault(VoxelGrid(), 0, 0, 4, 4, 0, "minecraft:stone_bricks", axis="y"),
         "barrel_vault 非法 axis"),
        (lambda: dome(VoxelGrid(), 0, 0, 0, 0, "minecraft:stone_bricks"),
         "dome 半径 < 1"),
    ):
        try:
            call()
            expect(False, f"{label} 应报错")
        except (ValueError, AssertionError):
            expect(True, f"{label} 已报错")

    # 19) B2 变换族：平移 / 镜像 / 旋转 / 拼接
    def cells_map(gg: VoxelGrid) -> dict:
        return {(x, y, z): gg.get(x, y, z) for (x, y, z) in gg._cells}

    def props_of(gg: VoxelGrid, x: int, y: int, z: int) -> dict:
        return split_blockstate(gg.get(x, y, z))[1]

    g25 = VoxelGrid()
    g25.fill_box((0, 0, 0), (4, 2, 4), "minecraft:stone_bricks")
    g25.set(0, 3, 0, _stairs("minecraft:oak_stairs", facing="east"))
    tr = g25.translate(10, 5, -3)
    expect(tr.bounds() == (10, 5, -3, 14, 8, 1), f"平移后包围盒 {tr.bounds()}")
    expect(tr.count() == g25.count(), "平移不改变方块数")

    expect(cells_map(g25.mirror("x").mirror("x")) == cells_map(g25), "镜像两次 = 恒等（含朝向）")
    expect(cells_map(g25.rotate_90(4)) == cells_map(g25), "旋转 4×90° = 恒等（含朝向）")
    expect(g25.mirror("x", pivot=0).get(0, 3, 0) == _stairs("minecraft:oak_stairs", facing="west"),
           "镜像 x 轴：facing east -> west")
    expect(g25.rotate_90(1, pivot=(0, 0)).get(0, 3, 0)
           == _stairs("minecraft:oak_stairs", facing="south"),
           "旋转 90°：facing east -> south")

    g26 = VoxelGrid()
    g26.set(0, 0, 0, "minecraft:oak_log[axis=x]")
    expect(g26.rotate_90(1, pivot=(0, 0)).get(0, 0, 0) == "minecraft:oak_log[axis=z]",
           "旋转 90°：log axis x -> z")

    g27 = VoxelGrid()
    g27.set(0, 0, 0, _slab("minecraft:stone_brick_slab", "bottom"))
    expect(g27.mirror("y", pivot=0).get(0, 0, 0) == _slab("minecraft:stone_brick_slab", "top"),
           "镜像 y 轴：slab bottom -> top")

    g28 = VoxelGrid()
    g28.set(0, 0, 0, "minecraft:stone")
    g28.blit(g28.translate(5, 0, 0))
    expect(g28.count() == 2, f"blit 拼接后方块数 {g28.count()} (期望 2)")

    g29 = VoxelGrid()
    g29.set(1, 0, 0, "minecraft:stone")
    g29.set(2, 0, 0, "minecraft:stone")
    g29.blit(g29.mirror("x", pivot=1))
    expect(g29.get(0, 0, 0) != AIR and g29.count() == 3,
           "pivot 镜像：半筑 x∈[1..2] 拼成 x∈[0..2] 的对称体")

    # 20) B3 细节族：窗带
    g30 = VoxelGrid()
    g30.fill_box((0, 0, 0), (10, 5, 2), "minecraft:stone_bricks")
    window_run(g30, "south", 0, start=2, y0=2, count=2, spacing=2, width=1, height=2,
               glass_block="minecraft:glass", frame_block="minecraft:stone_bricks",
               sill_block="minecraft:stone_brick_slab")
    expect(g30.get(2, 2, 0) == "minecraft:glass" and g30.get(5, 3, 0) == "minecraft:glass",
           "窗带：两扇窗洞都贴上玻璃")
    expect(g30.get(1, 2, 0) == "minecraft:stone_bricks" and g30.get(3, 2, 0) == "minecraft:stone_bricks",
           "窗带：窗框铺满窗洞外圈")
    expect(g30.get(2, 1, 0) == "minecraft:stone_brick_slab", "窗带：窗台铺在窗洞下一层")
    expect(g30.get(0, 2, 0) == "minecraft:stone_bricks", "窗带：窗户外侧墙体未被破坏")

    # 21) B3 细节族：门洞
    g31 = VoxelGrid()
    g31.fill_box((0, 0, 0), (8, 5, 2), "minecraft:stone_bricks")
    door_frame(g31, "south", 0, center=4, y0=0, width=1, height=3,
               door_block="minecraft:oak_door", frame_block="minecraft:stone_bricks")
    expect(g31.get(4, 0, 0) == _door("minecraft:oak_door", facing="north", half="lower")
           and g31.get(4, 1, 0) == _door("minecraft:oak_door", facing="north", half="upper"),
           "门洞：放下半+上半两格门，facing 取墙外法向")
    expect(g31.get(4, 2, 0) == AIR, "门洞：门以上仍镂空")

    # 22) B3 细节族：直跑梯 / 扶壁 / 腰线
    g32 = VoxelGrid()
    top32 = stairs_flight(g32, 0, 0, 0, steps=4, block="minecraft:oak_stairs",
                          dir="south", width=1, under_block="minecraft:stone_bricks")
    expect(top32 == 3, f"直跑梯最高一级 y = {top32} (期望 3)")
    expect(g32.get(0, 3, 3) == _stairs("minecraft:oak_stairs", facing="south"),
           "直跑梯：逐级升高且 facing = 上行方向")
    expect(g32.get(0, 0, 3) == "minecraft:stone_bricks", "直跑梯：under_block 把梯段下方填实")

    g33 = VoxelGrid()
    g33.fill_box((0, 0, 0), (6, 6, 2), "minecraft:stone_bricks")
    buttress(g33, "south", 0, u=3, y0=0, y1=4, block="minecraft:cobblestone",
             depth=1, width=1, taper=2)
    expect(g33.get(3, 0, -1) != AIR and g33.get(3, 0, -2) != AIR, "扶壁：向墙外挑出、底部按 taper 加宽")
    expect(g33.get(3, 4, -1) != AIR and g33.get(3, 4, -2) == AIR, "扶壁：上部收回到基础挑出深度")
    expect(g33.get(3, 3, 0) == "minecraft:stone_bricks", "扶壁：不破坏墙体本身")

    g35 = VoxelGrid()
    eave_band(g35, 0, 0, 6, 6, y=5, block="minecraft:stone_bricks",
              projection=1, thickness=2, upper_block="minecraft:stone_brick_slab")
    expect(g35.get(-1, 5, -1) != AIR, "腰线：外挑到 projection 边界")
    expect(g35.get(0, 5, 0) == "minecraft:stone_bricks",
           "腰线：thickness>=projection+1 时带与墙线连成一体")
    expect(g35.get(3, 5, 3) == AIR, "腰线：只铺环带、不填内部")
    expect(g35.get(0, 6, 0) == "minecraft:stone_brick_slab", "腰线：次级线脚在 y+1 内收一圈")

    # 23) B3 细节族：栏杆 + 栅栏连接属性补齐
    g34 = VoxelGrid()
    railing_run(g34, (0, 0), (6, 0), y=5, post_block="minecraft:oak_fence", post_every=2)
    expect(g34.count() == 7, f"栏杆铺满 0..6 共 {g34.count()} 格")
    n_conn = connect_fence_like(g34)
    expect(n_conn == 7, f"connect_fence_like 改写格数 {n_conn} (期望 7)")
    expect(props_of(g34, 1, 5, 0) == {"north": "false", "south": "false", "east": "true", "west": "true"},
           "connect_fence_like：中间格东西两侧都连上")
    expect(props_of(g34, 0, 5, 0) == {"north": "false", "south": "false", "east": "true", "west": "false"},
           "connect_fence_like：端头格只连一侧")
    expect(connect_fence_like(g34) == 0, "connect_fence_like 幂等（再跑一次无改动）")

    # 24) B4 材质族：确定性选材 / 做旧（无随机数）
    def mottle(salt: int) -> VoxelGrid:
        gg = VoxelGrid()
        for x in range(12):
            for y in range(3):
                for z in range(12):
                    gg.set(x, y, z, hash_pick(x, y, z, [
                        "minecraft:stone", "minecraft:mossy_cobblestone"], salt=salt))
        return gg

    expect(cells_map(mottle(7)) == cells_map(mottle(7)), "hash_pick：两次生成逐点相同（可安全 diff）")
    expect(cells_map(mottle(7)) != cells_map(mottle(8)), "hash_pick：不同 salt 得到不同花纹")
    picks = {hash_pick(x, 0, 0, ["a", "b", "c", "d"]) for x in range(200)}
    expect(picks == {"a", "b", "c", "d"}, f"hash_pick：200 个坐标覆盖全部候选 {sorted(picks)}")
    lo_aged = sum(1 for x in range(400) if weather_blend(x, 0, 0, "B", "A", 0, 20, 0.5) == "A")
    hi_aged = sum(1 for x in range(400) if weather_blend(x, 20, 0, "B", "A", 0, 20, 0.5) == "A")
    expect(lo_aged > hi_aged and hi_aged == 0,
           f"weather_blend：近地更旧（y0 {lo_aged} 处做旧 > y1 {hi_aged} 处）")

    print()
    if fails:
        print(f"[FAIL] {len(fails)} 项未通过：")
        for f in fails:
            print(f"   - {f}")
        return 1
    print("[OK] build_lib 全部自检通过")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="共享几何基元库")
    ap.add_argument("--selfcheck", action="store_true", help="跑内置自检")
    ap.add_argument("--palette", action="store_true", help="打印版本与调色板摘要")
    args = ap.parse_args()
    if args.palette:
        from palette_1_21_1 import PALETTE
        print(f"MC {MC_VERSION} / mcschematic.Version.{MCS_VERSION_ENUM} / Sponge v{SPONGE_VERSION}")
        for k, v in PALETTE.items():
            print(f"  [{k}] {len(v)} 项")
        return 0
    return _selfcheck()


if __name__ == "__main__":
    raise SystemExit(main())
