#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scaffold.py —— 轨道 B：为"风格库里没有的形态"生成骨架，模型只填 build()

两条轨道的分工
--------------
    轨道 A（gen_schem.py）  ：需求能映射到已有风格 → 只改 spec 字段，几何由风格 builder 负责
    轨道 B（本文件）        ：风格库里没有的形态（哥特飞扶壁、螺旋塔、异形穹顶…）
                              → 本文件生成骨架，**模型只写 build() 里的几何逻辑**

骨架已经把易错的部分全部封装好：依赖导入、参数合并、分片、导出、报告落盘。
模型不接触 mcschematic 调用，因此不会重犯 API 层的坑。

用法：
    python scaffold.py --spec spec.json --out build_spiral_tower.py
    python scaffold.py --spec spec.json --out build_x.py --check   # 生成并做语法检查

生成后：
    1) 编辑 build() 函数体
    2) python build_<name>.py      # 运行时读取 spec.json 并做前置校验
    3) python verify_schem.py --assert <产物>.schem

迭代：改参数只改 spec.json 再重跑 build_<name>.py（不会覆盖 build() 几何）；
      只有改几何才编辑 build()。spec.json 始终是唯一真相源。
"""

from __future__ import annotations

import argparse
import json
import os
import py_compile
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from palette_1_21_1 import (  # noqa: E402
    DATA_VERSION, MC_VERSION, MCS_VERSION_ENUM, SPONGE_VERSION,
)
from spec_schema import load_style, merge_params  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))

TEMPLATE = '''#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
@@NAME@@ —— 轨道 B 自定义几何建筑（由 scaffold.py 生成）

生成时间 : @@TS@@
风格参考 : @@STYLE@@
需求描述 : @@INTENT@@

只需修改 build() 函数体；其余部分（校验/分片/导出/报告）无需改动。

**spec.json 是唯一真相源**：本文件在运行时重新读取 @@SPEC_PATH@@，
不内嵌参数副本。所以"塔楼加高 3 格"这类迭代 = 改 spec.json → 重跑本脚本，
既不用重新生成骨架，也不会覆盖 build() 里的几何逻辑。
"""

import json
import os
import sys

SCRIPTS_DIR = r"@@SCRIPTS_DIR@@"
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

from build_lib import (          # noqa: F401  几何基元
    AIR, DEFAULT_MAX_DIM, VoxelGrid, _rect_ring, arch_gate, barrel_vault,
    battlements, battlements_solid, buttress, carve_gate, cone_roof,
    connect_fence_like, dome, door_frame, eave_band, fill_ground, gable_roof,
    hash_pick, hip_roof, pillar, plan_slices, railing_run, round_tower,
    slice_grid, stairs_flight, wall_ring, weather_blend, window_run,
)
import geom_lib as geom          # noqa: F401  声明式几何合成层（自建任意几何体）
from palette_1_21_1 import (     # noqa: F401  带属性校验的构造器
    button as p_button, door as p_door, fence as p_fence,
    fence_gate as p_fence_gate, ladder as p_ladder, leaves as p_leaves,
    pane as p_pane, pressure_plate as p_pressure_plate, slab as p_slab,
    stairs as p_stairs, trapdoor as p_trapdoor, wall as p_wall,
    wall_torch as p_wall_torch, validate_blockstate,
)
from gen_schem import export    # 分片 + 导出 + 报告
from spec_schema import load_style, validate_spec   # 运行时读 spec + 前置校验

SPEC_PATH = r"@@SPEC_PATH@@"


def load_spec() -> tuple[dict, dict]:
    """读 spec.json → 前置校验 → 返回 (spec, 已合并风格默认值的参数 S)。"""
    with open(SPEC_PATH, encoding="utf-8") as f:
        spec = json.load(f)
    style = spec.get("style")
    style_def = load_style(str(style)) if style else None
    ok, errors, warnings, params = validate_spec(spec, style_def)
    for w in warnings:
        print(f"[WARN] {w}")
    if not ok:
        print("[FAIL] spec 校验未通过，已中止生成：")
        for e in errors:
            print(f"   X {e}")
        raise SystemExit(1)
    return spec, params


# ============================================================================
#  ↓↓↓  只需要改这一个函数  ↓↓↓
# ============================================================================
def build(g: VoxelGrid, S: dict) -> None:
    r"""
    需求：@@INTENT@@

    ── 可用几何基元（build_lib，已导入）──────────────────────────────
      wall_ring(g, x0, z0, x1, z1, y0, y1, block, thickness=1, gate=None)
          矩形墙圈；gate={"side":"south","center":30,"width":5,"height":6} 可留门洞
      round_tower(g, cx, cz, radius, y0, y1, block, hollow=True, thickness=1)
          圆塔/圆柱壳；radius=3 -> 外径 7
      cone_roof(g, cx, cz, radius, y0, block, slope="outward") -> int
          锥形/攒尖顶，返回顶层 y；slope 反了就改 "inward"
      gable_roof(g, x0, z0, x1, z1, y0, block, ridge_axis="z", overhang=0,
                 gable_block=None) -> int
          双坡（人字）顶，沿 ridge_axis 起脊；overhang 挑檐；gable_block 补山墙三角
      hip_roof(g, x0, z0, x1, z1, y0, block, overhang=0) -> int
          四坡（庑殿）顶，短轴先收成正脊；正方形收成尖顶
      barrel_vault(g, x0, z0, x1, z1, y0, block, axis="z", thickness=1) -> int
          筒拱（半圆拱顶），沿 axis 拉出；半径由垂直轴的跨度决定
      dome(g, cx, cz, radius, y0, block, thickness=1) -> int
          半球穹顶壳（球心在 y0）
          ↑ 屋顶族只生成"屋面壳"，不填屋面下方实体；楼梯 facing 自动算（指向脊/拱顶中线）
      battlements(g, x0, z0, x1, z1, y, block, period=2, exclude=None)
          城垛（一实一空）；exclude(x,z)->bool 可跳过某处（如塔内）
      battlements_solid(g, x0, z0, x1, z1, y, block, thickness=1)
          实心压顶
      carve_gate(g, side, center, width, height, wall_coord, depth=1, arch=True)
          在墙面上挖拱形/矩形门洞
      arch_gate(g, x0, x1, y0, y1, z, block, depth=1)
          在**实体块**中挖半圆拱洞（适合桥洞、壁龛）
      pillar(g, x, z, y0, y1, block, radius=0)
          立柱；radius>0 变成圆柱
      fill_ground(g, x0, z0, x1, z1, y, block, thickness=1)
          地坪/地基

    ── 细节族基元（B3；side/wall_coord 约定同 carve_gate）────────────────
      window_run(g, side, wall_coord, start, y0, count=1, spacing=2, width=1,
                 height=2, depth=1, glass_block=..., frame_block=None, sill_block=None)
          一排窗：窗洞 + 玻璃 + 可选窗框/窗台
      door_frame(g, side, wall_coord, center, y0=0, width=1, height=2, depth=1,
                 door_block=None, frame_block=None, hinge="left", door_facing=None)
          门洞；width=1/height>=2 且给 door_block 时自动放上下两格门
      stairs_flight(g, x0, z0, y0, steps, block, dir="south", width=1, under_block=None) -> int
          直跑楼梯；dir 既是上行方向也是 facing；under_block 可填实梯下三角
      buttress(g, side, wall_coord, u, y0, y1, block, depth=1, width=1, taper=0)
          扶壁（向墙外挑出；taper 做底部收分）
      railing_run(g, p0, p1, y, post_block, rail_block=None, post_every=2, height=1)
          直线栏杆（含立柱）；线段按参数方程体素化，**任意方向**均可（不限于轴对齐）；栅栏类请随后调用 connect_fence_like(g)
      eave_band(g, x0, z0, x1, z1, y, block, projection=1, thickness=1,
                upper_block=None, upper_inset=1)
          腰线/挑檐带；thickness>=projection+1 时与墙线连成一体
      connect_fence_like(g) -> int
          收尾修正：给栅栏/玻璃板/铁栏杆按四邻补齐连接属性（必做，否则游戏里不连）

    ── 材质族基元（B4；**确定性**，无随机数，可安全参与 diff 门禁）──────
      hash_pick(x, y, z, choices, salt=0) -> str
          按坐标哈希选材（同坐标恒同结果）
      weather_blend(x, y, z, base, aged, y0, y1, ratio=0.35, salt=0) -> str
          近地做旧渐变

    ── 声明式几何层（geom_lib；风格库没有的形态在这里自建）──────────────
      几何体 = 隐式成员判定 contains(x,y,z)；彼此可用 并(|) / 交(&) / 差(-) 合成，
      再统一体素化。既有基元的几何（圆/矩形/球/长方体/直线…）都是这一层的具体化。
        geom.Segment3(p0, p1, radius=0)      空间直线段（**参数方程** P(t)=p0+t(p1-p0)；radius>0 管状加粗）
        geom.Circle2(cx, cz, r, inner=..)    闭合圆（圆盘/圆环）
        geom.Ellipse2(cu, cv, ru, rv, inner=..)  椭圆
        geom.Rect2(u0, v0, u1, v1, t=0)      矩形（t=0 实心 / t≥1 边框）
        geom.Parallelogram2(u0, v0, u1, v1, slant=..)  平行四边形（逐行斜切；斜墙/单坡）
        geom.Ball(cx, cy, cz, r, inner=.., y0=.., y1=..)   球/球壳/**部分球面**
        geom.Box(p0, p1, mode="solid"|"hollow"|"frame", thickness=..)  长方体三态
        geom.HalfSpace(p0, normal, bounds)   以**法向量**定义的半空间
        geom.FunctionSurface(f, x0, x1, z0, z1, thickness=..)  **二元函数曲面** y=f(x,z)
        geom.SpaceCurve(f, 0, 1, samples=.., radius=..)  **空间（闭合）曲线**（参数方程 P(t)）
        geom.helix(cx, cz, r, y0, y1, turns)  螺旋线参数函数（喂给 SpaceCurve）
        geom.bezier(pts)                      **贝塞尔曲线**参数函数（控制点 ≥2；de Casteljau）
        geom.torus_knot(cx, cz, R, r, p, q)   **(p,q) 环面纽结**参数函数（闭合；(2,3)=三叶结）
        geom.Prism(shape2, "xz"|"xy"|"yz", lo, hi)  二维图形沿第三轴拉伸
        geom.fill(g, solid, block) / geom.carve(g, solid)   体素化
      例：圆环塔   = geom.carve(g, geom.Prism(geom.Circle2(cx, cz, R, inner=R-1), "xz", y0, y1))
          斜墙     = geom.Prism(geom.Parallelogram2(0, 0, L-1, H-1, lean), "xy", 0, z1)
          曲面屋面 = geom.FunctionSurface(lambda x, z: h + rise*math.sin(math.pi*x/L), 0, L-1, 0, W-1)
          螺旋坡道 = geom.SpaceCurve(geom.helix(0, 0, 6, 0, 20, 3), samples=256, radius=1)
          弧形栈道 = geom.SpaceCurve(geom.bezier([(0,0,0), (12,6,4), (24,0,0)]), samples=256, radius=1)
          纽结雕塑 = geom.SpaceCurve(geom.torus_knot(0, 0, 8, 2, 2, 3), samples=512, radius=1)

    ── VoxelGrid 方法 ─────────────────────────────────────────────
      set(x, y, z, block)            放方块
      set_if_air(x, y, z, block)     仅在空位放
      get(x, y, z) -> str            读方块
      carve(x, y, z)                 挖空
      fill_box(p0, p1, block, hollow=False, thickness=1)
      clear_box(p0, p1)              区域挖空
      count() / bounds() / size()
      translate(dx, dy, dz) -> VoxelGrid        整体平移（新网格）
      mirror(axis, pivot=None) -> VoxelGrid     镜像；**朝向自动翻转**
      rotate_90(times=1, pivot=None) -> VoxelGrid  绕 y 轴旋转；**朝向自动旋转**
      blit(other, to=(0,0,0))       把另一网格平移后并入本网格
          ↑ 对称建筑可「只砌一半 + mirror + blit」，比手写两份可靠

    ── 构造器（palette，已导入）必须用于带朝向的方块 ────────────────
      p_stairs("minecraft:stone_brick_stairs", facing="north", half="bottom")
      p_slab("minecraft:stone_brick_slab", "top")
      p_wall("minecraft:cobblestone_wall", north="low")
      p_trapdoor("minecraft:oak_trapdoor", half="top")
      p_door("minecraft:oak_door", half="lower", hinge="left")
      p_fence("minecraft:oak_fence", east=True, west=True)      # 栅栏
      p_pane("minecraft:glass_pane", east=True, west=True)      # 玻璃板/铁栏杆
      p_fence_gate("minecraft:oak_fence_gate", facing="east")   # 栅栏门
      p_button("minecraft:oak_button", face="wall", facing="north")
      p_pressure_plate("minecraft:oak_pressure_plate")
      p_ladder(facing="north")                                  # 梯子
      p_wall_torch(facing="north")                              # 墙上的火把
      p_leaves("oak")            # 树叶；默认 persistent=true，粘贴后不会衰减消失

    ── 硬性提醒 ────────────────────────────────────────────────
      * 坐标：x=宽(东西)  y=高  z=长(南北)，可为负；导出时自动归一化
      * 楼梯/台阶/墙/活板门**必须**显式给朝向，否则全朝默认方向
      * 方块名必须存在于 MC @@MC_VERSION@@，否则 Litematica 会**静默替换成空气**
        —— 写完用 `python block_check.py 本文件` 自查
    """
@@BODY@@


# ============================================================================
#  ↓↓↓  以下无需修改  ↓↓↓
# ============================================================================
def main() -> int:
    spec, S = load_spec()                      # 每次运行都重新读 spec.json
    out = spec.get("output", {}) or {}
    out_dir = os.path.abspath(out.get("dir") or f"mc_builds/{spec.get('name')}")
    basename = str(out.get("basename") or spec.get("name"))
    max_dim = int(out.get("slice_max_dim") or DEFAULT_MAX_DIM)

    g = VoxelGrid()
    build(g, S)
    if g.count() == 0:
        print("[FAIL] build() 没有产生任何方块")
        return 1

    report = export(g, out_dir, basename, max_dim)
    report.update({
        "ok": True,
        "name": spec.get("name"),
        "style": spec.get("style", "custom"),
        "mode": "track_b",
        "intent": spec.get("intent", ""),
        "spec": SPEC_PATH,
        "mc_version": "@@MC_VERSION@@",
        "data_version": @@DATA_VERSION@@,
        "sponge_version": @@SPONGE_VERSION@@,
    })
    rp = os.path.join(out_dir, f"{basename}.report.json")
    with open(rp, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print(f"[OK] {report['blocks_total']} 方块 → {out_dir}")
    print(f"     含空气尺寸 {report['size']}  分片 {report['slice_count']} 片")
    for k, v in list(report["materials"].items())[:12]:
        print(f"     {v:>8}  {k}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''

BODY_PLACEHOLDER = '''    # TODO: 在这里实现几何逻辑
    size = int(S.get("size", 32))

    # 示例（请替换）：
    # 地基
    # fill_ground(g, 0, 0, size - 1, size - 1, 0, "minecraft:stone_bricks", 2)
    # 四面墙
    # wall_ring(g, 0, 0, size - 1, size - 1, 1, 8, "minecraft:stone_bricks", 1)
    # 四角塔
    # for cx, cz in ((0, 0), (0, size - 1), (size - 1, 0), (size - 1, size - 1)):
    #     round_tower(g, cx, cz, 2, 0, 12, "minecraft:stone_bricks", hollow=True)
    #     cone_roof(g, cx, cz, 2, 13, "minecraft:dark_oak_stairs", slope="outward")

    raise NotImplementedError("请在 build() 中实现几何逻辑（删掉本行）")
'''


def build_skeleton(spec: dict, scripts_dir: str, spec_path: str) -> str:
    style = spec.get("style")
    if style:
        # 只做一次前置体检，尽早暴露风格名/参数问题；参数本体由生成脚本运行时读取
        sd = load_style(str(style))
        _params, errs, _ = merge_params(sd, spec.get("params"))
        if errs:
            raise SystemExit("[FAIL] 参数校验未通过：\n   " + "\n   ".join(errs))

    text = TEMPLATE
    repl = {
        "@@NAME@@": str(spec.get("name", "custom_build")),
        "@@TS@@": time.strftime("%Y-%m-%d %H:%M:%S"),
        "@@STYLE@@": str(style or "custom"),
        "@@INTENT@@": str(spec.get("intent") or "（未填写 intent；建议在 spec 里用 intent 字段写清需求）"),
        "@@SCRIPTS_DIR@@": scripts_dir,
        "@@SPEC_PATH@@": spec_path,
        "@@MC_VERSION@@": MC_VERSION,
        "@@DATA_VERSION@@": str(DATA_VERSION),
        "@@SPONGE_VERSION@@": str(SPONGE_VERSION),
        "@@BODY@@": BODY_PLACEHOLDER.rstrip(),
    }
    for k, v in repl.items():
        text = text.replace(k, v)
    return text


def main() -> int:
    ap = argparse.ArgumentParser(description="轨道 B：生成自定义几何骨架")
    ap.add_argument("--spec", required=True, help="spec.json（建议含 intent 字段描述需求）")
    ap.add_argument("--out", required=True, help="输出的 build_*.py 路径")
    ap.add_argument("--scripts-dir", default=HERE, help="skill 的 scripts 目录（嵌入到骨架中）")
    ap.add_argument("--check", action="store_true", help="生成后做语法检查")
    args = ap.parse_args()

    spec_path = os.path.abspath(args.spec)
    with open(spec_path, encoding="utf-8") as f:
        spec = json.load(f)

    text = build_skeleton(spec, args.scripts_dir, spec_path)
    out = os.path.abspath(args.out)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"[OK] 骨架已生成：{out}")
    print(f"     风格参考：{spec.get('style', 'custom')}   需求：{spec.get('intent', '(未填)')}")
    print(f"     下一步：编辑 build() 函数体 → python {os.path.basename(out)}")
    print(f"     参数迭代：只改 {spec_path} 再重跑该脚本即可（不会覆盖 build()）")

    if args.check:
        try:
            with tempfile.TemporaryDirectory() as td:
                py_compile.compile(out, cfile=os.path.join(td, "x.pyc"), doraise=True)
            print("     [OK] 语法检查通过")
        except py_compile.PyCompileError as e:
            print(f"     [FAIL] 语法错误：{e}")
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
