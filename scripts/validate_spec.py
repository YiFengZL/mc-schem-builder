#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
validate_spec.py —— spec.json 前置校验命令行入口

在生成前把问题拦下来：参数越界、跨字段矛盾、方块名非法、字段笔误。
**任何一项不通过都不应继续生成**（否则错误会以"静默变空气"的形式进到 .schem 里）。

用法：
    python validate_spec.py --list                       # 列出全部风格
    python validate_spec.py --show castle                # 查看某风格的参数表
    python validate_spec.py --new castle --name my_castle  # 生成默认 spec
    python validate_spec.py --check spec.json            # 校验 spec
    python validate_spec.py --check spec.json --json      # 机器可读输出

退出码：0 = 通过；1 = 有问题
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from spec_schema import (  # noqa: E402
    SCHEMA_VERSION, describe_style, list_styles, load_style,
    spec_from_style, validate_spec,
)


def _load_spec(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def cmd_check(path: str, as_json: bool) -> int:
    if not os.path.isfile(path):
        print(f"[FAIL] spec 文件不存在：{path}")
        return 1
    try:
        spec = _load_spec(path)
    except json.JSONDecodeError as e:
        print(f"[FAIL] spec 不是合法 JSON：{e}")
        return 1

    style_def = None
    style = spec.get("style")
    if style:
        try:
            style_def = load_style(str(style))
        except FileNotFoundError as e:
            print(f"[FAIL] {e}")
            return 1

    ok, errors, warnings, params = validate_spec(spec, style_def)

    if as_json:
        print(json.dumps(
            {"ok": ok, "errors": errors, "warnings": warnings, "params": params},
            ensure_ascii=False, indent=2,
        ))
        return 0 if ok else 1

    print(f"spec : {path}")
    print(f"名称 : {spec.get('name')}")
    print(f"风格 : {spec.get('style')}" + (f" — {style_def.get('display_name_cn','')}" if style_def else ""))
    if style_def:
        n_decl = len(style_def.get("params", {}))
        n_used = len(params)
        print(f"参数 : 风格声明 {n_decl} 项，合并后 {n_used} 项")
    print()
    if warnings:
        print(f"警告 {len(warnings)} 条：")
        for w in warnings:
            print(f"  ! {w}")
        print()
    if errors:
        print(f"[FAIL] 错误 {len(errors)} 条：")
        for e in errors:
            print(f"  X {e}")
        print("\n请先修正以上问题再生成。")
        return 1

    print("[OK] spec 校验通过，可以生成")
    # 附带打印关键参数，便于确认
    print("\n生效参数：")
    for k, v in params.items():
        print(f"  {k:<22} = {v}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="spec.json 前置校验")
    ap.add_argument("--list", action="store_true", help="列出全部风格")
    ap.add_argument("--show", metavar="STYLE", help="显示某风格的参数表")
    ap.add_argument("--new", metavar="STYLE", help="生成默认 spec 到 stdout（或用 --out 写文件）")
    ap.add_argument("--name", help="配合 --new：输出名")
    ap.add_argument("--out", help="配合 --new：写到文件")
    ap.add_argument("--check", metavar="SPEC", help="校验 spec 文件")
    ap.add_argument("--json", action="store_true", help="机器可读输出")
    args = ap.parse_args()

    if args.list:
        for s in list_styles():
            if "error" in s:
                print(f"  {s['style']:<16} [加载失败] {s['error']}")
            else:
                print(f"  {s['style']:<16} {s.get('display_name_cn','')}")
                print(f"      {s.get('description','')}")
        return 0

    if args.show:
        try:
            print(describe_style(load_style(args.show)))
        except FileNotFoundError as e:
            print(f"[FAIL] {e}")
            return 1
        return 0

    if args.new:
        try:
            spec = spec_from_style(args.new, args.name)
        except FileNotFoundError as e:
            print(f"[FAIL] {e}")
            return 1
        text = json.dumps(spec, ensure_ascii=False, indent=2)
        if args.out:
            os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
            with open(args.out, "w", encoding="utf-8") as f:
                f.write(text + "\n")
            print(f"[OK] 已写入 {args.out}")
        else:
            print(text)
        return 0

    if args.check:
        return cmd_check(args.check, args.json)

    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
