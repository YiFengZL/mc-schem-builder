#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
render_preview.py —— .schem 预览渲染（纯 numpy 向量化，无逐方块循环）

性能设计（这是本文件的核心约束）
--------------------------------
1. **面剔除**：用 6 向数组切片比较求暴露面，只保留等距视图可见的 3 个朝向
   （顶面 / 右面 / 前面）。内部方块完全不参与渲染。
2. **分层画家算法**：等距投影下，深度 = x+y+z。按深度从远到近**整层**写入，
   同层体素互不遮挡，因此层内无需排序 —— 外层循环是 O(层数) 而非 O(方块数)。
3. **模板 + 基址写入**：每个朝向预先算一张像素遮罩模板，
   写入时用 `buf.flat[base[:,None] + offsets[None,:]]` 一次性完成。
4. 不依赖 PIL / matplotlib —— 直接手写 PNG（zlib + struct），避免额外依赖。

输出：
    <name>.iso.png   等距立体图
    <name>.top.png   俯视高度图
    <name>.side.png  正视图（可选）

用法：
    python render_preview.py --schem out/castle.schem
    python render_preview.py --schem big.schem --tile 1 --max-pixels 4000000
    python render_preview.py --schem out/castle.schem --no-top
"""

from __future__ import annotations

import argparse
import json
import os
import struct
import sys
import time
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np  # noqa: E402


# ============================================================================
# 解码（复用 verify_schem 的逻辑，含快速路径）
# ============================================================================
def load_voxels(path: str):
    """返回 (arr, palette, shape)。arr 为 uint16 调色板索引，0 恒为空气。"""
    import nbtlib

    root = nbtlib.load(path)

    def pick(*names, default=None):
        for n in names:
            if n in root:
                return root[n]
        return default

    W, H, L = int(pick("Width")), int(pick("Height")), int(pick("Length"))
    pal_tag = pick("Palette", "Blocks")
    idx2name = {int(v): str(k) for k, v in pal_tag.items()}
    palette = [idx2name.get(i, "minecraft:air") for i in range(len(idx2name))]

    block_data = pick("BlockData")
    raw = np.frombuffer(bytes((b & 0xFF) for b in block_data), dtype=np.uint8)

    total = W * H * L
    # 快速路径：调色板很小时绝大多数 varint 是单字节（<0x80），可整块解析
    if raw.size == total and not np.any(raw >= 0x80):
        arr = raw.astype(np.uint16)
    else:
        arr = np.empty(total, dtype=np.uint16)
        pos = 0
        n = raw.size
        for i in range(total):
            val = 0
            shift = 0
            while True:
                b = int(raw[pos]); pos += 1
                val |= (b & 0x7F) << shift
                if not (b & 0x80):
                    break
                shift += 7
            arr[i] = val

    # Sponge 索引顺序 x + z*W + y*W*L  →  重排为 (W,H,L)
    arr = arr.reshape(H, L, W).transpose(2, 0, 1)
    return np.ascontiguousarray(arr), palette, (W, H, L)


# ============================================================================
# 方块配色（按名称关键词推断，无需维护巨型色表）
# ============================================================================
WOOD_COLORS = {
    "oak": (162, 130, 78), "spruce": (114, 84, 48), "birch": (216, 214, 200),
    "jungle": (154, 110, 76), "acacia": (172, 92, 46), "dark_oak": (66, 43, 20),
    "mangrove": (117, 54, 52), "cherry": (224, 168, 172), "bamboo": (198, 178, 84),
    "crimson": (94, 44, 60), "warped": (44, 118, 108),
}
STONE_COLORS = {
    "deepslate": (72, 72, 76), "blackstone": (42, 34, 38), "netherrack": (110, 52, 52),
    "granite": (152, 104, 86), "diorite": (222, 222, 220), "andesite": (136, 136, 136),
    "tuff": (108, 108, 100), "sandstone": (222, 208, 156), "bricks": (150, 90, 74),
    "quartz": (234, 228, 220), "prismarine": (94, 160, 152), "purpur": (168, 124, 168),
    "end_stone": (220, 222, 162), "cobblestone": (128, 128, 128), "stone": (126, 126, 126),
    "obsidian": (22, 18, 32), "basalt": (70, 70, 74), "calcite": (224, 224, 220),
    "dripstone": (140, 110, 96), "mud": (60, 60, 62), "clay": (160, 166, 178),
}
COLOR_WORDS = {
    "white": (232, 236, 236), "light_gray": (154, 158, 158), "gray": (66, 70, 70),
    "black": (24, 24, 26), "brown": (114, 74, 42), "red": (160, 40, 36),
    "orange": (216, 122, 34), "yellow": (222, 196, 42), "lime": (118, 190, 40),
    "green": (58, 104, 34), "cyan": (32, 122, 138), "light_blue": (96, 168, 214),
    "blue": (52, 66, 168), "purple": (128, 46, 170), "magenta": (188, 70, 190),
    "pink": (224, 148, 172),
}
SPECIAL_COLORS = {
    "gold": (246, 208, 62), "iron": (216, 216, 216), "copper": (192, 108, 68),
    "diamond": (92, 220, 214), "emerald": (60, 200, 96), "redstone": (190, 30, 26),
    "lapis": (40, 70, 170), "coal": (28, 28, 28), "netherite": (66, 58, 60),
    "amethyst": (150, 110, 200), "glowstone": (250, 216, 120), "sea_lantern": (190, 240, 230),
    "shroomlight": (246, 176, 96), "lantern": (248, 200, 110), "torch": (252, 214, 120),
    "glass": (196, 226, 236), "water": (58, 106, 200), "lava": (222, 106, 26),
    "grass": (110, 168, 74), "leaves": (68, 130, 56), "dirt": (134, 96, 66),
    "sand": (222, 208, 156), "gravel": (130, 126, 124), "snow": (242, 246, 248),
    "ice": (160, 200, 240), "wool": (216, 216, 216), "concrete": (200, 200, 200),
    "terracotta": (152, 94, 68), "moss": (86, 124, 56), "bamboo": (198, 178, 84),
}

AIR_NAMES = {"minecraft:air", "minecraft:cave_air", "minecraft:void_air"}

# 精确名优先：避免被更短的关键词抢先匹配
# （否则 stone_bricks 会被 "bricks" 染成红砖色、dark_oak_planks 会被 "oak" 染成橡木色）
EXACT_COLORS = {
    "bricks": (150, 90, 74),
    "stone_bricks": (122, 122, 122),
    "mossy_stone_bricks": (108, 122, 100),
    "cracked_stone_bricks": (112, 112, 112),
    "chiseled_stone_bricks": (118, 118, 118),
    "deepslate_bricks": (78, 78, 80),
    "deepslate_tiles": (66, 66, 68),
    "nether_bricks": (44, 22, 26),
    "red_nether_bricks": (90, 30, 32),
    "mud_bricks": (140, 106, 88),
    "quartz_bricks": (232, 226, 218),
    "end_stone_bricks": (216, 218, 158),
    "polished_blackstone_bricks": (48, 40, 44),
    "stone": (126, 126, 126),
    "cobblestone": (128, 128, 128),
    "mossy_cobblestone": (110, 122, 100),
    "smooth_stone": (158, 158, 158),
}

# 关键词按长度降序，保证更具体的词先命中（dark_oak 先于 oak、stone_bricks 先于 stone）
_WOOD_SORTED = sorted(WOOD_COLORS.items(), key=lambda kv: -len(kv[0]))
_SPECIAL_SORTED = sorted(SPECIAL_COLORS.items(), key=lambda kv: -len(kv[0]))
_STONE_SORTED = sorted(STONE_COLORS.items(), key=lambda kv: -len(kv[0]))
_COLOR_SORTED = sorted(COLOR_WORDS.items(), key=lambda kv: -len(kv[0]))


def block_color(name: str) -> tuple[int, int, int]:
    """按方块名推断基色：精确表 -> 木材 -> 特殊 -> 颜色词 -> 石材 -> 稳定哈希兜底。"""
    n = name.split("[")[0].removeprefix("minecraft:")
    if n in EXACT_COLORS:
        return EXACT_COLORS[n]

    for w, c in _WOOD_SORTED:
        if w in n:
            return c
    for w, c in _SPECIAL_SORTED:
        if w in n:
            return c
    for w, c in _COLOR_SORTED:
        if n.startswith(w + "_") or f"_{w}_" in n or n.endswith("_" + w):
            return c
    for w, c in _STONE_SORTED:
        if w in n:
            return c
    # 兜底：稳定哈希（同一方块每次颜色一致）
    h = abs(hash(n)) % 360
    import colorsys
    r, g, b = colorsys.hsv_to_rgb(h / 360.0, 0.32, 0.72)
    return (int(r * 255), int(g * 255), int(b * 255))


def build_palette_rgb(palette: list[str]) -> np.ndarray:
    """调色板索引 -> RGB，形状 (N,3) uint8。"""
    out = np.zeros((len(palette), 3), dtype=np.uint8)
    for i, name in enumerate(palette):
        if name in AIR_NAMES or name == "minecraft:air":
            out[i] = (0, 0, 0)
        else:
            out[i] = block_color(name)
    return out


# ============================================================================
# 面剔除
# ============================================================================
def exposed_masks(solid: np.ndarray):
    """
    返回等距视图可见的 3 个朝向的暴露掩码 (top, right, front)。
    坐标语义：top 面在 (x, y+1, z)，right 面在 (x+1, y, z)，front 面在 (x, y, z+1)。
    """
    up = np.zeros_like(solid)
    up[:, :-1, :] = solid[:, 1:, :]
    top = solid & ~up

    xp = np.zeros_like(solid)
    xp[:-1, :, :] = solid[1:, :, :]
    right = solid & ~xp

    zp = np.zeros_like(solid)
    zp[:, :, :-1] = solid[:, :, 1:]
    front = solid & ~zp
    return top, right, front


# ============================================================================
# 等距渲染
# ============================================================================
def make_templates(TW: int, TH: int, VH: int):
    """
    生成 3 个朝向的像素遮罩模板。参考点(0,0) = 体素底面中心在屏幕上的位置。
    返回 [(mask_flat_indices, dy0, dx0, brightness), ...]
    """
    pad = 2
    W = TW + 2 * pad
    Hh = TH + VH + 2 * pad
    ax = W / 2.0                      # 锚点在模板中的 x
    ay = TH / 2.0 + VH + pad          # 锚点在模板中的 y

    ys, xs = np.mgrid[0:Hh, 0:W].astype(np.float64)
    dx = xs - ax + 0.5
    dy = ys - ay + 0.5

    # 顶面菱形：中心在锚点上方 VH
    cy = dy + VH
    top = (np.abs(dx) / (TW / 2.0) + np.abs(cy) / (TH / 2.0)) <= 1.0

    # 左面（前面，-z 侧）：参数化 u∈[0,1] 沿 W->S 边，t∈[0,1] 向下
    u = (dx + TW / 2.0) / (TW / 2.0)
    t = (dy - u * (TH / 2.0)) / VH
    left = (u >= 0) & (u <= 1) & (t >= 0) & (t <= 1)

    # 右面（+x 侧）
    u2 = (TW / 2.0 - dx) / (TW / 2.0)
    t2 = (dy - u2 * (TH / 2.0)) / VH
    right = (u2 >= 0) & (u2 <= 1) & (t2 >= 0) & (t2 <= 1)

    # 合并为一个六边形模板：每个像素记录它属于哪个朝向（决定明暗）
    # 这样每个体素只需**一次**写入，遮挡关系完全由体素深度排序决定，
    # 避免"分朝向绘制导致前面的面无视深度覆盖顶面"的错误。
    mask = top | right | left
    face_id = np.zeros((Hh, W), dtype=np.int8)
    face_id[top] = 0
    face_id[right] = 1
    face_id[left] = 2

    axi, ayi = int(round(ax)), int(round(ay))
    yy, xx = np.nonzero(mask)
    # 相对锚点的行列偏移（可为负）——锚点即体素底面中心的屏幕位置
    return yy - ayi, xx - axi, face_id[yy, xx], axi, ayi


def render_iso(arr: np.ndarray, palette: list[str], tile: int = 2,
               max_pixels: int = 6_000_000, bg=(24, 26, 32), verbose=True):
    """等距立体渲染。返回 (H, W, 3) uint8。"""
    W_, H_, L_ = arr.shape
    solid = arr != 0
    if not solid.any():
        raise ValueError("模型为空，无可渲染内容")

    TW = max(1, tile)
    TH = max(1, TW // 2)
    VH = max(1, TH)

    # 自适应缩放：控制输出像素总量
    while True:
        pw = int((W_ + L_) * TW / 2) + 8
        ph = int((W_ + L_) * TH / 2 + H_ * VH) + 8
        if pw * ph <= max_pixels or TW <= 1:
            break
        TW -= 1
        TH = max(1, TW // 2)
        VH = max(1, TH)
    if verbose and TW != tile:
        print(f"     [缩放] tile {tile} -> {TW}（像素预算 {max_pixels}）")

    dy, dx, face_id, ax, ay = make_templates(TW, TH, VH)
    shade = np.array([1.0, 0.78, 0.58], dtype=np.float64)[face_id]   # (K,)
    rgb_pal = build_palette_rgb(palette)

    top, right, front = exposed_masks(solid)
    visible = top | right | front
    # np.nonzero 返回 (dim0,dim1,dim2) == (x,y,z)，arr 形状为 (W,H,L)
    xa, ya, za = np.nonzero(visible)
    if xa.size == 0:
        raise ValueError("没有可见方块")

    sxp_all = np.round((xa - za) * (TW / 2.0)).astype(np.int64)
    syp_all = np.round((xa + za) * (TH / 2.0) - ya * VH).astype(np.int64)
    min_x, max_x = int(sxp_all.min()), int(sxp_all.max())
    min_y, max_y = int(syp_all.min()), int(syp_all.max())

    cw = max(1, (max_x - min_x) + 2 * ax + 4)
    ch = max(1, (max_y - min_y) + 2 * ay + 4)

    buf = np.zeros((ch, cw, 3), dtype=np.float64)
    buf[:, :] = bg
    flat = buf.reshape(-1, 3)

    if verbose:
        print(f"     可见方块 {xa.size} 个（暴露面 顶/右/前 "
              f"{[int(m.sum()) for m in (top, right, front)]}），画布 {cw}×{ch}，tile={TW}，"
              f"模板 {dy.size} 像素")

    # 深度 = x+y+z；远的先写，近的后写覆盖之
    depth = (xa.astype(np.int64) + ya + za)
    order = np.argsort(-depth, kind="stable")
    sxp = sxp_all[order] - min_x + ax
    syp = syp_all[order] - min_y + ay
    d_sorted = depth[order]
    col = rgb_pal[arr[xa[order], ya[order], za[order]]].astype(np.float64)

    uniq = np.unique(-d_sorted)
    edges = np.append(np.searchsorted(-d_sorted, uniq, side="left"), d_sorted.size)
    lim = flat.shape[0]
    MAX_CHUNK = 20000        # 限制单次 fancy-index 的内存占用
    for bi in range(edges.size - 1):
        s, e = int(edges[bi]), int(edges[bi + 1])
        while s < e:
            t = min(e, s + MAX_CHUNK)
            idx = (syp[s:t, None] + dy[None, :]) * cw + (sxp[s:t, None] + dx[None, :])
            np.clip(idx, 0, lim - 1, out=idx)
            flat[idx] = col[s:t, None, :] * shade[None, :, None]
            s = t

    return np.clip(buf, 0, 255).astype(np.uint8)


def render_top(arr: np.ndarray, palette: list[str], cell: int = 1):
    """俯视高度图：每列取最高非空气方块，按高度做明暗。"""
    W_, H_, L_ = arr.shape
    solid = arr != 0
    heights = np.zeros((W_, L_), dtype=np.int32)
    topidx = np.zeros((W_, L_), dtype=np.uint16)
    for y in range(H_):
        layer = solid[:, y, :]
        if not layer.any():
            continue
        topidx[layer] = arr[:, y, :][layer]
        heights[layer] = y + 1

    rgb_pal = build_palette_rgb(palette)
    img = np.zeros((W_, L_, 3), dtype=np.float64)
    nz = heights > 0
    base = rgb_pal[topidx].astype(np.float64)
    hmax = max(1, int(heights.max()))
    shade = (0.55 + 0.45 * (heights[..., None] / hmax))
    img = np.where(nz[..., None], base * shade, np.array([22, 24, 30], dtype=np.float64))

    if cell > 1:
        img = np.repeat(np.repeat(img, cell, axis=0), cell, axis=1)
    # 转成 x 水平、z 垂直的常规朝向
    return np.clip(img, 0, 255).astype(np.uint8)


def render_side(arr: np.ndarray, palette: list[str], axis: str = "z", cell: int = 1):
    """
    正交视图：沿 axis 方向投影，每个像素取该视线上**最后一个**非空气方块。

    axis="z" → 正视图（画面水平 = x，垂直 = y，上 = 最高）
    axis="x" → 侧视图（画面水平 = z，垂直 = y，上 = 最高）
    注：两条分支都是"后写覆盖"，等价于从 +axis 侧看；轴向映射见 VISION_AXIS_NOTE，
    与 audit_schem 的 region → 世界坐标换算是同一套约定。
    """
    W_, H_, L_ = arr.shape
    solid = arr != 0
    rgb_pal = build_palette_rgb(palette)

    if axis == "z":
        idx = np.zeros((W_, H_), dtype=np.uint16)
        hit = np.zeros((W_, H_), dtype=bool)
        for z in range(L_):
            layer = solid[:, :, z]
            idx[layer] = arr[:, :, z][layer]
            hit |= layer
    else:
        # 侧视图：把 (H, L) 的 x 切片转置成 (L, H)，才能与 idx/hit 的朝向对齐
        # （早先这里漏了转置，该分支一跑就 IndexError —— 因为一直没人调用过）
        idx = np.zeros((L_, H_), dtype=np.uint16)
        hit = np.zeros((L_, H_), dtype=bool)
        for x in range(W_):
            layer = solid[x, :, :].T
            idx[layer] = arr[x, :, :].T[layer]
            hit |= layer

    img = np.where(hit[..., None], rgb_pal[idx].astype(np.float64),
                   np.array([22, 24, 30], dtype=np.float64))
    img = np.flipud(img.transpose(1, 0, 2))
    if cell > 1:
        img = np.repeat(np.repeat(img, cell, axis=0), cell, axis=1)
    return np.clip(img, 0, 255).astype(np.uint8)


# ============================================================================
# PNG 输出（纯 numpy + zlib，无 PIL 依赖）
# ============================================================================
def write_png(path: str, rgb: np.ndarray) -> None:
    """
    手写 PNG（8bit RGB，无外部依赖）。

    注意：PNG 每行开头需要 **1 个字节** 的 filter type（0 = None），
    不是 1 个像素。写成 3 字节会导致整幅图逐行错位（渲染出来像黑屏）。
    """
    h, w, _ = rgb.shape
    rgb8 = rgb.astype(np.uint8)
    lines = np.zeros((h, w * 3 + 1), dtype=np.uint8)
    lines[:, 1:] = rgb8.reshape(h, w * 3)
    raw = lines.tobytes()

    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(raw, 6))
    png += chunk(b"IEND", b"")
    with open(path, "wb") as f:
        f.write(png)


VISION_AXIS_NOTE = {
    "front": "水平 = x（左→右 = x 从小到大），垂直 = y（上 = 最高）",
    "side": "水平 = z（左→右 = z 从小到大），垂直 = y（上 = 最高）",
    "top": "水平 = z（左→右 = z 从小到大），垂直 = x（上 = 索引 0 → 下 = 最大）",
    "iso": "等轴测（透视），无法直接反算坐标：只给 region + 文字描述",
}


def write_vision_pack(arr, palette, out_dir: str, *, tile: int, max_pixels: int,
                      target: str = "", quiet: bool = False) -> dict:
    """
    视觉检查包：四视图（正/侧/俯/等轴测）+ 识图报告模板。

    四视图是给**多模态模型**看的：它按模板回填 vision_report.json，再由
    audit_schem.py --vision 合并进结构化错误清单（region 会自动换算成世界坐标区间）。
    """
    os.makedirs(out_dir, exist_ok=True)
    views = {
        "front": render_side(arr, palette, "z", cell=2),
        "side": render_side(arr, palette, "x", cell=2),
        "top": render_top(arr, palette, cell=2),
        "iso": render_iso(arr, palette, tile=tile, max_pixels=max_pixels, verbose=False),
    }
    files = {}
    for k, img in views.items():
        p = os.path.join(out_dir, f"view_{k}.png")
        write_png(p, img)
        files[k] = {"file": p, "size": [int(img.shape[1]), int(img.shape[0])]}
        if not quiet:
            print(f"     {k:<6} {img.shape[1]}×{img.shape[0]} -> {p}")

    tpl = {
        "schema": "mc-vision/1",
        "reviewer": "<填：执行识图的模型名>",
        "target": target,
        "views": ["front", "side", "top", "iso"],
        "_怎么用": "逐张看 view_*.png，只在**确实看到结构性错误**时写一条 findings；"
                   "没看出问题就保持 findings 为空并把 verdict 写 PASS。",
        "_定位要求": "每条必须能定位：优先给 bbox=[x0,y0,z0,x1,y1,z1]（世界坐标）；"
                     "拿不到世界坐标就给 region=[u0,v0,u1,v1]（归一化矩形，u 从左到右、v 从上到下），"
                     "审计脚本会按所选视图的轴映射自动换算。",
        "_视图轴映射": VISION_AXIS_NOTE,
        "_典型错误类型": ["屋顶塌陷", "门体悬空", "结构错位", "悬空方块", "构件缺失",
                          "比例失真", "镜像不对称", "其他"],
        "findings": [
            {
                "type": "<从典型错误类型里选或自拟>",
                "severity": "error 或 warning",
                "view": "iso 或 front 或 side 或 top",
                "region": [0.0, 0.0, 0.0, 0.0],
                "bbox": None,
                "note": "<看到什么、在哪个部位>"
            }
        ],
        "verdict": "PASS 或 FAIL"
    }
    tp = os.path.join(out_dir, "vision_report.template.json")
    with open(tp, "w", encoding="utf-8") as f:
        json.dump(tpl, f, ensure_ascii=False, indent=2)
    if not quiet:
        print(f"     识图模板 -> {tp}")
    return {"views": files, "template": tp}


# ============================================================================
# CLI
# ============================================================================
def main() -> int:
    ap = argparse.ArgumentParser(description=".schem 预览渲染（numpy 向量化）")
    ap.add_argument("--schem", required=True, help=".schem 路径")
    ap.add_argument("--out-dir", help="输出目录（默认与 .schem 同目录）")
    ap.add_argument("--tile", type=int, default=6, help="体素像素宽度（默认 6；越大越清晰，超出像素预算会自动下调）")
    ap.add_argument("--max-pixels", type=int, default=6_000_000, help="像素预算，超出自动降 tile")
    ap.add_argument("--top-cell", type=int, default=2, help="俯视图放大倍数")
    ap.add_argument("--no-top", action="store_true", help="不生成俯视图")
    ap.add_argument("--no-side", action="store_true", help="不生成正视图")
    ap.add_argument("--vision-pack", metavar="DIR",
                    help="额外输出视觉检查包：四视图（正/侧/俯/等轴测）+ 识图报告模板")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    t0 = time.time()
    arr, palette, shape = load_voxels(args.schem)
    t_load = time.time() - t0
    total = int((arr != 0).sum())
    if not args.quiet:
        print(f"载入 {args.schem}")
        print(f"     尺寸 {shape[0]}×{shape[1]}×{shape[2]}   非空气 {total} 方块   {t_load:.2f}s")

    out_dir = args.out_dir or os.path.dirname(os.path.abspath(args.schem))
    os.makedirs(out_dir, exist_ok=True)
    base = os.path.splitext(os.path.basename(args.schem))[0]

    t1 = time.time()
    iso = render_iso(arr, palette, tile=args.tile, max_pixels=args.max_pixels, verbose=not args.quiet)
    p_iso = os.path.join(out_dir, f"{base}.iso.png")
    write_png(p_iso, iso)
    t_iso = time.time() - t1
    if not args.quiet:
        print(f"     等距图 {iso.shape[1]}×{iso.shape[0]}  {t_iso:.2f}s -> {p_iso}")

    if not args.no_top:
        t2 = time.time()
        top = render_top(arr, palette, cell=args.top_cell)
        p_top = os.path.join(out_dir, f"{base}.top.png")
        write_png(p_top, top)
        if not args.quiet:
            print(f"     俯视图 {top.shape[1]}×{top.shape[0]}  {time.time()-t2:.2f}s -> {p_top}")

    if not args.no_side:
        t3 = time.time()
        side = render_side(arr, palette, "z", cell=args.top_cell)
        p_side = os.path.join(out_dir, f"{base}.side.png")
        write_png(p_side, side)
        if not args.quiet:
            print(f"     正视图 {side.shape[1]}×{side.shape[0]}  {time.time()-t3:.2f}s -> {p_side}")

    if args.vision_pack:
        if not args.quiet:
            print(f"     视觉检查包（四视图 + 识图模板）-> {args.vision_pack}")
        write_vision_pack(arr, palette, args.vision_pack, tile=args.tile,
                          max_pixels=args.max_pixels, target=base,
                          quiet=args.quiet)

    if not args.quiet:
        print(f"     总耗时 {time.time()-t0:.2f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
