#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
spec_schema.py —— spec.json 的结构定义、风格加载与校验（轨道 A 的前置）

核心思想：**spec.json 是唯一真相源**
    几何参数全部落进一个 JSON；迭代 = 改其中几个字段重跑，
    而不是让模型重写整个脚本。这是"微调不跑偏"的根本保证。

两类文件
--------
spec.json          —— 你的建筑（唯一真相源）
references/styles/*.json —— 风格预设（声明该风格有哪些参数、默认值、取值范围、约束）

合并规则
--------
    最终参数 = 风格默认值  <-  spec["params"] 覆盖
    未在风格中声明的参数 -> warning（多为笔误），但仍会传入 builder

校验分三层
----------
    1) 结构层：schema_version / name / style / params 类型
    2) 取值层：min / max / choices
    3) 语义层：风格自定义 constraints（跨字段约束）+ block 类型走方块白名单
"""

from __future__ import annotations

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from palette_1_21_1 import (  # noqa: E402
    MC_VERSION, MCS_VERSION_ENUM, validate_blockstate,
)

SCHEMA_VERSION = 1
HERE = os.path.dirname(os.path.abspath(__file__))
STYLES_DIR = os.path.abspath(os.path.join(HERE, "..", "references", "styles"))

NAME_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_\-]{0,63}$")


# ---------------------------------------------------------------------------
# 风格加载
# ---------------------------------------------------------------------------
def list_styles() -> list[dict]:
    """列出全部风格预设。"""
    out = []
    if not os.path.isdir(STYLES_DIR):
        return out
    for fn in sorted(os.listdir(STYLES_DIR)):
        if not fn.endswith(".json"):
            continue
        try:
            with open(os.path.join(STYLES_DIR, fn), encoding="utf-8") as f:
                d = json.load(f)
            out.append(d)
        except Exception as e:
            out.append({"style": fn[:-5], "error": str(e)})
    return out


def load_style(style_name: str) -> dict:
    p = os.path.join(STYLES_DIR, f"{style_name}.json")
    if not os.path.isfile(p):
        avail = ", ".join(s.get("style", "?") for s in list_styles())
        raise FileNotFoundError(f"未知风格 {style_name!r}；可用：{avail}")
    with open(p, encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# 参数合并
# ---------------------------------------------------------------------------
def merge_params(style_def: dict, user_params: dict | None) -> tuple[dict, list[str], list[str]]:
    """
    返回 (params, errors, warnings)。
    params 里每个值都是已做类型归一化的最终值。
    """
    errors: list[str] = []
    warnings: list[str] = []
    decl: dict = style_def.get("params", {})
    user_params = user_params or {}
    if not isinstance(user_params, dict):
        return {}, [f"spec.params 必须是对象，收到 {type(user_params).__name__}"], []

    merged: dict = {}
    for key, meta in decl.items():
        if key in user_params:
            merged[key] = user_params[key]
        else:
            merged[key] = meta.get("default")

    for key, val in user_params.items():
        if key not in decl:
            warnings.append(f"参数 {key!r} 不在风格 {style_def.get('style')} 的声明中（可能是笔误，已原样传入）")
            merged[key] = val

    for key, meta in decl.items():
        val = merged.get(key)
        t = meta.get("type", "str")
        if val is None:
            if meta.get("required"):
                errors.append(f"参数 {key} 必填但缺失")
            continue
        # 类型归一化
        try:
            if t == "int":
                val = int(val)
            elif t == "float":
                val = float(val)
            elif t == "bool":
                if isinstance(val, str):
                    val = val.strip().lower() in ("true", "1", "yes", "on")
                else:
                    val = bool(val)
            elif t in ("str", "block", "choice"):
                val = str(val)
        except (TypeError, ValueError):
            errors.append(f"参数 {key} 需要 {t}，收到 {val!r}")
            continue
        merged[key] = val

        if t in ("int", "float"):
            lo, hi = meta.get("min"), meta.get("max")
            if lo is not None and val < lo:
                errors.append(f"参数 {key}={val} 小于下限 {lo}")
            if hi is not None and val > hi:
                errors.append(f"参数 {key}={val} 大于上限 {hi}")
        if t == "choice":
            ch = meta.get("choices") or []
            if ch and val not in ch:
                errors.append(f"参数 {key}={val!r} 不在允许值 {ch}")
        if t == "block":
            ok, msg = validate_blockstate(val)
            if not ok:
                errors.append(f"参数 {key} 的方块 {val!r} 非法：{msg}")

    return merged, errors, warnings


# 约束表达式可用的安全内置（无 IO、无导入、无反射）
SAFE_BUILTINS = {
    "min": min, "max": max, "abs": abs, "round": round,
    "int": int, "float": float, "len": len, "sum": sum,
    "any": any, "all": all, "sorted": sorted,
}


def eval_constraints(style_def: dict, params: dict) -> list[str]:
    """执行风格自定义的跨字段约束。表达式只能访问 params 的键与 SAFE_BUILTINS。"""
    errs: list[str] = []
    for c in style_def.get("constraints", []):
        expr, msg = c.get("expr"), c.get("msg", "约束未通过")
        if not expr:
            continue
        try:
            env = dict(SAFE_BUILTINS)
            env.update(params)
            ok = eval(expr, {"__builtins__": SAFE_BUILTINS}, env)  # noqa: S307 (本地工具，受限命名空间)
        except Exception as e:
            errs.append(f"约束 {expr!r} 求值失败：{e}")
            continue
        if not ok:
            errs.append(f"{msg}（约束：{expr}）")
    return errs


# ---------------------------------------------------------------------------
# spec 校验
# ---------------------------------------------------------------------------
def validate_spec(spec: dict, style_def: dict | None = None) -> tuple[bool, list[str], list[str], dict]:
    """
    返回 (ok, errors, warnings, merged_params)。
    """
    errors: list[str] = []
    warnings: list[str] = []

    if not isinstance(spec, dict):
        return False, ["spec 根必须是 JSON 对象"], [], {}

    sv = spec.get("schema_version", SCHEMA_VERSION)
    if sv != SCHEMA_VERSION:
        warnings.append(f"schema_version={sv}，当前工具版本为 {SCHEMA_VERSION}")

    name = spec.get("name")
    if not name:
        errors.append("缺少 name 字段（输出文件名）")
    elif not NAME_RE.match(str(name)):
        errors.append(f"name={name!r} 非法：只能用字母/数字/下划线/连字符，长度 1-64")

    style = spec.get("style")
    if not style:
        errors.append("缺少 style 字段")
    elif style_def is None:
        try:
            style_def = load_style(str(style))
        except FileNotFoundError as e:
            return False, [str(e)], warnings, {}

    mcv = spec.get("mc_version", MC_VERSION)
    if mcv != MC_VERSION:
        errors.append(
            f"spec.mc_version={mcv} 与工具内置版本 {MC_VERSION} 不一致；"
            f"如需换版本请一并替换 palette 与注册表"
        )

    params, perr, pwarn = merge_params(style_def, spec.get("params"))
    errors += perr
    warnings += pwarn
    errors += eval_constraints(style_def, params)

    out = spec.get("output", {}) or {}
    if not isinstance(out, dict):
        errors.append("output 必须是对象")
    else:
        md = out.get("slice_max_dim", 256)
        try:
            md = int(md)
            if md < 64:
                errors.append(f"output.slice_max_dim={md} 过小（建议 ≥64，默认 256）")
        except (TypeError, ValueError):
            errors.append(f"output.slice_max_dim 需要整数，收到 {md!r}")

    return (not errors), errors, warnings, params


def spec_from_style(style_name: str, name: str | None = None) -> dict:
    """按风格默认值生成一份可直接跑的 spec。"""
    sd = load_style(style_name)
    params = {k: v.get("default") for k, v in sd.get("params", {}).items()}
    return {
        "schema_version": SCHEMA_VERSION,
        "name": name or f"{style_name}_build",
        "style": style_name,
        "mc_version": MC_VERSION,
        "params": params,
        "output": {
            "dir": f"mc_builds/{name or style_name}",
            "basename": name or style_name,
            "slice_max_dim": 256,
        },
    }


def describe_style(style_def: dict) -> str:
    """把风格渲染成人类可读的参数表（供模型/用户参考）。"""
    lines = [
        f"风格 {style_def.get('style')} —— {style_def.get('display_name_cn', '')}",
        f"  {style_def.get('description', '')}",
        "",
        f"  {'参数':<22}{'类型':<8}{'默认':<20}{'范围/取值':<24}说明",
        "  " + "-" * 104,
    ]
    for k, m in style_def.get("params", {}).items():
        t = m.get("type", "str")
        rng = ""
        if m.get("min") is not None or m.get("max") is not None:
            rng = f"{m.get('min', '-')}~{m.get('max', '-')}"
        elif m.get("choices"):
            rng = "/".join(map(str, m["choices"]))
        lines.append(f"  {k:<22}{t:<8}{str(m.get('default')):<20}{rng:<24}{m.get('desc', '')}")
    cons = style_def.get("constraints") or []
    if cons:
        lines.append("")
        lines.append("  跨字段约束：")
        for c in cons:
            lines.append(f"    - {c.get('msg')}  [{c.get('expr')}]")
    return "\n".join(lines)


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="风格浏览 / spec 生成")
    ap.add_argument("--list", action="store_true", help="列出全部风格")
    ap.add_argument("--show", metavar="STYLE", help="显示某风格的参数表")
    ap.add_argument("--new", metavar="STYLE", help="按风格生成默认 spec（打印到 stdout）")
    ap.add_argument("--name", help="配合 --new 指定输出名")
    args = ap.parse_args()

    if args.list:
        for s in list_styles():
            print(f"  {s.get('style'):<16} {s.get('display_name_cn', '')}  — {s.get('description', '')}")
        return 0
    if args.show:
        print(describe_style(load_style(args.show)))
        return 0
    if args.new:
        print(json.dumps(spec_from_style(args.new, args.name), ensure_ascii=False, indent=2))
        return 0
    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
