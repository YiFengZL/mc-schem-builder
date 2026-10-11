#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
vision_review.py —— 视觉检查维度：把四视图**接入多模态模型**，产结构化识图报告

它把「四张图 + 判定要求 + 坐标约定」打包成一次多模态请求，拿回 JSON 后规范化成
`mc-vision/1` 报告（`vision_report.json`），供 `audit_schem.py --vision` 合并进
结构化错误清单。这样视觉维度就不再依赖人工回填。

三种后端（`--backend`）
----------------------
* `openai`（默认）：**OpenAI 兼容**的多模态接口 `POST {base}/v1/chat/completions`，
  图片以 `data:image/png;base64,...` 走 `image_url`。适用于 OpenAI / 通义 / 豆包 /
  DeepSeek-VL / vLLM / LM Studio / one-api 等一切兼容实现。
* `ollama`：Ollama 原生 `POST {base}/api/chat`，图片走 `images: [base64]`。
* `echo`：不联网，直接产一份「无异常」报告 —— 只用于**自测链路**（出图/解析/合并/状态机）。

配置（不要写进代码，也不要贴到对话里）
--------------------------------------
    VISION_BASE_URL   例如 https://api.openai.com        （ollama 默认 http://127.0.0.1:11434）
    VISION_API_KEY    接口密钥（ollama 不需要）
    VISION_MODEL      例如 gpt-4o-mini / qwen-vl-max / llava:13b

用法
----
  # 由 loop_check.py --vision-auto 自动调用（推荐）
  python vision_review.py --views <out>/audit/round_2/views --bbox -200,-59,-200,199,-4,199 \\
      --out <out>/audit/round_2/views/vision_report.json

  # 自测链路（不联网）
  python vision_review.py --views ... --bbox ... --backend echo --out /tmp/v.json

  # 只导出「给模型看的提示词 + JSON 契约」（人工/agent 回填时用），再规范化回填结果
  python vision_review.py --views ... --bbox ... --prompt-out /tmp/prompt.txt
  python vision_review.py --ingest /tmp/agent_reply.json --out .../vision_report.json

退出码：0 = 已产出报告；2 = 未配置多模态模型（打印需要哪些环境变量）；1 = 出错
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
VIEWS = ("front", "side", "top", "iso")
VIEW_DESC = {
    "front": "正视图（画面水平 = x：左→右 x 增大；垂直 = y：上 = 最高）",
    "side": "侧视图（画面水平 = z：左→右 z 增大；垂直 = y：上 = 最高）",
    "top": "俯视图（画面水平 = z：左→右 z 增大；垂直 = x：上 = x 最小）",
    "iso": "等轴测（透视，无法反算坐标：只能给 region + 文字描述）",
}
TYPES = ["屋顶塌陷", "门体悬空", "结构错位", "悬空方块", "构件缺失", "比例失真", "镜像不对称", "其他"]


def system_prompt(bbox: tuple[int, int, int, int, int, int] | None) -> str:
    rng = (f"模型世界坐标范围：x ∈ [{bbox[0]},{bbox[3]}]，y ∈ [{bbox[1]},{bbox[4]}]，"
           f"z ∈ [{bbox[2]},{bbox[5]}]" if bbox else "模型世界坐标范围：未知（那就只给 region）")
    return "\n".join([
        "你是 Minecraft 建筑投影（Litematica .schem）的结构检查员。",
        "下面给你**同一栋建筑**的四张视图：" + "、".join(f"view_{v}.png" for v in VIEWS) + "。",
        "各视图的轴约定：",
        *[f"  - view_{v}.png：{VIEW_DESC[v]}" for v in VIEWS],
        rng,
        "只报告**结构性错误**，从这些类型里选：" + "、".join(TYPES) + "。",
        "不要报告审美、材质、配色问题；不要因为『看起来朴素』而报错。",
        "每条错误必须**能定位**：优先给 bbox=[x0,y0,z0,x1,y1,z1]（世界坐标整数）；",
        "拿不到世界坐标就给 region=[u0,v0,u1,v1]（归一化矩形，u 从左到右、v 从上到下，取值 0~1）"
        "并写明 view；等轴测图只能给 region + 文字。",
        "没有发现问题时：findings 必须为空数组，verdict 必须为 \"PASS\"。",
        "只输出一个 JSON 对象，不要输出任何解释、Markdown 代码块或多余文字。JSON 契约：",
        '{"schema":"mc-vision/1","reviewer":"<你是什么模型>","findings":'
        '[{"type":"<上面的类型之一>","severity":"error","view":"front|side|top|iso",'
        '"region":[0,0,0,0],"bbox":null,"note":"<看到什么、在哪个部位>"}],"verdict":"PASS|FAIL"}',
    ])


def user_prompt(notes: str = "") -> str:
    base = ("逐张检查这四张图，按契约输出 JSON。"
            "请特别留意：屋面是否连续无塌陷、门/窗/构件是否悬空或缺失、"
            "左右是否镜像一致、有无与主体脱离的漂浮块。")
    return base + ("\n附加关注点：" + notes if notes else "")


def _b64(path: str) -> str:
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("ascii")


def _post(url: str, payload: dict, key: str | None, timeout: int) -> dict:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    if key:
        req.add_header("Authorization", f"Bearer {key}")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", errors="replace"))


def _extract_json(text: str) -> dict:
    """从模型回复里抠出第一个 JSON 对象（容忍 ```json 围栏与前后废话）。"""
    t = text.strip()
    t = re.sub(r"^```[a-zA-Z]*\s*|\s*```$", "", t, flags=re.S)
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass
    depth, start = 0, None
    for i, ch in enumerate(t):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start is not None:
                try:
                    return json.loads(t[start:i + 1])
                except json.JSONDecodeError:
                    start = None
    raise ValueError("模型回复里找不到合法 JSON：" + text[:200])


def normalize(rep: dict, reviewer_fallback: str, bbox) -> dict:
    """把模型回复规范化成 mc-vision/1（补齐 schema/reviewer，校正 severity/view/region）。"""
    out = {
        "schema": "mc-vision/1",
        "reviewer": str(rep.get("reviewer") or reviewer_fallback),
        "views": list(VIEWS),
        "findings": [],
        "verdict": str(rep.get("verdict") or "PASS").upper(),
    }
    for f in (rep.get("findings") or []):
        if not isinstance(f, dict):
            continue
        sev = str(f.get("severity", "error")).lower()
        sev = sev if sev in ("error", "warning", "info") else "error"
        view = str(f.get("view", "")).lower()
        view = view if view in VIEWS else None
        region = f.get("region")
        if isinstance(region, (list, tuple)) and len(region) == 4:
            region = [max(0.0, min(1.0, float(v))) for v in region]
        else:
            region = None
        bb = f.get("bbox")
        bb = [int(round(float(c))) for c in bb] if isinstance(bb, (list, tuple)) and len(bb) == 6 else None
        out["findings"].append({
            "type": str(f.get("type") or "其他"),
            "severity": sev,
            "view": view,
            "region": region,
            "bbox": bb,
            "note": str(f.get("note") or ""),
        })
    if bbox:
        out["_world_bbox"] = list(bbox)
    if out["verdict"] == "PASS" and any(f["severity"] == "error" for f in out["findings"]):
        out["verdict"] = "FAIL"                      # 契约自洽：有 error 就不可能是 PASS
    return out


def call_openai(base: str, model: str, key: str | None, views_dir: str, bbox, timeout: int,
                notes: str) -> dict:
    content = [{"type": "text", "text": user_prompt(notes)}]
    for v in VIEWS:
        p = os.path.join(views_dir, f"view_{v}.png")
        if not os.path.isfile(p):
            raise FileNotFoundError(f"缺视图 {p}（先跑 render_preview.py --vision-pack）")
        content.append({"type": "text", "text": f"【view_{v}.png】"})
        content.append({"type": "image_url",
                        "image_url": {"url": "data:image/png;base64," + _b64(p)}})
    body = {
        "model": model,
        "temperature": 0,
        "max_tokens": 2048,
        "messages": [{"role": "system", "content": system_prompt(bbox)},
                     {"role": "user", "content": content}],
    }
    url = base.rstrip("/") + "/v1/chat/completions"
    try:
        resp = _post(url, dict(body, response_format={"type": "json_object"}), key, timeout)
    except urllib.error.HTTPError as e:                   # 有些实现不认 response_format
        if e.code not in (400, 404, 422):
            raise
        resp = _post(url, body, key, timeout)
    text = resp["choices"][0]["message"]["content"]
    if isinstance(text, list):                            # 少数实现返回分段内容
        text = "".join(seg.get("text", "") for seg in text)
    return _extract_json(text)


def call_ollama(base: str, model: str, views_dir: str, bbox, timeout: int, notes: str) -> dict:
    imgs = []
    for v in VIEWS:
        p = os.path.join(views_dir, f"view_{v}.png")
        if not os.path.isfile(p):
            raise FileNotFoundError(f"缺视图 {p}")
        imgs.append(_b64(p))
    body = {
        "model": model,
        "stream": False,
        "format": "json",
        "messages": [{"role": "system", "content": system_prompt(bbox)},
                     {"role": "user", "content": user_prompt(notes) + "\n四张图按 "
                      "view_front / view_side / view_top / view_iso 的顺序给出。", "images": imgs}],
        "options": {"temperature": 0},
    }
    resp = _post(base.rstrip("/") + "/api/chat", body, None, timeout)
    return _extract_json(resp["message"]["content"])


def echo_report(bbox) -> dict:
    """自测后端：不联网，产一份「无异常」报告（用于验证出图→解析→合并→状态机全链路）。"""
    return {"schema": "mc-vision/1", "reviewer": "echo-backend(自测)",
            "findings": [], "verdict": "PASS",
            "_note": "echo 后端未调用任何模型，仅用于链路自测"}


def main() -> int:
    ap = argparse.ArgumentParser(description="四视图 → 多模态模型 → 结构化识图报告")
    ap.add_argument("--views", help="四视图所在目录（render_preview.py --vision-pack 的产物）")
    ap.add_argument("--bbox", help="模型世界包围盒 x0,y0,z0,x1,y1,z1（写进提示词，供模型换算坐标）")
    ap.add_argument("--backend", default="openai", choices=["openai", "ollama", "echo"],
                    help="openai=OpenAI 兼容接口（默认）；ollama 原生；echo 自测")
    ap.add_argument("--endpoint", help="默认取 $VISION_BASE_URL")
    ap.add_argument("--model", help="默认取 $VISION_MODEL")
    ap.add_argument("--api-key", help="默认取 $VISION_API_KEY（不要直接把密钥写在命令行里）")
    ap.add_argument("--timeout", type=int, default=120)
    ap.add_argument("--notes", default="", help="附加关注点，会写进用户提示")
    ap.add_argument("--out", help="输出的 vision_report.json")
    ap.add_argument("--prompt-out", help="只导出提示词与 JSON 契约（人工/agent 回填用）")
    ap.add_argument("--ingest", help="把已有回复（模型/agent 产出）规范化成 mc-vision/1")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    bbox = None
    if args.bbox:
        try:
            bbox = tuple(int(v) for v in args.bbox.split(","))
            assert len(bbox) == 6
        except (ValueError, AssertionError):
            print("[FAIL] --bbox 需为 x0,y0,z0,x1,y1,z1")
            return 1

    # ---- 分支一：只导出提示词（给 agent / 人工回填用）----
    if args.prompt_out:
        with open(args.prompt_out, "w", encoding="utf-8") as f:
            f.write("=== SYSTEM ===\n" + system_prompt(bbox) + "\n\n=== USER ===\n" + user_prompt(args.notes))
        print(f"[OK] 提示词与 JSON 契约 -> {args.prompt_out}")
        if not args.out and not args.ingest:
            return 0

    # ---- 分支二：规范化回填（模型/agent 已给出回复）----
    if args.ingest:
        with open(args.ingest, encoding="utf-8") as f:
            raw = f.read()
        try:
            rep = _extract_json(raw)
        except ValueError as e:
            print(f"[FAIL] {e}")
            return 1
        norm = normalize(rep, "ingest", bbox)
        if not args.out:
            print(json.dumps(norm, ensure_ascii=False, indent=2))
            return 0
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(norm, f, ensure_ascii=False, indent=2)
        print(f"[OK] 识图报告（归一化回填）-> {args.out}   findings={len(norm['findings'])}"
              f"  verdict={norm['verdict']}")
        return 0

    if not args.views:
        print("[FAIL] 需要 --views（或配合 --ingest / --prompt-out 使用）")
        return 1

    # ---- 分支三：真正调用多模态模型 ----
    if args.backend == "echo":
        norm = normalize(echo_report(bbox), "echo-backend(自测)", bbox)
    else:
        base = args.endpoint or os.environ.get("VISION_BASE_URL")
        model = args.model or os.environ.get("VISION_MODEL")
        key = args.api_key or os.environ.get("VISION_API_KEY")
        if args.backend == "ollama":
            base, model = base or "http://127.0.0.1:11434", model or ""
            if not model:
                print("[未配置] ollama 需要 VISION_MODEL（如 llava:13b / qwen2.5vl:7b）")
                return 2
        else:
            if not base or not model:
                print("[未配置] 视觉维度需要多模态模型，请设置环境变量后重跑：")
                print("    VISION_BASE_URL=https://<你的 OpenAI 兼容网关>")
                print("    VISION_API_KEY=<密钥>        # ollama 不需要")
                print("    VISION_MODEL=<多模态模型名>   # 如 gpt-4o-mini / qwen-vl-max / llava:13b")
                print("  或者用 --backend echo 自测链路；或 --prompt-out 导出提示词由 agent/人工回填后 --ingest")
                return 2
        try:
            if args.backend == "ollama":
                rep = call_ollama(base, model, args.views, bbox, args.timeout, args.notes)
            else:
                rep = call_openai(base, model, key, args.views, bbox, args.timeout, args.notes)
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")[:300]
            print(f"[FAIL] 多模态接口返回 HTTP {e.code}：{body}")
            print("       若报鉴权/模型不存在，检查 VISION_API_KEY / VISION_MODEL")
            return 1
        except (urllib.error.URLError, TimeoutError) as e:
            print(f"[FAIL] 连不上多模态接口：{e}（检查 VISION_BASE_URL 或改用 --backend ollama/echo）")
            return 1
        norm = normalize(rep, f"{args.backend}:{model}", bbox)

    if not args.out:
        print(json.dumps(norm, ensure_ascii=False, indent=2))
        return 0
    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(norm, f, ensure_ascii=False, indent=2)
    if not args.quiet:
        print(f"[OK] 识图报告 -> {args.out}")
        print(f"     reviewer={norm['reviewer']}  findings={len(norm['findings'])}  "
              f"verdict={norm['verdict']}")
        for f_ in norm["findings"][:6]:
            print(f"     - [{f_['severity']}] {f_['type']} view={f_['view']} "
                  f"region={f_['region']} bbox={f_['bbox']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
