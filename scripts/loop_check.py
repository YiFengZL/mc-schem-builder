#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
loop_check.py —— 智能体闭环的「一轮检查」驱动器

一轮 = **生成 → 执行 → 检查 → 反馈**（修复由生成侧按反馈做，见文末"下一步"）

    ① 生成+执行：跑轨道 B 骨架脚本（产出 .schem + report.json）
    ② 产物断言：verify_schem --assert（格式层 / 完整性层 / 规格层）
    ③ 视觉检查：渲染四视图（正/侧/俯/等轴测）+ 识图报告模板
    ④ 结构体检：audit_schem 四维静态指标（悬空占比 / 连通性 / 组件包围盒 / 对称轴偏差）
    ⑤ 状态机：连续通过计数、最佳轮次、终止判定，并打印**可定位的下一步动作**

终止条件：
    * 连续 pass_need（默认 2）轮所有指标达标且无视觉结构性错误 → 终止，交付最终结果
    * 到 max_rounds（默认 5）轮仍未通过 → 终止，输出最佳轮次 + 未解决错误清单

为什么分两步做视觉：③ 只负责把四视图与模板准备好，**识图那一步必须由多模态模型完成**
（本环境里就是 agent 自己读 view_*.png），回填 vision_report.json 后用 --vision 传回，
审计再把视觉错误并进同一份结构化清单。静态维度与视觉维度都过，整轮才算过。

用法：
    python loop_check.py --spec mc_builds/gangxia_north/spec.json --round 1
    python loop_check.py --spec ... --round 2 --vision <out>/audit/round_2/views/vision_report.json
    python loop_check.py --spec ... --round 3 --skip-vision     # 只跑静态四维（视觉记为 SKIP）
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import audit_schem as A                                      # noqa: E402
import render_preview as R                                   # noqa: E402
from build_lib import VoxelGrid                              # noqa: E402
from gen_schem import SCHEM_VERSION                          # noqa: E402
from spec_schema import load_style, validate_spec             # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))


def _run(cmd: list[str]) -> tuple[bool, str]:
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return p.returncode == 0, (p.stdout or "") + (p.stderr or "")


def _print_next(res: dict, loop: dict, args) -> None:
    """把下一步写成**可定位的动作**（禁止"不对，重来"）。"""
    errs = [f for f in res["findings"] if f["severity"] == "error"]
    print("\n──── 下一步（定向修复：按下表 bbox 改代码/参数，再跑下一轮）────")
    if not errs:
        if loop.get("status") == "PASS_TERMINATED":
            print("  无 error；已达终止条件 → 交付当前结果（产物 + 四视图 + audit.json）")
        elif args.vision or args.skip_vision:
            print(f"  无 error；为确认稳定再跑第 {args.round + 1} 轮（连续 {loop.get('pass_need')} 轮通过才终止）")
        else:
            print("  静态维度全部达标；只差视觉维度：读 views/view_*.png 回填 vision_report.json，"
                  f"再跑第 {args.round} 轮并用 --vision 传回")
    else:
        for f in errs[:12]:
            print(f"  - [{f['dim']}/{f['type']}] bbox={f['bbox']}")
            if f.get("note"):
                print(f"      {f['note']}")
            if f.get("hint"):
                print(f"      提示：{f['hint']}")
        if len(errs) > 12:
            print(f"  ...（共 {len(errs)} 条，完整清单见同轮 audit.json）")


def main() -> int:
    ap = argparse.ArgumentParser(description="智能体闭环：一轮完整检查（生成→执行→检查→反馈）")
    ap.add_argument("--spec", required=True, help="spec.json")
    ap.add_argument("--round", type=int, default=1, help="第几轮（决定落盘目录与状态机）")
    ap.add_argument("--max-rounds", type=int, default=5, help="最大轮次（默认 5）")
    ap.add_argument("--pass-need", type=int, default=2, help="连续通过轮数（默认 2）")
    ap.add_argument("--vision", help="本轮多模态识图报告 vision_report.json")
    ap.add_argument("--vision-auto", action="store_true",
                    help="自动调用多模态模型产出识图报告（需 VISION_BASE_URL/API_KEY/MODEL，"
                         "或 --vision-backend echo 自测链路）")
    ap.add_argument("--vision-backend", default="", help="透传给 vision_review.py：openai / ollama / echo")
    ap.add_argument("--vision-endpoint", default="", help="透传：多模态接口地址")
    ap.add_argument("--vision-model", default="", help="透传：多模态模型名")
    ap.add_argument("--skip-vision", action="store_true", help="本轮跳过视觉维度（记为 SKIP）")
    ap.add_argument("--skip-build", action="store_true", help="不重跑生成，只做检查（产物已存在时）")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    spec_path = os.path.abspath(args.spec)
    with open(spec_path, encoding="utf-8") as f:
        spec = json.load(f)
    name = str(spec.get("name"))
    out_cfg = spec.get("output", {}) or {}
    out_dir = os.path.abspath(out_cfg.get("dir") or f"mc_builds/{name}")
    base = str(out_cfg.get("basename") or name)
    bp = os.path.join(os.path.dirname(spec_path), f"build_{name}.py")
    round_dir = os.path.join(out_dir, "audit", f"round_{args.round}")
    views_dir = os.path.join(round_dir, "views")
    t0 = time.time()

    print(f"\n########## 闭环第 {args.round} 轮 / 上限 {args.max_rounds} 轮 · {name} ##########")

    # ---- ① 生成 + 执行 ----
    extra: list[dict] = []
    if not args.skip_build:
        if not os.path.isfile(bp):
            print(f"[1/5] 生成+执行  FAIL  找不到骨架脚本 {bp}")
            return 1
        ok, log = _run([sys.executable, bp])
        print(f"[1/5] 生成+执行  {'OK' if ok else 'FAIL'}")
        if not ok:
            tail = "\n".join(log.strip().splitlines()[-12:])
            print(tail)
            extra.append({"id": "P-001", "dim": "生成", "type": "build 脚本执行失败",
                          "severity": "error", "bbox": None, "note": tail[-400:],
                          "hint": "先修 build() 里的异常或参数越界，再重跑本轮"})
    else:
        print("[1/5] 生成+执行  跳过（--skip-build）")

    # ---- ② 产物断言 ----
    rep = os.path.join(out_dir, f"{base}.report.json")
    outputs = []
    if os.path.isfile(rep):
        with open(rep, encoding="utf-8") as f:
            outputs = json.load(f).get("outputs", [])
    vp = os.path.join(HERE, "verify_schem.py")
    vfail = 0
    for o in outputs:
        ok, log = _run([sys.executable, vp, "--assert", str(o["file"])])
        if not ok:
            vfail += 1
            bad = [ln.strip() for ln in log.splitlines() if "X " in ln or "FAIL" in ln]
            extra.append({
                "id": f"V-1{vfail:02d}", "dim": "产物", "type": "回读断言未过", "severity": "error",
                "bbox": list(o.get("bbox") or []) or None,
                "note": f"{os.path.basename(str(o['file']))}：" + "；".join(bad[:3]),
                "hint": "该片 .schem 与 report 不一致：优先怀疑分片基点 / 方块名 / 尺寸声明",
            })
    print(f"[2/5] 产物断言  {'OK' if vfail == 0 else 'FAIL'}   （{len(outputs)} 个产物文件）")

    # ---- ③ 视觉检查包：一次构建 → 落「整体」临时 .schem → 四视图 ----
    style_def = load_style(str(spec["style"]))
    ok_v, verrs, _warns, params = validate_spec(spec, style_def)
    solid = None
    origin = (0, 0, 0)
    bbox_str = ""
    if ok_v and os.path.isfile(bp):
        mod = A._load_module(bp)
        g = VoxelGrid()
        mod.build(g, params)
        b = g.bounds()
        if b is not None:
            origin = (b[0], b[1], b[2])
            bbox_str = ",".join(str(int(v)) for v in b)
            with tempfile.TemporaryDirectory() as td:
                g.to_mcschematic((b[0], b[1], b[2])).save(td, f"{base}_full", SCHEM_VERSION)
                arr, palette, _shape = R.load_voxels(os.path.join(td, f"{base}_full.schem"))
                R.write_vision_pack(arr, palette, views_dir, tile=6, max_pixels=6_000_000,
                                    target=base, quiet=args.quiet)
                solid = arr != 0
        del mod
    else:
        for e in verrs:
            extra.append({"id": "P-002", "dim": "参数", "type": "前置校验未过", "severity": "error",
                          "bbox": None, "note": e, "hint": "先修 spec / 风格声明"})
    print(f"[3/5] 视觉检查包  {'OK' if solid is not None else 'SKIP'}   -> {views_dir}")

    # ---- ③.5 视觉接入：自动调用多模态模型产出识图报告 ----
    vision_path = args.vision
    vr = os.path.join(HERE, "vision_review.py")
    if vision_path is None and args.vision_auto and solid is not None:
        vout = os.path.join(views_dir, "vision_report.json")
        cmd = [sys.executable, vr, "--views", views_dir, "--out", vout]
        if bbox_str:
            cmd += [f"--bbox={bbox_str}"]      # 等号形式：坐标含负号，空格形式会被 argparse 当成选项
        if args.vision_backend:
            cmd += ["--backend", args.vision_backend]
        if args.vision_endpoint:
            cmd += ["--endpoint", args.vision_endpoint]
        if args.vision_model:
            cmd += ["--model", args.vision_model]
        ok_vr, log_vr = _run(cmd)
        print(("[3.5/5] 视觉接入  OK  多模态识图报告 -> " + vout) if ok_vr
              else "[3.5/5] 视觉接入  跳过（未配置多模态模型 / 调用失败）—— 本轮视觉维度记 NOT_RUN，不会静默通过")
        if ok_vr:
            vision_path = vout
        else:
            tail = "\n".join(ln for ln in log_vr.strip().splitlines() if ln.strip())[:400]
            if tail:
                print("       " + tail.replace("\n", "\n       "))
            # 顺手把「给模型看的提示词 + JSON 契约」导出来，便于 agent/人工回填
            _run([sys.executable, vr, "--views", views_dir, f"--bbox={bbox_str}",
                  "--prompt-out", os.path.join(views_dir, "review_prompt.txt")])
            print(f"       已导出提示词 -> {os.path.join(views_dir, 'review_prompt.txt')}"
                  f"（回填为 vision_report.json 后用 --vision 传回）")

    if solid is None:
        print("[4/5] 结构体检  跳过（前置未过）")
        print("[5/5] 状态机    本轮 FAIL")
        for f in extra:
            print(f"  - [{f['dim']}/{f['type']}] {f.get('note')}")
        return 1

    # ---- ④ 体检 + ⑤ 状态机 ----
    cfg = A.cfg_from_spec(spec)
    res = A.audit(solid, origin, cfg, name=name, round_no=args.round, vision_path=vision_path,
                  vision_skip=args.skip_vision, state_path=os.path.join(out_dir, ".audit_state.json"),
                  max_rounds=args.max_rounds, pass_need=args.pass_need, extra=extra)
    A.print_report(res)
    outp = os.path.join(round_dir, "audit.json")
    os.makedirs(round_dir, exist_ok=True)
    with open(outp, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False, indent=2)
    print(f"\n结构化结果 -> {outp}")
    if vision_path is None and not args.skip_vision:
        print(f"识图模板   -> {os.path.join(views_dir, 'vision_report.template.json')}"
              f"（填好另存为 vision_report.json 后用 --vision 传回，或加 --vision-auto 自动接入）")
    loop = res.get("loop") or {}
    print(f"[4/5] 结构体检  {res['verdict']}      [5/5] 状态机  {loop.get('status')}")
    print(f"本轮用时 {time.time() - t0:.1f}s")
    _print_next(res, loop, args)
    return 0 if res["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
