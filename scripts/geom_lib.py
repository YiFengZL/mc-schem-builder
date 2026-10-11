#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
geom_lib.py —— 声明式几何合成层（build_lib 的地基）

设计原则
--------
1. **隐式定义**：每个几何体由成员判定 ``contains(x, y, z) -> bool`` 唯一确定；
   体素化 = 在包围盒内逐格代入判定（整数坐标即体素中心，与既有基元一致）。
2. **布尔合成**：几何体之间用 并 ``|`` / 交 ``&`` / 差 ``-`` 组合 —— 对应
   「建筑 = 若干几何体的拼接与合成」。
3. **只回答归属**：本层不涉及材质、朝向、连接属性（那是语义基元层 build_lib
   B1–B5 的职责）。所以它可以被自由复用、组合、求交。
4. **与既有基元逐点等价**：本层先把 build_lib 里**已经隐式存在**的几何体显式化，
   自检里对每个都做「与原先写死的判定逐格相同」的回归断言 —— 显式化不改变任何产物。

坐标
----
    x = 宽(东西)   y = 高    z = 长(南北)，可为负（导出时统一归一化）。
    二维图元用平面坐标 (u, v)，由 ``Prism`` 映射到世界轴：
        plane="xz" -> (u,v)=(x,z)，沿 y 拉伸
        plane="xy" -> (u,v)=(x,y)，沿 z 拉伸
        plane="yz" -> (u,v)=(z,y)，沿 x 拉伸

已显式化的几何体（原先隐式散落在 build_lib / gen_schem 各处）
------------------------------------------------------------
    Segment3    直线段（**参数方程** P(t)=p0+t·(p1-p0)）  ← railing_run / stairs_flight
    Circle2     闭合圆（圆盘 / 圆环）                      ← round_tower / cone_roof / dome 截面
    Ellipse2    椭圆                                       ← build_bridge 拱券 / carve_gate 拱券
    Rect2       矩形（实心 / 边框）                        ← _rect_ring / wall_ring / 压顶
    Ball        球面（球 / 球壳 / **部分球面**）            ← dome
    Box         长方体（实心 / 空心 / 框架）               ← fill_box
    HalfSpace   以**法向量**定义的半空间                    ← 屋顶坡面（屋面高度场=两半空间之交）

自检：
    python geom_lib.py --selfcheck
"""

from __future__ import annotations

import argparse
import math
import os
import sys
from typing import Iterator, Sequence

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

BBox = tuple[int, int, int, int, int, int]      # 闭区间整数包围盒
Point = tuple[float, float, float]


def _floor(v: float) -> int:
    return int(math.floor(v))


def _ceil(v: float) -> int:
    return int(math.ceil(v))


def _iround(v: float) -> int:
    """四舍五入（半值向上），避免 Python round 的 banker's rounding 造成坐标抖动。"""
    return int(math.floor(v + 0.5))


def _bbox_union(a: BBox, b: BBox) -> BBox:
    return (min(a[0], b[0]), min(a[1], b[1]), min(a[2], b[2]),
            max(a[3], b[3]), max(a[4], b[4]), max(a[5], b[5]))


def _bbox_intersect(a: BBox, b: BBox) -> BBox:
    return (max(a[0], b[0]), max(a[1], b[1]), max(a[2], b[2]),
            min(a[3], b[3]), min(a[4], b[4]), min(a[5], b[5]))


def _point_seg_dist2(p: Point, a: Point, b: Point) -> float:
    """点 p 到线段 ab 的距离平方（用于把直线/空间曲线体素化成管）。"""
    dx, dy, dz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
    L2 = dx * dx + dy * dy + dz * dz
    if L2 == 0:
        return (p[0] - a[0]) ** 2 + (p[1] - a[1]) ** 2 + (p[2] - a[2]) ** 2
    t = ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy + (p[2] - a[2]) * dz) / L2
    t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
    px, py, pz = a[0] + dx * t, a[1] + dy * t, a[2] + dz * t
    return (p[0] - px) ** 2 + (p[1] - py) ** 2 + (p[2] - pz) ** 2


# ============================================================================
# 基类 + 布尔合成
# ============================================================================
class Solid:
    """几何体：由隐式判定 contains(x,y,z) 唯一确定（包围盒只是求值范围）。"""

    def contains(self, x: int, y: int, z: int) -> bool:
        raise NotImplementedError

    def bounds(self) -> BBox:
        raise NotImplementedError

    # ---- 布尔合成 ----
    def __or__(self, other: "Solid") -> "Solid":
        return Union(self, other)

    def __and__(self, other: "Solid") -> "Solid":
        return Intersect(self, other)

    def __sub__(self, other: "Solid") -> "Solid":
        return Difference(self, other)

    def cells(self) -> Iterator[tuple[int, int, int]]:
        """遍历包围盒内所有属于本几何体的整格（含空气包围盒，不含空气格）。"""
        x0, y0, z0, x1, y1, z1 = self.bounds()
        for x in range(x0, x1 + 1):
            for y in range(y0, y1 + 1):
                for z in range(z0, z1 + 1):
                    if self.contains(x, y, z):
                        yield (x, y, z)


class Union(Solid):
    """并集：A ∪ B（拼接）。"""

    def __init__(self, a: Solid, b: Solid):
        self.a, self.b = a, b

    def contains(self, x, y, z): return self.a.contains(x, y, z) or self.b.contains(x, y, z)
    def bounds(self): return _bbox_union(self.a.bounds(), self.b.bounds())


class Intersect(Solid):
    """交集：A ∩ B（裁剪 / 取共同部分）。"""

    def __init__(self, a: Solid, b: Solid):
        self.a, self.b = a, b

    def contains(self, x, y, z): return self.a.contains(x, y, z) and self.b.contains(x, y, z)
    def bounds(self): return _bbox_intersect(self.a.bounds(), self.b.bounds())


class Difference(Solid):
    """差集：A − B（挖空）。"""

    def __init__(self, a: Solid, b: Solid):
        self.a, self.b = a, b

    def contains(self, x, y, z): return self.a.contains(x, y, z) and not self.b.contains(x, y, z)
    def bounds(self): return self.a.bounds()


def fill(g, solid: Solid, block: str) -> None:
    """把几何体体素化后填入网格（g 只需有 .set 方法）。"""
    for (x, y, z) in solid.cells():
        g.set(x, y, z, block)


def carve(g, solid: Solid) -> None:
    """把几何体体素化后从网格挖空（g 只需有 .carve 方法）。"""
    for (x, y, z) in solid.cells():
        g.carve(x, y, z)


# ============================================================================
# 直线段（参数方程）
# ============================================================================
class Segment3(Solid):
    """
    空间直线段，**参数方程**：P(t) = p0 + t·(p1 − p0)，t ∈ [0, 1]。

    radius <= 0：单格细线，用 DDA 沿 t 采样逐格（``iter_voxels()`` 为准，保序、去重）；
    radius > 0 ：管状加粗，contains = 到线段的距离 ≤ radius。

    说明：直线段在体素上是「一格宽」的线，用参数采样得到相邻格最多差 1（26 邻接），
    比 y=kx+b 形式更稳（垂直线、任意斜率都不退化），也便于逐格铺立柱/栏杆。
    """

    def __init__(self, p0: Point, p1: Point, radius: float = 0.0):
        self.p0 = (float(p0[0]), float(p0[1]), float(p0[2]))
        self.p1 = (float(p1[0]), float(p1[1]), float(p1[2]))
        self.radius = float(radius)

    def point_at(self, t: float) -> Point:
        ax, ay, az = self.p0
        bx, by, bz = self.p1
        return (ax + (bx - ax) * t, ay + (by - ay) * t, az + (bz - az) * t)

    def iter_voxels(self) -> Iterator[tuple[int, int, int]]:
        """沿参数 t 采样得到的整格序列（自 p0 指向 p1，保序、不重复）。"""
        ax, ay, az = self.p0
        bx, by, bz = self.p1
        dx, dy, dz = bx - ax, by - ay, bz - az
        n = max(abs(dx), abs(dy), abs(dz))
        if n == 0:
            yield (_iround(ax), _iround(ay), _iround(az))
            return
        k = int(math.ceil(n))
        seen: set[tuple[int, int, int]] = set()
        for i in range(k + 1):
            t = i / k
            v = (_iround(ax + dx * t), _iround(ay + dy * t), _iround(az + dz * t))
            if v not in seen:
                seen.add(v)
                yield v

    def _dist2(self, x: float, y: float, z: float) -> float:
        return _point_seg_dist2((x, y, z), self.p0, self.p1)

    def contains(self, x, y, z) -> bool:
        r = self.radius if self.radius > 0 else 0.5
        return self._dist2(x, y, z) <= r * r + 1e-9

    def bounds(self) -> BBox:
        m = self.radius if self.radius > 0 else 0.5
        return (_floor(min(self.p0[0], self.p1[0]) - m),
                _floor(min(self.p0[1], self.p1[1]) - m),
                _floor(min(self.p0[2], self.p1[2]) - m),
                _ceil(max(self.p0[0], self.p1[0]) + m),
                _ceil(max(self.p0[1], self.p1[1]) + m),
                _ceil(max(self.p0[2], self.p1[2]) + m))


# ============================================================================
# 二维图元（平面内），用 Prism 拉伸成三维
# ============================================================================
class Shape2:
    """平面图形：由 contains2(u,v) 唯一确定；可布尔合成后再用 Prism 拉伸。"""

    def contains2(self, u: int, v: int) -> bool:
        raise NotImplementedError

    def bounds2(self) -> tuple[int, int, int, int]:
        raise NotImplementedError

    def __or__(self, other: "Shape2") -> "Shape2":
        return Union2(self, other)

    def __and__(self, other: "Shape2") -> "Shape2":
        return Intersect2(self, other)

    def __sub__(self, other: "Shape2") -> "Shape2":
        return Difference2(self, other)

    def cells2(self) -> Iterator[tuple[int, int]]:
        u0, v0, u1, v1 = self.bounds2()
        for u in range(u0, u1 + 1):
            for v in range(v0, v1 + 1):
                if self.contains2(u, v):
                    yield (u, v)


class Union2(Shape2):
    """平面图形并集。"""

    def __init__(self, a: Shape2, b: Shape2):
        self.a, self.b = a, b

    def contains2(self, u, v): return self.a.contains2(u, v) or self.b.contains2(u, v)
    def bounds2(self):
        A, B = self.a.bounds2(), self.b.bounds2()
        return (min(A[0], B[0]), min(A[1], B[1]), max(A[2], B[2]), max(A[3], B[3]))


class Intersect2(Shape2):
    """平面图形交集。"""

    def __init__(self, a: Shape2, b: Shape2):
        self.a, self.b = a, b

    def contains2(self, u, v): return self.a.contains2(u, v) and self.b.contains2(u, v)
    def bounds2(self):
        A, B = self.a.bounds2(), self.b.bounds2()
        return (max(A[0], B[0]), max(A[1], B[1]), min(A[2], B[2]), min(A[3], B[3]))


class Difference2(Shape2):
    """平面图形差集。"""

    def __init__(self, a: Shape2, b: Shape2):
        self.a, self.b = a, b

    def contains2(self, u, v): return self.a.contains2(u, v) and not self.b.contains2(u, v)
    def bounds2(self): return self.a.bounds2()


class Rect2(Shape2):
    """
    平面内**矩形**。thickness=0 为实心矩形；t≥1 为边框（自外向内 t 层的框）。

    边框的成员判定有闭式：格点到最近边的距离（inset）≤ t−1。
    ``cells2()`` 保留与旧 ``_rect_ring`` 完全一致的**逐层顺序 + 去重**。
    """

    def __init__(self, u0: int, v0: int, u1: int, v1: int, thickness: int = 0):
        self.u0, self.u1 = int(min(u0, u1)), int(max(u0, u1))
        self.v0, self.v1 = int(min(v0, v1)), int(max(v0, v1))
        self.t = max(0, int(thickness))

    def contains2(self, u: int, v: int) -> bool:
        if not (self.u0 <= u <= self.u1 and self.v0 <= v <= self.v1):
            return False
        if self.t == 0:
            return True
        inset = min(u - self.u0, self.u1 - u, v - self.v0, self.v1 - v)
        return inset <= self.t - 1

    def bounds2(self) -> tuple[int, int, int, int]:
        return (self.u0, self.v0, self.u1, self.v1)

    def cells2(self) -> Iterator[tuple[int, int]]:
        if self.t == 0:
            for u in range(self.u0, self.u1 + 1):
                for v in range(self.v0, self.v1 + 1):
                    yield (u, v)
            return
        seen: set[tuple[int, int]] = set()
        for layer in range(self.t):
            au0, av0, au1, av1 = self.u0 + layer, self.v0 + layer, self.u1 - layer, self.v1 - layer
            if au0 > au1 or av0 > av1:
                break
            for u in range(au0, au1 + 1):
                for v in (av0, av1):
                    if (u, v) not in seen:
                        seen.add((u, v)); yield (u, v)
            for v in range(av0, av1 + 1):
                for u in (au0, au1):
                    if (u, v) not in seen:
                        seen.add((u, v)); yield (u, v)


class Circle2(Shape2):
    """
    平面内**闭合圆**（圆盘 / 圆环）。

    成员判定（d² = (u−cu)² + (v−cv)²）：
        实心圆盘        inner=None            ，d² ≤ r²
        圆环            inner=ri              ，ri² {≤,<} d² ≤ r²
    inner_closed 决定内边界归属（仅差 d² == ri² 这一圈）：
        True  -> ri² ≤ d²  （round_tower 约定：内表面属实体）
        False -> ri² <  d² （dome 约定：内表面属空腔）
    outer_closed=False 时外边界取严格小于（用于挖「开圆盘」空腔）。
    """

    def __init__(self, cu: float, cv: float, r: float,
                 inner: float | None = None, inner_closed: bool = True,
                 outer_closed: bool = True):
        self.cu, self.cv, self.r = float(cu), float(cv), float(r)
        self.inner = None if inner is None else float(inner)
        self.inner_closed = bool(inner_closed)
        self.outer_closed = bool(outer_closed)

    def contains2(self, u: int, v: int) -> bool:
        d2 = (u - self.cu) ** 2 + (v - self.cv) ** 2
        r2 = self.r * self.r
        if self.outer_closed:
            if d2 > r2:
                return False
        elif d2 >= r2:
            return False
        if self.inner is not None:
            i2 = self.inner * self.inner
            return d2 >= i2 if self.inner_closed else d2 > i2
        return True

    def bounds2(self) -> tuple[int, int, int, int]:
        return (_ceil(self.cu - self.r), _ceil(self.cv - self.r),
                _floor(self.cu + self.r), _floor(self.cv + self.r))


class Ellipse2(Shape2):
    """
    平面内**椭圆**：(u−cu)²/ru² + (v−cv)²/rv² ≤ 1。

    inner 给出时（0<inner<1）为环，按比例内缩，成员判定 q{≤,<}inner²。
    """

    def __init__(self, cu: float, cv: float, ru: float, rv: float,
                 inner: float | None = None, inner_closed: bool = True):
        self.cu, self.cv = float(cu), float(cv)
        self.ru, self.rv = float(ru), float(rv)
        self.inner = None if inner is None else float(inner)
        self.inner_closed = bool(inner_closed)

    def contains2(self, u: int, v: int) -> bool:
        if self.ru == 0 or self.rv == 0:
            return False
        du, dv = (u - self.cu) / self.ru, (v - self.cv) / self.rv
        q = du * du + dv * dv
        if q > 1.0:
            return False
        if self.inner is not None:
            i2 = self.inner * self.inner
            return q >= i2 if self.inner_closed else q > i2
        return True

    def bounds2(self) -> tuple[int, int, int, int]:
        return (_ceil(self.cu - self.ru), _ceil(self.cv - self.rv),
                _floor(self.cu + self.ru), _floor(self.cv + self.rv))


class Parallelogram2(Shape2):
    """
    平面内**平行四边形**（剪切矩形）：行 v∈[v0,v1] 的 u 区间 = [u0,u1] 逐行平移 slant 格。

    slant=0 即矩形；slant>0 表示每升高一行向 +u 偏 slant 格（倾斜/斜切）。
    典型用途：斜墙、单坡、倾斜体块（配 Prism 沿第三轴拉伸）。
    """

    def __init__(self, u0: int, v0: int, u1: int, v1: int, slant: int = 0):
        self.u0, self.u1 = int(min(u0, u1)), int(max(u0, u1))
        self.v0, self.v1 = int(min(v0, v1)), int(max(v0, v1))
        self.slant = int(slant)

    def contains2(self, u: int, v: int) -> bool:
        if not (self.v0 <= v <= self.v1):
            return False
        s = self.slant * (v - self.v0)
        return self.u0 + s <= u <= self.u1 + s

    def bounds2(self) -> tuple[int, int, int, int]:
        span = self.slant * (self.v1 - self.v0)
        return (min(self.u0, self.u0 + span), self.v0,
                max(self.u1, self.u1 + span), self.v1)


class Prism(Solid):
    """
    把平面二维图形沿第三轴拉伸成三维体（棱柱/拉伸体）。

    plane="xz"：(u,v)=(x,z)，沿 y 拉伸 [lo,hi]   —— 竖圆柱 / 墙圈
    plane="xy"：(u,v)=(x,y)，沿 z 拉伸 [lo,hi]   —— 墙面上的窗洞/拱券
    plane="yz"：(u,v)=(z,y)，沿 x 拉伸 [lo,hi]   —— 东西面上的窗洞/拱券
    """

    def __init__(self, shape2, plane: str, lo: int, hi: int):
        if plane not in ("xz", "xy", "yz"):
            raise ValueError(f"plane 只能是 xz/xy/yz，收到 {plane!r}")
        self.s, self.plane = shape2, plane
        self.lo, self.hi = int(min(lo, hi)), int(max(lo, hi))

    def contains(self, x, y, z) -> bool:
        if self.plane == "xz":
            return self.lo <= y <= self.hi and self.s.contains2(x, z)
        if self.plane == "xy":
            return self.lo <= z <= self.hi and self.s.contains2(x, y)
        return self.lo <= x <= self.hi and self.s.contains2(z, y)

    def bounds(self) -> BBox:
        u0, v0, u1, v1 = self.s.bounds2()
        if self.plane == "xz":
            return (u0, self.lo, v0, u1, self.hi, v1)
        if self.plane == "xy":
            return (u0, v0, self.lo, u1, v1, self.hi)
        return (self.lo, u0, v0, self.hi, u1, v1)


# ============================================================================
# 三维图元
# ============================================================================
class Ball(Solid):
    """
    **球 / 球壳 / 部分球面**。球心 (cx,cy,cz)，半径 r。

    inner=None 为实心球；inner 给出为球壳（内边界归属见 inner_closed，约定同 Circle2）。
    y0/y1 给出则只保留该 y 区间 —— 即「部分球面」（dome = 只留上半球）。
    """

    def __init__(self, cx: float, cy: float, cz: float, r: float,
                 inner: float | None = None, inner_closed: bool = True,
                 y0: int | None = None, y1: int | None = None):
        self.cx, self.cy, self.cz = float(cx), float(cy), float(cz)
        self.r = float(r)
        self.inner = None if inner is None else float(inner)
        self.inner_closed = bool(inner_closed)
        self.ylo = None if y0 is None else int(y0)
        self.yhi = None if y1 is None else int(y1)

    def contains(self, x, y, z) -> bool:
        if self.ylo is not None and y < self.ylo:
            return False
        if self.yhi is not None and y > self.yhi:
            return False
        d2 = (x - self.cx) ** 2 + (y - self.cy) ** 2 + (z - self.cz) ** 2
        if d2 > self.r * self.r:
            return False
        if self.inner is not None:
            i2 = self.inner * self.inner
            return d2 >= i2 if self.inner_closed else d2 > i2
        return True

    def bounds(self) -> BBox:
        ylo = self.ylo if self.ylo is not None else _ceil(self.cy - self.r)
        yhi = self.yhi if self.yhi is not None else _floor(self.cy + self.r)
        return (_ceil(self.cx - self.r), ylo, _ceil(self.cz - self.r),
                _floor(self.cx + self.r), yhi, _floor(self.cz + self.r))


class Box(Solid):
    """
    **长方体**，p0/p1 为含端点的整数角点。三态：
        mode="solid"  实心
        mode="hollow" 空心壳（壳厚 thickness）
        mode="frame"  框架（仅 12 条棱，棱截面 thickness×thickness）
    """

    def __init__(self, p0: tuple[int, int, int], p1: tuple[int, int, int],
                 mode: str = "solid", thickness: int = 1):
        if mode not in ("solid", "hollow", "frame"):
            raise ValueError(f"mode 只能是 solid/hollow/frame，收到 {mode!r}")
        self.x0, self.y0, self.z0 = (min(p0[i], p1[i]) for i in range(3))
        self.x1, self.y1, self.z1 = (max(p0[i], p1[i]) for i in range(3))
        self.mode = mode
        self.t = max(1, int(thickness))

    def contains(self, x, y, z) -> bool:
        if not (self.x0 <= x <= self.x1 and self.y0 <= y <= self.y1 and self.z0 <= z <= self.z1):
            return False
        if self.mode == "solid":
            return True
        t = self.t
        nx = x < self.x0 + t or x > self.x1 - t
        ny = y < self.y0 + t or y > self.y1 - t
        nz = z < self.z0 + t or z > self.z1 - t
        if self.mode == "hollow":
            return nx or ny or nz
        return (int(nx) + int(ny) + int(nz)) >= 2     # 框架：至少在两个轴上贴面 = 棱

    def bounds(self) -> BBox:
        return (self.x0, self.y0, self.z0, self.x1, self.y1, self.z1)


class HalfSpace(Solid):
    """
    以**法向量** n + 一点 p0 定义的半空间：n·(p − p0) ≤ 0。

    半空间无界，必须显式给出求值包围盒 bounds_（本层只用它限定遍历范围）。
    屋顶坡面是它的隐式实例：屋面高度场 = 两个半空间之交。
    """

    def __init__(self, p0: Point, normal: Point, bounds: BBox):
        self.p0 = (float(p0[0]), float(p0[1]), float(p0[2]))
        self.n = (float(normal[0]), float(normal[1]), float(normal[2]))
        self._b = tuple(int(v) for v in bounds)  # type: ignore[assignment]

    def value(self, x, y, z) -> float:
        return (self.n[0] * (x - self.p0[0]) + self.n[1] * (y - self.p0[1])
                + self.n[2] * (z - self.p0[2]))

    def contains(self, x, y, z) -> bool:
        return self.value(x, y, z) <= 1e-9

    def bounds(self) -> BBox:
        return self._b  # type: ignore[return-value]


class FunctionSurface(Solid):
    """
    **二元函数曲面**（高度场）：y = f(x, z)，在矩形域 [x0,x1]×[z0,z1] 上取整格。

    顶面恰好是 y = f(x, z)（四舍五入取整），厚 thickness **向下**增厚
    （占据 y ∈ [f−t+1, f]）。典型用途：自由曲面屋面、异形穹顶、地形。
    """

    def __init__(self, f, x0: int, x1: int, z0: int, z1: int, thickness: int = 1):
        self.f = f
        self.x0, self.x1 = int(min(x0, x1)), int(max(x0, x1))
        self.z0, self.z1 = int(min(z0, z1)), int(max(z0, z1))
        self.t = max(1, int(thickness))

    def _top(self, x: int, z: int) -> int:
        return _iround(self.f(x, z))

    def contains(self, x, y, z) -> bool:
        if not (self.x0 <= x <= self.x1 and self.z0 <= z <= self.z1):
            return False
        h = self._top(x, z)
        return h - self.t + 1 <= y <= h

    def bounds(self) -> BBox:
        hs = [self._top(x, z)
              for x in range(self.x0, self.x1 + 1)
              for z in range(self.z0, self.z1 + 1)]
        return (self.x0, min(hs) - self.t + 1, self.z0, self.x1, max(hs), self.z1)


class SpaceCurve(Solid):
    """
    **空间闭合曲线**（参数方程）：P(t) = f(t)，t ∈ [t0, t1]；闭合时 f(t1) ≈ f(t0)。

    注意：空间曲线只能用参数方程定义；「三元函数 F(x,y,z)=c」给出的是**曲面**（等值面），
    不是曲线 —— 所以本类只接受参数函数 f(t) -> (x, y, z)。

    体素化为**管状**：到折线的距离 ≤ radius（radius ≥ 1 保证连通不断线）。
    ``helix(...)`` 是内置的螺旋线实例，可直接喂给本类。
    """

    def __init__(self, f, t0: float = 0.0, t1: float = 1.0,
                 samples: int = 128, radius: float = 1.0):
        self.t0, self.t1 = float(t0), float(t1)
        self.radius = float(radius)
        n = max(1, int(samples))
        self.pts: list[Point] = [tuple(float(c) for c in f(self.t0 + (self.t1 - self.t0) * i / n))  # type: ignore[misc]
                                 for i in range(n + 1)]

    def contains(self, x, y, z) -> bool:
        r2 = self.radius * self.radius + 1e-9
        p = (x, y, z)
        for i in range(len(self.pts) - 1):
            if _point_seg_dist2(p, self.pts[i], self.pts[i + 1]) <= r2:
                return True
        return False

    def bounds(self) -> BBox:
        xs = [p[0] for p in self.pts]
        ys = [p[1] for p in self.pts]
        zs = [p[2] for p in self.pts]
        m = int(math.ceil(self.radius))
        return (_floor(min(xs)) - m, _floor(min(ys)) - m, _floor(min(zs)) - m,
                _ceil(max(xs)) + m, _ceil(max(ys)) + m, _ceil(max(zs)) + m)


def helix(cx: float, cz: float, radius: float, y0: float, y1: float,
          turns: float, phase: float = 0.0):
    """螺旋线参数函数（供 ``SpaceCurve`` 用）：t∈[0,1] -> (x, y, z)。

    绕竖直轴 (cx, cz) 转 ``turns`` 圈，高度由 y0 线性升到 y1；turns 取整数时首尾闭合。
    """
    r, ya, yb = float(radius), float(y0), float(y1)
    tn, ph = float(turns), float(phase)

    def f(t: float) -> Point:
        a = ph + 2.0 * math.pi * tn * t
        return (cx + r * math.cos(a), ya + (yb - ya) * t, cz + r * math.sin(a))

    return f


def bezier(pts: Sequence[Point]):
    """**贝塞尔曲线**（de Casteljau）：控制点 pts（≥2）-> 参数函数 t∈[0,1] -> 点。

    2 个控制点退化为直线段，3 个为二次，4 个为三次……控制点可任意弯曲，
    适合弧形栈道 / 拱肋 / 曲面边界（配合 ``SpaceCurve`` 管状化）。
    开放曲线；要闭合成环请让首尾控制点重合，或改用 ``helix`` / ``torus_knot``。
    """
    ctrl = [(float(p[0]), float(p[1]), float(p[2])) for p in pts]
    if len(ctrl) < 2:
        raise ValueError("bezier 至少需要 2 个控制点")

    def f(t: float) -> Point:
        q = list(ctrl)
        while len(q) > 1:
            s = 1.0 - t
            q = [(q[i][0] * s + q[i + 1][0] * t,
                  q[i][1] * s + q[i + 1][1] * t,
                  q[i][2] * s + q[i + 1][2] * t) for i in range(len(q) - 1)]
        return q[0]

    return f


def torus_knot(cx: float, cz: float, R: float, r: float, p: int = 2, q: int = 3,
               y0: float = 0.0, phase: float = 0.0):
    """**(p,q) 环面纽结**参数函数（供 ``SpaceCurve`` 用）：t∈[0,1] -> (x,y,z)。

    绕竖直轴 (cx, cz) 的主环半径 ``R``、管半径 ``r``；p、q 取互质整数时为闭合纽结
    （(2,3)=三叶结），t=0 与 t=1 重合。到轴心的距离落在 [R−r, R+r]。
    """
    Rf, rf, pn, qn = float(R), float(r), int(p), int(q)

    def f(t: float) -> Point:
        ph = phase + 2.0 * math.pi * t
        rr = Rf + rf * math.cos(qn * ph)
        return (cx + rr * math.cos(pn * ph), y0 + rf * math.sin(qn * ph),
                cz + rr * math.sin(pn * ph))

    return f


# ============================================================================
# 自检
# ============================================================================
def _selfcheck() -> int:
    print("geom_lib 自检（声明式几何合成层）")
    fails: list[str] = []

    def expect(cond: bool, label: str) -> None:
        print(f"  {'OK  ' if cond else '[X] '} {label}")
        if not cond:
            fails.append(label)

    def cellset(s: Solid) -> set[tuple[int, int, int]]:
        return set(s.cells())

    # ---------- 1) 长方体三态 ----------
    expect(len(cellset(Box((0, 0, 0), (4, 4, 4), "solid"))) == 125, "Box 实心 5³=125")
    expect(len(cellset(Box((0, 0, 0), (4, 4, 4), "hollow"))) == 98, "Box 空心 5³=98")
    expect(len(cellset(Box((0, 0, 0), (4, 4, 4), "frame"))) == 44, "Box 框架 5³=44（12 棱）")
    fr = cellset(Box((0, 0, 0), (4, 4, 4), "frame"))
    expect((0, 0, 0) in fr and (0, 0, 2) in fr and (2, 2, 2) not in fr and (0, 2, 2) not in fr,
           "Box 框架：棱在、面心/体心不在")

    # ---------- 2) 闭合圆（圆盘 / 圆环） ----------
    disk = Circle2(0, 0, 3)
    expect(len(set(disk.cells2())) == 29, f"圆盘 r=3 格数={len(set(disk.cells2()))} (期望 29)")
    expect(disk.bounds2() == (-3, -3, 3, 3), "圆盘 r=3 包围盒 -3..3")
    ann = Circle2(0, 0, 3, inner=2, inner_closed=True)     # round_tower 约定
    expect(set(ann.cells2()) == {(u, v) for u, v in disk.cells2() if u * u + v * v >= 4},
           "圆环(内含)= 圆盘 − 开内盘")

    # ---------- 3) 回归：与 round_tower 旧判定逐格相同 ----------
    def legacy_round_tower(cx, cz, radius, y0, y1, hollow, thickness, clear_interior):
        out, carved = set(), set()
        t = max(1, thickness)
        r2 = radius * radius
        inner = max(0, radius - t)
        in2 = inner * inner
        for y in range(min(y0, y1), max(y0, y1) + 1):
            for dx in range(-radius, radius + 1):
                for dz in range(-radius, radius + 1):
                    d2 = dx * dx + dz * dz
                    if d2 > r2:
                        continue
                    if hollow and d2 < in2:
                        if clear_interior:
                            carved.add((cx + dx, y, cz + dz))
                        continue
                    out.add((cx + dx, y, cz + dz))
        return out, carved

    for (radius, hollow, t, clear) in ((3, False, 1, False), (3, True, 1, True),
                                       (4, True, 2, True), (2, True, 3, True), (5, True, 1, False)):
        filled_legacy, carved_legacy = legacy_round_tower(0, 0, radius, 0, 3, hollow, t, clear)
        inner = max(0, radius - t) if hollow else None
        shell = Prism(Circle2(0, 0, radius, inner=inner, inner_closed=True), "xz", 0, 3)
        filled_new = cellset(shell)
        carved_new = set()
        if hollow and clear:
            carved_new = cellset(Prism(Circle2(0, 0, max(0, radius - t), outer_closed=False), "xz", 0, 3))
        expect(filled_new == filled_legacy and carved_new == carved_legacy,
               f"回归 round_tower(r={radius},hollow={hollow},t={t},clear={clear}) 逐格相同")

    # ---------- 4) 回归：与 dome 旧判定逐格相同 ----------
    def legacy_dome(cx, cz, radius, y0, thickness):
        out = set()
        r = int(radius); t = max(1, int(thickness))
        in2 = (r - t) ** 2
        out2 = r * r
        for dy in range(0, r + 1):
            for dx in range(-r, r + 1):
                for dz in range(-r, r + 1):
                    d2 = dx * dx + dy * dy + dz * dz
                    if in2 < d2 <= out2:
                        out.add((cx + dx, y0 + dy, cz + dz))
        return out

    for (radius, t) in ((3, 1), (4, 2), (5, 3)):
        ball = Ball(0, 0, 0, radius, inner=radius - t, inner_closed=False, y0=0, y1=radius)
        expect(cellset(ball) == legacy_dome(0, 0, radius, 0, t),
               f"回归 dome(r={radius},t={t}) 逐格相同")
    expect(Ball(0, 0, 0, 3, inner=2, inner_closed=False, y0=0, y1=3).contains(0, 0, 0) is False
           and Ball(0, 0, 0, 3).contains(0, -1, 0) is True,
           "Ball：部分球面（y 裁剪）+ 全球面")

    # ---------- 5) 回归：与 _rect_ring 旧判定逐格相同 ----------
    def legacy_rect_ring(x0, z0, x1, z1, thickness):
        t = max(1, thickness)
        seen, out = set(), set()
        for layer in range(t):
            ax0, az0, ax1, az1 = x0 + layer, z0 + layer, x1 - layer, z1 - layer
            if ax0 > ax1 or az0 > az1:
                break
            for x in range(ax0, ax1 + 1):
                for z in (az0, az1):
                    if (x, z) not in seen:
                        seen.add((x, z)); out.add((x, z))
            for z in range(az0, az1 + 1):
                for x in (ax0, ax1):
                    if (x, z) not in seen:
                        seen.add((x, z)); out.add((x, z))
        return out

    for args in ((0, 0, 4, 4, 1), (0, 0, 4, 4, 2), (0, 0, 4, 4, 3), (0, 0, 4, 4, 9),
                 (0, 0, 0, 4, 1), (0, 0, 3, 0, 2), (0, 0, 1, 1, 1)):
        expect(set(Rect2(*args).cells2()) == legacy_rect_ring(*args),
               f"回归 _rect_ring{args} 逐格相同")

    # ---------- 6) 直线段（参数方程） ----------
    seg = Segment3((0, 0, 0), (6, 0, 0))
    expect(list(seg.iter_voxels()) == [(x, 0, 0) for x in range(7)],
           "参数直线：轴对齐线段逐格与 range 一致（保序）")
    expect(list(Segment3((6, 0, 0), (0, 0, 0)).iter_voxels()) == [(x, 0, 0) for x in range(6, -1, -1)],
           "参数直线：反向线段保序（供栏杆立柱定位）")
    diag = list(Segment3((0, 0, 0), (5, 3, 2)).iter_voxels())
    conn = all(max(abs(diag[i + 1][k] - diag[i][k]) for k in range(3)) <= 1
               for i in range(len(diag) - 1))
    expect(diag[0] == (0, 0, 0) and diag[-1] == (5, 3, 2) and len(diag) == 6 and conn,
           f"参数直线：三维斜线两端正确且逐格 26 邻接（{len(diag)} 格）")
    tube = Segment3((0, 0, 0), (10, 0, 0), radius=1.0)
    expect(tube.contains(5, 1, 0) and not tube.contains(5, 2, 0),
           "参数直线：radius>0 管状加粗（距离判定）")

    # ---------- 7) 椭圆 ----------
    e = Ellipse2(0, 0, 4, 2)
    expect(e.contains2(0, 0) and e.contains2(0, 2) and e.contains2(4, 0)
           and not e.contains2(0, 3) and not e.contains2(5, 0),
           "椭圆：半轴 4/2 边界判定正确")

    # ---------- 7b) 二维布尔合成（拱券几何 = 矩形 ∪ (圆 ∩ 矩形)） ----------
    arc2 = Rect2(0, 0, 6, 3) | (Circle2(3, 3, 3) & Rect2(0, 4, 6, 6))
    expect(arc2.contains2(0, 0) and arc2.contains2(3, 6) and arc2.contains2(0, 3)
           and not arc2.contains2(0, 6) and not arc2.contains2(3, 7),
           "二维合成：矩形 ∪ (圆 ∩ 矩形) 成员判定正确（拱券）")

    # ---------- 8) 布尔合成 ----------
    A = Box((0, 0, 0), (4, 4, 4), "solid")
    B = Box((2, 2, 2), (6, 6, 6), "solid")
    expect((A | B).contains(6, 6, 6) and (A | B).contains(0, 0, 0),
           "并集：两部分都保留（拼接）")
    expect((A & B).contains(3, 3, 3) and not (A & B).contains(0, 0, 0),
           "交集：只留共同部分")
    expect((A - B).contains(0, 0, 0) and not (A - B).contains(3, 3, 3),
           "差集：挖掉 B")

    # ---------- 9) 半空间（法向量） ----------
    hs = HalfSpace((0, 3, 0), (0, 1, 0), (0, 0, 0, 5, 5, 5))
    expect(hs.contains(0, 2, 0) and hs.contains(0, 3, 0) and not hs.contains(0, 4, 0),
           "半空间：法向量 (0,1,0) 保留下方（n·(p−p0) ≤ 0）")

    # ---------- 9b) 平行四边形 ----------
    pg0 = Parallelogram2(0, 0, 4, 4, slant=0)
    expect(set(pg0.cells2()) == {(u, v) for u in range(5) for v in range(5)},
           "平行四边形 slant=0 即实心矩形 5×5")
    pg = Parallelogram2(0, 0, 2, 4, slant=1)
    expect(pg.contains2(0, 0) and pg.contains2(3, 3) and not pg.contains2(3, 0)
           and pg.bounds2() == (0, 0, 6, 4),
           "平行四边形：逐行平移 slant（斜切）判定 + 包围盒正确")

    # ---------- 9c) 二元函数曲面 ----------
    flat = FunctionSurface(lambda x, z: 5, 0, 3, 0, 3)
    expect(len(set(flat.cells())) == 16 and flat.bounds() == (0, 5, 0, 3, 5, 3),
           "二元函数曲面：常数函数 = 4×4 平面板")
    slope = FunctionSurface(lambda x, z: x, 0, 4, 0, 0)
    expect(slope.contains(3, 3, 0) and slope.contains(0, 0, 0)
           and not slope.contains(3, 0, 0) and slope.bounds() == (0, 0, 0, 4, 4, 0),
           "二元函数曲面：单坡 y=x，顶面随 x 升高")
    thick = FunctionSurface(lambda x, z: 3, 0, 1, 0, 1, thickness=3)
    expect(len(set(thick.cells())) == 4 * 3, "二元函数曲面：thickness 向下增厚")

    # ---------- 9d) 空间闭合曲线（参数方程） ----------
    hx = helix(0, 0, 5, 0, 6, 2)
    expect(abs(hx(0)[0] - hx(1)[0]) < 1e-9 and abs(hx(0)[2] - hx(1)[2]) < 1e-9
           and hx(0)[1] == 0 and hx(1)[1] == 6,
           "helix：整数圈首尾闭合、高度线性")
    curve = SpaceCurve(hx, 0, 1, samples=256, radius=1.0)
    cs = set(curve.cells())
    expect(len(cs) > 50 and curve.contains(5, 0, 0),
           f"空间闭合曲线：螺旋管体素化非空（{len(cs)} 格）且起点在管内")

    # ---------- 9e) 贝塞尔曲线 ----------
    bq = bezier([(0, 0, 0), (4, 6, 0), (8, 0, 0)])
    expect(bq(0.0) == (0.0, 0.0, 0.0) and bq(1.0) == (8.0, 0.0, 0.0)
           and bq(0.5) == (4.0, 3.0, 0.0),
           "bezier：二次曲线端点/中点正确（de Casteljau）")
    bl = bezier([(0, 0, 0), (10, 0, 0)])
    expect(bl(0.25) == (2.5, 0.0, 0.0), "bezier：两点退化为直线段")
    bc = bezier([(0, 0, 0), (4, 6, 8), (8, 0, 2)])
    expect(abs(bc(0.5)[1] - 3.0) < 1e-9 and abs(bc(0.5)[2] - 4.5) < 1e-9,
           "bezier：三维控制点（含 y/z 弯曲）")
    bcs = set(SpaceCurve(bq, 0.0, 1.0, samples=128, radius=1.0).cells())
    expect(len(bcs) > 20, f"bezier：SpaceCurve 体素化非空（{len(bcs)} 格）")

    # ---------- 9f) 环面纽结 ----------
    tk = torus_knot(0, 0, 8, 2, 2, 3)
    expect(all(abs(tk(0.0)[k] - tk(1.0)[k]) < 1e-9 for k in range(3)),
           "torus_knot：(2,3) 纽结闭合（t=0 与 t=1 重合）")
    dr = [math.hypot(tk(i / 64.0)[0], tk(i / 64.0)[2]) for i in range(65)]
    expect(min(dr) > 5.9 and max(dr) < 10.1,
           f"torus_knot：到轴心距离落在 [R−r, R+r]=[6,10]（{min(dr):.1f}..{max(dr):.1f}）")
    tks = set(SpaceCurve(tk, 0.0, 1.0, samples=512, radius=1.0).cells())
    expect(len(tks) > 100, f"torus_knot：SpaceCurve 体素化非空（{len(tks)} 格）")

    # ---------- 10) 确定性：两次遍历逐格相同 ----------
    def _det() -> set:
        return cellset(Prism(Circle2(3, -2, 4, inner=2), "xz", 0, 2))
    expect(_det() == _det(), "确定性：同一定义两次体素化逐格相同")

    print()
    if fails:
        print(f"[FAIL] {len(fails)} 项未通过：")
        for f in fails:
            print(f"   - {f}")
        return 1
    print("[OK] geom_lib 全部自检通过")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="声明式几何合成层")
    ap.add_argument("--selfcheck", action="store_true", help="跑内置自检（含与既有基元的等价回归）")
    args = ap.parse_args()
    if args.selfcheck:
        return _selfcheck()
    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
