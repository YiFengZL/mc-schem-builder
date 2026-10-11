#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
audit_schem.py —— 结构体检（静态四维）+ 结构化错误清单 + 迭代状态机

这是「生成→执行→检查→反馈→修复」闭环里的**检查**环节。它把"看起来不对劲"变成
带坐标区间、错误类型、严重等级的机器可读清单，供生成侧定向修复（禁止"不对，重来"）。

四个静态维度（每项都输出 实测值 / 阈值 / 判定）
------------------------------------------------
1. 悬空方块占比   floating_ratio      方块下方为空气 **且** 六邻实体数 ≤ 1（近乎孤立的漂浮块）
2. 结构连通性     disconnected_ratio  不属于最大连通分量（6 邻接）的方块占比
3. 组件包围盒     component bbox      按 spec.audit.components 声明逐项比对；未声明则用细长规则兜底
4. 对称轴偏差     symmetry_dev        声明 symmetry_axis 时，关于包围盒中面镜像后的差异占比

视觉维度（可选但默认参与判定）
------------------------------
`--vision vision_report.json`：由多模态识图（正/侧/俯/等轴测四视图）产出的结构化报告。
未提供时该项记为 NOT_RUN（不算通过）——因为"无视觉结构性错误"必须由识图给出结论。
`region`（归一化矩形）+ `view` 会在正/侧/俯三个正交视图下**自动换算成世界坐标区间**。

退出码：0 = 通过；1 = 有 error 级问题或维度未达标

用法
----
  # 直接体检（进程内跑 build()，看的是完整网格，不受分片影响）
  python audit_schem.py --spec mc_builds/gangxia_north/spec.json

  # 体检已导出的 .schem
  python audit_schem.py --schem out/x.schem [--origin -200,-59,-200]

  # 一轮完整检查（带状态机与终止判定）
  python audit_schem.py --spec spec.json --round 2 --vision views/vision_report.json
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

SEV_ORDER = {"error": 0, "warning": 1, "info": 2}

DEFAULTS = {
    "float_max": 0.005,       # 悬空方块占比上限
    "disc_max": 0.005,        # 非最大连通分量占比上限
    "sym_tol": 0.02,          # 对称轴偏差上限
    "slender_span": 40,       # 细长分量判定的最小跨度（格）
    "slender_ratio": 8.0,     # 最长向 / 次长向 ≥ 该倍数 → 细长
    "slender_density": 0.5,   # 且 方块数 < 最长向 × 该系数（密度极低）才判
    "max_clusters": 8,        # 悬空/异常最多列几团
}


# ============================================================================
# 载入：spec（进程内 build）或 .schem
# ============================================================================
def cfg_from_spec(spec: dict) -> dict:
    """默认阈值 + spec.audit 覆盖（components / symmetry_axis / 各类上限）。"""
    cfg = dict(DEFAULTS)
    audit_cfg = spec.get("audit") or {}
    cfg.update({k: v for k, v in audit_cfg.items()
                if k in DEFAULTS or k in ("components", "component_tol", "symmetry_axis",
                                          "comp_max", "max_report")})
    return cfg


def _load_module(path: str):
    spec = importlib.util.spec_from_file_location("mc_build_target", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)          # 只加载定义；build_*.py 的 main() 在 __main__ 里
    return mod


def grid_from_spec(spec_path: str, build_path: str | None = None):
    """
    读 spec → 校验 → 进程内跑 build() → (solid, origin, name, out_dir, errors)。
    这样审计的是**完整网格**，分片与否都不影响连通性/对称性判定。
    """
    from build_lib import VoxelGrid
    from spec_schema import load_style, validate_spec

    with open(spec_path, encoding="utf-8") as f:
        spec = json.load(f)

    errors: list[str] = []
    style_def = None
    if spec.get("style"):
        try:
            style_def = load_style(str(spec["style"]))
        except FileNotFoundError as e:
            errors.append(str(e))
    ok, verrs, _warns, params = validate_spec(spec, style_def)
    errors += verrs

    out_dir = os.path.abspath((spec.get("output", {}) or {}).get("dir")
                              or f"mc_builds/{spec.get('name')}")
    name = str(spec.get("name", "build"))
    bp = build_path or os.path.join(os.path.dirname(os.path.abspath(spec_path)), f"build_{name}.py")
    if not os.path.isfile(bp):
        errors.append(f"找不到轨道 B 骨架脚本（期望 {bp}；可用 --build 指定）")
        return None, (0, 0, 0), name, out_dir, errors

    if errors:                            # 参数/风格先过关，再跑几何
        return None, (0, 0, 0), name, out_dir, errors

    mod = _load_module(os.path.abspath(bp))
    g = VoxelGrid()
    t0 = time.time()
    mod.build(g, params)
    arr, palette, origin = g.to_numpy()
    del mod
    print(f"     进程内 build {time.time() - t0:.1f}s   方块 {g.count()}")
    if arr.size == 0:
        errors.append("build() 没有产生任何方块")
        return None, origin, name, out_dir, errors
    return (arr != 0), origin, name, out_dir, errors


def grid_from_schem(schem: str, origin: tuple[int, int, int] | None = None):
    """从 .schem 读网格；origin 省略时尝试从同目录 report.json 里取该片的基点。"""
    from render_preview import load_voxels

    arr, _palette, shape = load_voxels(schem)
    solid = arr != 0
    if origin is None:
        origin = _origin_from_report(schem, shape)
    return solid, origin, os.path.splitext(os.path.basename(schem))[0], shape


def _origin_from_report(schem: str, shape) -> tuple[int, int, int]:
    """report.json 里按文件名找该片的 origin（分片基点）；找不到就用 (0,0,0)。"""
    d = os.path.dirname(os.path.abspath(schem))
    rp = os.path.join(d, "..", f"{d.rstrip(os.sep).split(os.sep)[-1]}.report.json")
    cand = [p for p in (os.path.join(d, "report.json"), os.path.normpath(rp)) if os.path.isfile(p)]
    base = os.path.basename(schem)
    for p in cand:
        try:
            with open(p, encoding="utf-8") as f:
                rep = json.load(f)
            for o in rep.get("outputs", []):
                if os.path.basename(str(o.get("file", ""))) == base and o.get("origin"):
                    return tuple(int(v) for v in o["origin"])
        except (OSError, ValueError, KeyError):
            continue
    return (0, 0, 0)


# ============================================================================
# 四个静态维度
# ============================================================================
# 26 邻接偏移（面 6 + 棱 12 + 角 8）。
# MC 里"对角搭接"是常规做法（45° 台阶、斜墙、斜屋脊），连通性判定必须容忍：
# 纯 6 邻接会把一条完整台阶判成 N 个独立结构，把台阶角块判成悬空 —— 都是假警报。
_OFF26 = [(dx, dy, dz) for dx in (-1, 0, 1) for dy in (-1, 0, 1) for dz in (-1, 0, 1)
          if (dx, dy, dz) != (0, 0, 0)]


def mask_floating(solid: np.ndarray) -> np.ndarray:
    """
    悬空 = 下方为空气 **且** 26 邻接实体数 ≤ 1（真正孤立的漂浮块）。

    只对"下方为空气"的候选格数邻接（整场 26 次加法太贵），返回掩码供聚类定位。
    """
    below = np.zeros_like(solid)
    below[:, 1:, :] = solid[:, :-1, :]
    idx = np.argwhere(solid & ~below)
    out = np.zeros(solid.shape, dtype=bool)
    if idx.size == 0:
        return out
    cnt = np.zeros(len(idx), dtype=np.uint8)
    shape = solid.shape
    for dx, dy, dz in _OFF26:
        p = idx + (dx, dy, dz)
        ok = np.ones(len(idx), dtype=bool)
        for k in range(3):
            ok &= (p[:, k] >= 0) & (p[:, k] < shape[k])
        cnt[ok] += solid[p[ok, 0], p[ok, 1], p[ok, 2]]
    sel = cnt <= 1
    out[idx[sel, 0], idx[sel, 1], idx[sel, 2]] = True
    return out


def _grow(solid_idx: set, seeds, shape) -> set:
    """
    6 邻接洪泛：只在 solid_idx 内走，返回本次连通的集合。

    故意用 Python 集合而非向量化索引算术：复杂度只与方块数相关（O(N)），
    且没有"下标换算写错"的隐患（早先的向量化版本正栽在这里，会越界/爆内存）。
    88 万块的岗厦北整场扫描约数秒，对检查环节完全可接受。
    """
    W, H, L = shape
    sx, sy = H * L, L
    seen: set = set()
    stack = [int(s) for s in seeds if int(s) in solid_idx]
    while stack:
        f = stack.pop()
        if f in seen:
            continue
        seen.add(f)
        x, r = divmod(f, sx)
        y, z = divmod(r, L)
        if x + 1 < W and f + sx in solid_idx and f + sx not in seen:
            stack.append(f + sx)
        if x > 0 and f - sx in solid_idx and f - sx not in seen:
            stack.append(f - sx)
        if y + 1 < H and f + sy in solid_idx and f + sy not in seen:
            stack.append(f + sy)
        if y > 0 and f - sy in solid_idx and f - sy not in seen:
            stack.append(f - sy)
        if z + 1 < L and f + 1 in solid_idx and f + 1 not in seen:
            stack.append(f + 1)
        if z > 0 and f - 1 in solid_idx and f - 1 not in seen:
            stack.append(f - 1)
    return seen


def _idx_bbox(flat_idx, shape) -> list[int]:
    """一组 flat 下标 → 索引 bbox [x0,y0,z0,x1,y1,z1]。"""
    H, L = shape[1], shape[2]
    xs, rem = np.divmod(np.asarray(flat_idx, dtype=np.int64), H * L)
    ys, zs = np.divmod(rem, L)
    return [int(xs.min()), int(ys.min()), int(zs.min()),
            int(xs.max()), int(ys.max()), int(zs.max())]


def _merge26(lab: np.ndarray) -> list[list[int]]:
    """
    把 6 邻接标签按 **26 邻接** 合并（union-find，边用向量化错位比较收集）。

    这样"对角搭接"的多块结构会并成一个分量，而真正隔开一段距离的构件仍算独立。
    """
    parent: dict[int, int] = {}

    def find(a: int) -> int:
        while parent.get(a, a) != a:
            a = parent[a]
        return a

    def union(a: int, b: int) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)

    W, H, L = lab.shape
    for dx, dy, dz in _OFF26:
        a = lab[max(0, -dx):W - max(0, dx), max(0, -dy):H - max(0, dy), max(0, -dz):L - max(0, dz)]
        b = lab[max(0, dx):W - max(0, -dx), max(0, dy):H - max(0, -dy), max(0, dz):L - max(0, -dz)]
        m = (a > 0) & (b > 0) & (a != b)
        if not m.any():
            continue
        aa, bb = a[m], b[m]
        pairs = np.unique(np.stack([np.minimum(aa, bb), np.maximum(aa, bb)], axis=1), axis=0)
        for u, v in pairs.tolist():
            union(int(u), int(v))

    groups: dict[int, list[int]] = {}
    for i in np.unique(lab):
        if int(i) > 0:
            groups.setdefault(find(int(i)), []).append(int(i))
    return list(groups.values())


def components(solid: np.ndarray, max_report: int = 6) -> tuple[list[dict], int]:
    """
    结构连通性：先按 6 邻接严格分组，再按 26 邻接合并。

    返回 (合并后的分量列表 [{size, bbox, strict_parts}] 按 size 降序, 严格 6 邻接分量数)。
    """
    flat = solid.ravel()
    remaining = set(np.flatnonzero(flat).tolist())
    strict: list[set] = []
    while remaining:
        comp = _grow(remaining, [next(iter(remaining))], solid.shape)
        remaining -= comp
        strict.append(comp)

    lab = np.zeros(flat.size, dtype=np.int32)
    for i, comp in enumerate(strict, 1):
        lab[np.fromiter(comp, dtype=np.int64, count=len(comp))] = i
    lab = lab.reshape(solid.shape)

    out: list[dict] = []
    for group in _merge26(lab):
        bbs = [_idx_bbox(sorted(strict[i - 1]), solid.shape) for i in group]
        out.append({
            "size": sum(len(strict[i - 1]) for i in group),
            "bbox": [min(b[k] for b in bbs) for k in range(3)]
                    + [max(b[k + 3] for b in bbs) for k in range(3)],
            "strict_parts": len(group),
        })
    out.sort(key=lambda c: -c["size"])
    return out[:max_report], len(strict)


def symmetry_dev(solid: np.ndarray, axis: str) -> float:
    """关于包围盒中面镜像后的差异占比（数组是贴 bbox 裁的，翻转即关于中面镜像）。"""
    ax = 0 if axis == "x" else 2
    flipped = np.flip(solid, ax)
    return float((solid != flipped).sum()) / max(1, int(solid.sum()))


def clusters(mask: np.ndarray, limit: int) -> list[dict]:
    """把异常掩码聚成若干团（每团给方块数与索引 bbox），按大小降序。"""
    remaining = set(np.flatnonzero(mask.ravel()).tolist())
    res: list[dict] = []
    while remaining and len(res) < limit:
        comp = _grow(remaining, [next(iter(remaining))], mask.shape)
        remaining -= comp
        res.append({"count": len(comp), "bbox": _idx_bbox(sorted(comp), mask.shape)})
    res.sort(key=lambda c: -c["count"])
    return res


# ============================================================================
# 视觉报告：结构化校验 + region → 世界坐标区间
# ============================================================================
# 三个正交视图的画面轴映射（render_preview 的实测朝向）：
#   正视图 view_front : 水平 = x（左→右 = 索引 0→W-1），垂直 = y（上 = 最大 y）
#   侧视图 view_side  : 水平 = z（左→右 = 索引 0→L-1），垂直 = y（上 = 最大 y）
#   俯视图 view_top   : 水平 = z（左→右 = 索引 0→L-1），垂直 = x（上 = 索引 0 → 下 = W-1）
VIEW_AXES = {
    "front": ((0, False), (1, True)),
    "side":  ((2, False), (1, True)),
    "top":   ((2, False), (0, False)),
}


def region_to_bbox(view: str, region, origin, shape):
    """
    把归一化矩形 (u0,v0,u1,v1) 换算成世界坐标区间；非正交视图返回 None。

    正交视图沿视线方向被"压扁"，该轴无法从图上定位 → 取**全深度**（因此这条 bbox
    在深度方向是一整片，属于"剖面式定位"：够用来缩小排查范围，但不是单点坐标）。
    """
    if view not in VIEW_AXES or not region or len(region) != 4:
        return None
    W, H, L = shape
    dims = (W, H, L)
    lo = [origin[k] for k in range(3)]
    hi = [origin[k] + dims[k] - 1 for k in range(3)]
    for (ax, vert), span in zip(VIEW_AXES[view], ((region[0], region[2]), (region[1], region[3]))):
        a, b = min(span), max(span)
        n = dims[ax]
        i0, i1 = int(a * (n - 1)), int(round(b * (n - 1)))
        i0, i1 = max(0, min(n - 1, i0)), max(0, min(n - 1, i1))
        if vert:                                  # 垂直轴：图像上 = 索引最大
            i0, i1 = (n - 1) - i1, (n - 1) - i0
        lo[ax], hi[ax] = origin[ax] + i0, origin[ax] + i1
    return [lo[0], lo[1], lo[2], hi[0], hi[1], hi[2]]


def load_vision(path: str, origin, shape) -> tuple[list[dict], list[str], bool]:
    """读多模态识图报告 → (findings, 校验问题, 是否已运行)。"""
    with open(path, encoding="utf-8") as f:
        rep = json.load(f)
    problems: list[str] = []
    if str(rep.get("schema", "")) != "mc-vision/1":
        problems.append(f"vision.schema 应为 'mc-vision/1'，收到 {rep.get('schema')!r}")
    if not rep.get("reviewer"):
        problems.append("vision.reviewer 缺失（应写明是哪个多模态模型看的图）")
    findings: list[dict] = []
    for i, v in enumerate(rep.get("findings", []) or [], 1):
        t = str(v.get("type", "")).strip()
        sev = str(v.get("severity", "error")).strip()
        if not t:
            problems.append(f"vision.findings[{i}] 缺 type")
            continue
        if sev not in SEV_ORDER:
            problems.append(f"vision.findings[{i}].severity={sev!r} 非法（error/warning/info）")
            sev = "error"
        bbox = v.get("bbox")
        if bbox is not None and len(bbox) == 6:
            bbox = [int(round(float(c))) for c in bbox]
        else:
            bbox = region_to_bbox(str(v.get("view", "")), v.get("region"), origin, shape)
            if bbox is None and not v.get("region"):
                problems.append(
                    f"vision.findings[{i}] 既无 bbox 也无 region —— 无法定位（要求给出坐标区间）")
        findings.append({
            "id": f"V-{i:03d}", "dim": "视觉", "type": t, "severity": sev,
            "bbox": bbox, "view": v.get("view"), "region": v.get("region"),
            "count": v.get("count"), "note": v.get("note", ""),
            "measured": None, "threshold": None,
            "hint": "按 view/region 定位到结构：先看该视图的投影关系，再核对对应构件的生成代码",
        })
    # 识图给了 FAIL 却一条可定位条目都没有 → 视为"模糊反馈"，直接判不合格
    if str(rep.get("verdict", "")).upper() == "FAIL" and not any(
            f["severity"] == "error" for f in findings):
        problems.append("vision.verdict=FAIL 但 findings 里没有 error 级条目 —— 必须给出可定位的问题")
    return findings, problems, True


# ============================================================================
# 主流程
# ============================================================================
def audit(solid, origin, cfg, *, name, round_no, vision_path, vision_skip,
          state_path, max_rounds, pass_need, extra: list[dict] | None = None):
    t0 = time.time()
    W, H, L = solid.shape
    total = int(solid.sum())
    findings: list[dict] = list(extra or [])
    metrics: list[dict] = []

    def m(name_cn, key, measured, threshold, ok, unit=""):
        metrics.append({"metric": key, "name": name_cn, "measured": round(float(measured), 6),
                        "threshold": threshold, "unit": unit, "verdict": "OK" if ok else "FAIL"})

    def add(fid, dim, typ, sev, bbox, note, **kw):
        d = {"id": fid, "dim": dim, "type": typ, "severity": sev, "bbox": bbox, "note": note}
        d.update(kw)
        findings.append(d)

    # ---- 维度 1：悬空方块 ----
    fmask = mask_floating(solid)
    fn = int(fmask.sum())
    fr = fn / max(1, total)
    m("悬空方块占比", "floating_ratio", fr, cfg["float_max"], fr <= cfg["float_max"])
    if fn:
        for i, c in enumerate(clusters(fmask, cfg["max_clusters"]), 1):
            bb = [origin[k] + c["bbox"][k] for k in range(3)] + \
                 [origin[k] + c["bbox"][k + 3] for k in range(3)]
            add(f"F-{i:03d}", "悬空", "悬空方块", "error" if fr > cfg["float_max"] else "warning",
                bb, f"该团 {c['count']} 块下方为空气且六邻实体 ≤ 1",
                metric="floating_ratio", measured=round(fr, 6), threshold=cfg["float_max"],
                hint="检查该区间是否漏了支撑柱/墙线；若整块只是装饰件，考虑并入相邻构件或补支撑")

    # ---- 维度 2：连通性（按 26 邻接判定，容忍 MC 常见的对角搭接）----
    comps, n6 = components(solid, max_report=cfg.get("max_report", 6))
    biggest = comps[0]["size"] if comps else 0
    disc = total - biggest
    dr = disc / max(1, total)
    m("非主体方块占比", "disconnected_ratio", dr, cfg["disc_max"], dr <= cfg["disc_max"])
    m("连通分量数(26邻接)", "component_count", len(comps), cfg.get("comp_max", 1),
      len(comps) <= cfg.get("comp_max", 1), unit="个")
    metrics.append({"metric": "component_count_strict", "name": "6 邻接分量数(参考)",
                    "measured": n6, "threshold": None, "unit": "个", "verdict": "INFO",
                    "note": "对角搭接（45° 台阶/斜墙）会被拆成多段，仅作参考"})
    for i, c in enumerate(comps[1:], 1):
        if c["size"] / max(1, total) <= cfg["disc_max"]:
            continue
        bb = [origin[k] + c["bbox"][k] for k in range(3)] + \
             [origin[k] + c["bbox"][k + 3] for k in range(3)]
        add(f"C-{i:03d}", "连通性", "脱离主体", "error", bb,
            f"该分量 {c['size']} 块（占 {c['size']/max(1,total):.4%}）与主体不连通",
            metric="disconnected_ratio", measured=round(c["size"] / max(1, total), 6),
            threshold=cfg["disc_max"],
            hint="要么补连接件（墙/梁/柱）把它并入主体，要么它本就是独立构件（如单独摆放的塔），需在 spec.audit 里声明")

    # ---- 维度 3：组件包围盒 ----
    declares = cfg.get("components") or []
    tol = int(cfg.get("component_tol", 0))
    bad = 0
    for i, comp in enumerate(declares, 1):
        bb = comp.get("bbox")
        nm = comp.get("name", f"组件{i}")
        t = int(comp.get("tol", tol))
        if not bb or len(bb) != 6:
            add(f"B-{i:03d}", "组件", "声明非法", "error", None,
                f"组件 {nm} 的 bbox 声明非法：{bb}", hint="spec.audit.components[].bbox 需为 [x0,y0,z0,x1,y1,z1]")
            bad += 1
            continue
        sel = solid[bb[0] - origin[0]:bb[3] - origin[0] + 1,
                    bb[1] - origin[1]:bb[4] - origin[1] + 1,
                    bb[2] - origin[2]:bb[5] - origin[2] + 1]
        n = int(sel.sum())
        if n == 0:
            add(f"B-{i:03d}", "组件", "组件缺失", "error", [int(v) for v in bb],
                f"声明组件 {nm} 在其 bbox 内没有任何方块",
                metric="component_bbox", measured=0, threshold=t,
                hint="确认该构件是否被生成、或 bbox 声明是否写错")
            bad += 1
            continue
        idx = np.argwhere(sel)
        act = [bb[0] + int(idx[:, 0].min()), bb[1] + int(idx[:, 1].min()), bb[2] + int(idx[:, 2].min()),
               bb[0] + int(idx[:, 0].max()), bb[1] + int(idx[:, 1].max()), bb[2] + int(idx[:, 2].max())]
        delta = [act[k] - int(bb[k]) for k in range(6)]
        if any(abs(d) > t for d in delta):
            add(f"B-{i:03d}", "组件", "包围盒越界", "error", act,
                f"组件 {nm} 实际 bbox {act} 与声明 {list(bb)} 逐边偏差 {delta}（容差 {t}）",
                metric="component_bbox", measured=max(abs(d) for d in delta), threshold=t,
                hint="按偏差方向核对构件尺寸参数（含板厚/挑出量），或修正 spec.audit.components 的声明")
            bad += 1
    m("组件包围盒越界数", "component_bbox_violations", bad, 0, bad == 0, unit="个")
    if not declares:                      # 未声明 → 用细长规则兜底
        for i, c in enumerate(comps, 1):
            span = [c["bbox"][k + 3] - c["bbox"][k] + 1 for k in range(3)]
            mx, second = max(span), sorted(span)[-2]
            if mx >= cfg["slender_span"] and mx >= cfg["slender_ratio"] * max(1, second) \
                    and c["size"] < mx * cfg["slender_density"]:
                bb = [origin[k] + c["bbox"][k] for k in range(3)] + \
                     [origin[k] + c["bbox"][k + 3] for k in range(3)]
                add(f"S-{i:03d}", "组件", "疑似细长杆/错位飞线", "warning", bb,
                    f"分量尺寸 {span}（最长 {mx} 格）且仅 {c['size']} 块，密度异常低",
                    metric="component_bbox", measured=mx, threshold=cfg["slender_span"],
                    hint="疑似参数取错方向的细杆（历史上 stairs_flight 的 width 轴反了就长这样）")

    # ---- 维度 4：对称轴 ----
    axis = cfg.get("symmetry_axis")
    if axis in ("x", "z"):
        dev = symmetry_dev(solid, axis)
        m(f"对称轴偏差({axis})", "symmetry_dev", dev, cfg["sym_tol"], dev <= cfg["sym_tol"])
        if dev > cfg["sym_tol"]:
            flip = np.flip(solid, 0 if axis == "x" else 2)
            bad_mask = (solid ^ flip) & solid
            for i, c in enumerate(clusters(bad_mask, cfg["max_clusters"]), 1):
                bb = [origin[k] + c["bbox"][k] for k in range(3)] + \
                     [origin[k] + c["bbox"][k + 3] for k in range(3)]
                add(f"Y-{i:03d}", "对称", "对称轴偏差", "error", bb,
                    f"该区间 {c['count']} 块在镜像后找不到对应块（整体偏差 {dev:.4%}）",
                    metric="symmetry_dev", measured=round(dev, 6), threshold=cfg["sym_tol"],
                    hint=f"沿 {axis} 轴镜像后核对：多出来的一侧是否漏了对称生成（mirror/blit）或用绝对坐标写死了单侧")
    else:
        metrics.append({"metric": "symmetry_dev", "name": "对称轴偏差", "measured": None,
                        "threshold": None, "unit": "", "verdict": "SKIP",
                        "note": "spec.audit.symmetry_axis 未声明（本建筑本就非对称）"})

    # ---- 视觉维度 ----
    vision_done = False
    if vision_skip:
        metrics.append({"metric": "vision", "name": "视觉结构性错误", "measured": None,
                        "threshold": 0, "unit": "个", "verdict": "SKIP", "note": "--skip-vision"})
    elif vision_path and os.path.isfile(vision_path):
        vf, problems, vision_done = load_vision(vision_path, origin, solid.shape)
        findings += vf
        verr = sum(1 for f in vf if f["severity"] == "error")
        m("视觉结构性错误", "vision_errors", verr, 0, verr == 0, unit="个")
        for p in problems:
            add(f"V-90{problems.index(p):01d}", "视觉", "视觉报告不合规", "error", None, p,
                hint="vision_report.json 必须给出 type/severity，并带 bbox 或 region")
    else:
        metrics.append({"metric": "vision", "name": "视觉结构性错误", "measured": None,
                        "threshold": 0, "unit": "个", "verdict": "NOT_RUN",
                        "note": "未提供 vision_report.json —— 四视图已渲染，等识图回填"})
        add("V-000", "视觉", "视觉维度未执行", "error", None,
            "四视图已生成但尚无识图结论；本维度不通过则整轮不通过",
            hint="读 <out>/views/view_{front,side,top,iso}.png 后按 views/vision_report.template.json 回填，"
                 "再以 --vision 传入")

    # ---- 汇总 ----
    errors = [f for f in findings if f["severity"] == "error"]
    warnings = [f for f in findings if f["severity"] == "warning"]
    metrics_fail = [mt for mt in metrics if mt["verdict"] in ("FAIL", "NOT_RUN")]
    verdict = "PASS" if (not metrics_fail and not errors) else "FAIL"
    result = {
        "schema": "mc-audit/1",
        "name": name,
        "round": round_no,
        "target": {"blocks": total,
                   "bbox": [origin[0], origin[1], origin[2],
                            origin[0] + W - 1, origin[1] + H - 1, origin[2] + L - 1]},
        "shape": [W, H, L],
        "origin": list(origin),
        "metrics": metrics,
        "components": [{"size": c["size"], "bbox": c["bbox"],
                        "strict_parts": c.get("strict_parts", 1)} for c in comps],
        "findings": sorted(findings, key=lambda f: (SEV_ORDER.get(f["severity"], 9), str(f["id"]))),
        "summary": {"errors": len(errors), "warnings": len(warnings), "metrics_failed": len(metrics_fail)},
        "verdict": verdict,
        "elapsed_s": round(time.time() - t0, 2),
    }
    result["loop"] = update_state(state_path, round_no, result, max_rounds, pass_need) \
        if state_path else None
    return result


def update_state(state_path: str, round_no: int, result: dict, max_rounds: int, pass_need: int) -> dict:
    """迭代状态机：连续通过计数、最佳轮次、终止判定。"""
    st = {"rounds": [], "consecutive_pass": 0, "best": None}
    if os.path.isfile(state_path):
        try:
            with open(state_path, encoding="utf-8") as f:
                st.update(json.load(f))
        except (OSError, ValueError):
            pass
    score = result["summary"]["errors"] * 10 + result["summary"]["warnings"]
    # "全维度通过"才计入连续通过：视觉被 SKIP/NOT_RUN 的轮次不能算数（规范要求
    # 所有指标达标**且**无视觉结构性错误）
    full = all(m["verdict"] not in ("SKIP", "NOT_RUN") for m in result.get("metrics", []))
    st["rounds"] = [r for r in st.get("rounds", []) if r.get("round") != round_no]
    st["rounds"].append({"round": round_no, "verdict": result["verdict"], "score": score,
                         "full_dims": full,
                         "errors": result["summary"]["errors"],
                         "warnings": result["summary"]["warnings"]})
    st["rounds"].sort(key=lambda r: r["round"])
    # 连续通过数由**轮次历史**重算（而不是无脑 +1）：否则把同一轮重跑两次会被误算成"连续两轮通过"
    streak = 0
    for r in st["rounds"]:
        streak = streak + 1 if (r["verdict"] == "PASS" and r.get("full_dims")) else 0
    st["consecutive_pass"] = streak
    best = st.get("best")
    if best is None or score < best.get("score", 10 ** 9):
        st["best"] = {"round": round_no, "score": score, "verdict": result["verdict"]}
    if st["consecutive_pass"] >= pass_need:
        st["status"] = "PASS_TERMINATED"
        st["reason"] = f"连续 {st['consecutive_pass']} 轮全部指标达标且无视觉结构性错误 —— 终止循环"
    elif round_no >= max_rounds:
        st["status"] = "MAX_ROUNDS_TERMINATED"
        st["reason"] = (f"已达 {max_rounds} 轮上限仍未通过：输出最佳轮次 "
                        f"{st['best']['round']}（score {st['best']['score']}）与未解决清单")
    else:
        st["status"] = "CONTINUE"
        if result["verdict"] == "PASS":
            st["reason"] = (f"本轮通过，但连续通过 {st['consecutive_pass']}/{pass_need} —— "
                            f"需再连过一轮确认稳定后终止")
        else:
            st["reason"] = (f"第 {round_no} 轮未通过（{result['summary']['errors']} 个 error / "
                            f"{result['summary']['warnings']} 个 warning）：按 findings 的 bbox 定向修复后重跑")
    st["max_rounds"], st["pass_need"] = max_rounds, pass_need
    os.makedirs(os.path.dirname(os.path.abspath(state_path)), exist_ok=True)
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=2)
    return st


def print_report(res: dict) -> None:
    print(f"\n===== 结构体检 · 第 {res['round']} 轮 · {res['name']} =====")
    print(f"  方块 {res['target']['blocks']}   bbox {res['target']['bbox']}   用时 {res['elapsed_s']}s")
    print(f"\n  {'维度':<26}{'实测':>14}{'阈值':>12}{'判定':>9}")
    print("  " + "-" * 62)
    for mt in res["metrics"]:
        meas = "-" if mt["measured"] is None else f"{mt['measured']}"
        thr = "-" if mt["threshold"] is None else f"{mt['threshold']}"
        print(f"  {mt['name']:<26}{meas:>14}{thr:>12}{mt['verdict']:>9}"
              + (f"   {mt.get('note', '')}" if mt.get("note") else ""))
    if res["components"]:
        print(f"\n  连通分量 {len(res['components'])} 个（最大 {res['components'][0]['size']} 块）：")
        for c in res["components"][:6]:
            print(f"    {c['size']:>7} 块   bbox {c['bbox']}")
    fs = res["findings"]
    print(f"\n  问题清单：{res['summary']['errors']} 个 error / {res['summary']['warnings']} 个 warning")
    for f in fs:
        bb = f["bbox"] if f["bbox"] else "-"
        print(f"    [{f['severity']:<7}] {f['id']}  {f['dim']}/{f['type']}   bbox={bb}")
        if f.get("note"):
            print(f"              {f['note']}")
        if f.get("hint"):
            print(f"              修复提示：{f['hint']}")
    loop = res.get("loop")
    print(f"\n  判定：{res['verdict']}")
    if loop:
        print(f"  循环状态：{loop['status']} —— {loop['reason']}")
        print(f"           已跑 {len(loop['rounds'])} 轮；连续通过 {loop['consecutive_pass']} 次"
              f"（需 {loop['pass_need']}）；最佳轮次 {loop['best']}")


def main() -> int:
    ap = argparse.ArgumentParser(description="结构体检（静态四维 + 结构化错误 + 迭代状态机）")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--spec", help="spec.json（进程内跑 build()，审计完整网格）")
    src.add_argument("--schem", help="已导出的 .schem")
    ap.add_argument("--build", help="轨道 B 骨架脚本（默认 <spec目录>/build_<name>.py）")
    ap.add_argument("--origin", help=".schem 模式下的世界基点 x,y,z（默认查同目录 report.json）")
    ap.add_argument("--vision", help="多模态识图报告 vision_report.json")
    ap.add_argument("--skip-vision", action="store_true", help="本轮不做视觉维度（记为 SKIP）")
    ap.add_argument("--out", help="结构化结果 JSON 落盘路径")
    ap.add_argument("--round", type=int, default=1, help="第几轮（配合 --state 做终止判定）")
    ap.add_argument("--state", help="迭代状态文件（默认 <输出目录>/.audit_state.json）")
    ap.add_argument("--max-rounds", type=int, default=5, help="最大轮次（默认 5）")
    ap.add_argument("--pass-need", type=int, default=2, help="连续通过轮数（默认 2）")
    ap.add_argument("--float-max", type=float, default=None, help="悬空占比阈值")
    ap.add_argument("--disc-max", type=float, default=None, help="非主体占比阈值")
    ap.add_argument("--sym-tol", type=float, default=None, help="对称偏差阈值")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    cfg = dict(DEFAULTS)
    errors: list[str] = []

    if args.spec:
        with open(args.spec, encoding="utf-8") as f:
            sp = json.load(f)
        cfg.update(cfg_from_spec(sp))
        solid, origin, name, out_dir, errors = grid_from_spec(args.spec, args.build)
    else:
        org = tuple(int(v) for v in args.origin.split(",")) if args.origin else None
        solid, origin, name, _shape = grid_from_schem(args.schem, org)
        out_dir = os.path.dirname(os.path.abspath(args.schem))

    for k, v in (("float_max", args.float_max), ("disc_max", args.disc_max), ("sym_tol", args.sym_tol)):
        if v is not None:
            cfg[k] = v

    if solid is None:
        print("[FAIL] 无法体检：")
        for e in errors:
            print(f"   X {e}")
        return 1

    extra = [{"id": f"P-{i:03d}", "dim": "参数", "type": "前置校验未过", "severity": "error",
              "bbox": None, "note": e, "hint": "先修 spec / 风格声明"} for i, e in enumerate(errors, 1)]
    state_path = args.state or os.path.join(out_dir, ".audit_state.json")
    res = audit(solid, origin, cfg, name=name, round_no=args.round, vision_path=args.vision,
                vision_skip=args.skip_vision, state_path=state_path,
                max_rounds=args.max_rounds, pass_need=args.pass_need, extra=extra)

    outp = args.out or os.path.join(out_dir, "audit", f"round_{args.round}", "audit.json")
    os.makedirs(os.path.dirname(os.path.abspath(outp)), exist_ok=True)
    with open(outp, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2)

    if not args.quiet:
        print_report(res)
        print(f"\n  结构化结果 -> {outp}")
        print(f"  迭代状态   -> {state_path}")
    return 0 if res["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
