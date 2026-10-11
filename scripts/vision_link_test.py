#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
vision_link_test.py —— 视觉接入链路自测（不依赖真实多模态模型）

用一个**本地 mock 端点**冒充 OpenAI 兼容的多模态接口，把 `vision_review.call_openai`
真跑一遍，校验三件事：
  1) 请求形状正确：4 张视图都以 `data:image/png;base64,...` 挂在 `image_url` 上；
  2) 回复解析健壮：模型用 ```json 围栏包裹也能抠出来；
  3) 规范化正确：severity/view/region/bbox 被校正，并落成 `mc-vision/1`。

再拿这份「带伪缺陷」的报告走一遍审计，验证视觉错误**能一路变成带世界坐标的结构化 error**。

用法：python vision_link_test.py [--views <dir>] [--round 1]
退出码：0 = 链路自测通过
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import vision_review as V                                     # noqa: E402

CANNED = {
    "schema": "mc-vision/1", "reviewer": "mock-vlm",
    "verdict": "FAIL",
    "findings": [
        {"type": "屋顶塌陷", "severity": "ERROR", "view": "FRONT",
         "region": [0.25, 0.0, 0.5, 0.1], "note": "顶板中部下陷（mock 伪缺陷，用于验证链路）"},
        {"type": "结构错位", "severity": "warning", "view": "top",
         "region": [0.45, 0.45, 0.55, 0.55], "note": "中庭四周错位（mock 伪缺陷）"},
    ],
}


class Handler(BaseHTTPRequestHandler):
    seen: dict = {}

    def do_POST(self) -> None:                                # noqa: N802
        n = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(n) or b"{}")
        imgs, sys_msg = [], ""
        for m in body.get("messages", []):
            c = m.get("content")
            if isinstance(c, str):
                sys_msg += c
            elif isinstance(c, list):
                for seg in c:
                    if isinstance(seg, dict) and seg.get("type") == "image_url":
                        imgs.append(seg["image_url"]["url"])
        Handler.seen = {"model": body.get("model"), "n_images": len(imgs),
                        "all_data_uri": all(u.startswith("data:image/png;base64,") for u in imgs),
                        "has_axis_contract": "画面水平" in sys_msg}
        payload = json.dumps({"choices": [{"message": {
            "content": "```json\n" + json.dumps(CANNED, ensure_ascii=False) + "\n```"}}]},
            ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *a) -> None:                        # 静音
        pass


def main() -> int:
    ap = argparse.ArgumentParser(description="视觉接入链路自测（本地 mock 端点）")
    ap.add_argument("--views", default="mc_builds/gangxia_north/audit/round_2/views")
    ap.add_argument("--round", type=int, default=1)
    ap.add_argument("--keep", action="store_true", help="保留伪缺陷报告（供审计链路复现）")
    args = ap.parse_args()

    views = os.path.abspath(args.views)
    missing = [f"view_{v}.png" for v in V.VIEWS if not os.path.isfile(os.path.join(views, f"view_{v}.png"))]
    if missing:
        print(f"[FAIL] 缺视图 {missing}；先跑 render_preview.py --vision-pack")
        return 1

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    fails: list[str] = []

    def chk(cond: bool, label: str) -> None:
        print(f"  {'OK  ' if cond else '[X] '} {label}")
        if not cond:
            fails.append(label)

    try:
        bbox = (-200, -59, -200, 199, -4, 199)
        rep = V.call_openai(f"http://127.0.0.1:{port}", "mock-vlm", "dummy-key", views, bbox, 30, "")
        norm = V.normalize(rep, "mock-vlm", bbox)
        seen = Handler.seen
        print("① 请求形状：")
        chk(seen.get("n_images") == 4, f"四张视图全部附上（{seen.get('n_images')} 张）")
        chk(seen.get("all_data_uri") is True, "全部以 data:image/png;base64 附在 image_url 上")
        chk(seen.get("has_axis_contract") is True, "系统提示里带轴约定与 JSON 契约")
        chk(seen.get("model") == "mock-vlm", f"模型名透传（{seen.get('model')}）")
        print("② 回复解析与规范化：")
        chk(len(rep.get("findings", [])) == 2, "```json 围栏内的 JSON 被正确抠出")
        chk(norm["schema"] == "mc-vision/1" and norm["reviewer"] == "mock-vlm", "schema/reviewer 已归一")
        f0 = norm["findings"][0]
        chk(f0["severity"] == "error", f"severity 大小写被校正（{f0['severity']}）")
        chk(f0["view"] == "front", f"view 大小写被校正（{f0['view']}）")
        chk(norm["verdict"] == "FAIL", "有 error 时 verdict 自洽为 FAIL")
    finally:
        srv.shutdown()

    outp = os.path.join(views, "vision_report.mock.json")
    with open(outp, "w", encoding="utf-8") as f:
        json.dump(norm, f, ensure_ascii=False, indent=2)
    print(f"\n带伪缺陷的报告 -> {outp}")

    # 再把这份报告喂给审计，看视觉错误能否变成带世界坐标的 error
    import audit_schem as A
    solid_probe = None
    try:
        import numpy as np
        solid_probe = np.zeros((5, 5, 5), dtype=bool)      # 只为跑通 region→坐标换算
        solid_probe[0, 0, 0] = True
    except ImportError:
        pass
    vf, probs, done = A.load_vision(outp, (-200, -59, -200), (400, 56, 400))
    print("③ 视觉错误进入审计：")
    for f in vf:
        print(f"     [{f['severity']}] {f['type']}  view={f['view']}  -> 世界坐标区间 {f['bbox']}")
    chk(len(vf) == 2 and all(f["bbox"] for f in vf), "每条视觉错误都换算出了世界坐标区间")
    chk(not probs, f"报告合规（问题：{probs}）")

    if not args.keep:
        os.remove(outp)
        print("（已删除 mock 报告；加 --keep 可保留）")
    print("\n" + ("[OK] 视觉接入链路自测通过" if not fails else f"[FAIL] {len(fails)} 项未通过"))
    return 0 if not fails else 1


if __name__ == "__main__":
    raise SystemExit(main())
