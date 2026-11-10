# -*- coding: utf-8 -*-
"""Tiny zero-dependency web server for the team builder."""
from __future__ import annotations

import json
import os
import re
import socketserver
import sys
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from .analyzer import analyze, load_config, mask_key, save_config
from .feedback import CATEGORIES, handle as handle_feedback, load_all as load_feedback
from .engine import ENEMY_EFFECTS, TeamPlanner, load_roster

HERE = os.path.dirname(os.path.abspath(__file__))
WEB = os.path.join(HERE, "web")
MIME = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
        ".js": "application/javascript; charset=utf-8", ".svg": "image/svg+xml",
        ".png": "image/png", ".ico": "image/x-icon", ".json": "application/json; charset=utf-8"}

class _FastHTTPServer(ThreadingHTTPServer):
    """Avoid reverse-DNS lookup in server_bind (can block several seconds)."""

    def server_bind(self):
        socketserver.TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = host
        self.server_port = port


def _warmup_recognition():
    try:
        from .recognition import library
        lib = library()
        print(f"[recognition] 预热完成：{len(lib.entries)} 张图标")
    except Exception as e:  # noqa: BLE001
        print(f"[recognition] 预热跳过：{e}")


ROSTER = load_roster()
ROSTER_ALL = load_roster(include_unreleased=True)
ROSTER_JP = load_roster(include_unreleased=True, include_jp=True)


class Handler(BaseHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin",
                         os.environ.get("PCR_CORS_ORIGIN", "*"))
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Access-Control-Max-Age", "86400")
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(204)
        self.end_headers()

    server_version = "PCRTeamBuilder/1.0"

    def log_message(self, fmt, *args):  # quieter logs
        sys.stderr.write("[web] %s\n" % (fmt % args))

    # ---------- helpers ----------
    def _send(self, code: int, body: bytes, ctype: str = "application/json; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def json(self, obj, code: int = 200):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        if length > 12_000_000:
            raise ValueError("请求体过大（识别图片请控制在 8MB 以内）")
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception as e:  # noqa: BLE001
            raise ValueError(f"JSON 解析失败：{e}")

    def static(self, path: str):
        if path in ("/", ""):
            path = "/index.html"
        safe = os.path.normpath(path.lstrip("/"))
        if safe.startswith(".."):
            self.json({"error": "forbidden"}, 403)
            return
        full = os.path.join(WEB, safe)
        if not os.path.isfile(full):
            self.json({"error": "not found", "path": path}, 404)
            return
        ext = os.path.splitext(full)[1].lower()
        with open(full, "rb") as f:
            self._send(200, f.read(), MIME.get(ext, "application/octet-stream"))

    # ---------- routes ----------
    def do_GET(self):
        path = urlparse(self.path).path
        try:
            if path == "/api/characters":
                include_all = "all=1" in (urlparse(self.path).query or "")
                roster = ROSTER_ALL if include_all else ROSTER
                chars = [{"name": u.name, "position": u.position, "attack": u.attack,
                          "roles": u.roles, "power": u.power, "tags": u.tags[:6],
                          "playable": u.playable,
                          "aliases": roster.roster_aliases(u.name)}
                         for u in sorted(roster.units.values(),
                                         key=lambda x: ({"前卫": 0, "中卫": 1, "后卫": 2}.get(x.position, 3), x.pos, x.name))]
                return self.json({"count": len(chars), "characters": chars})
            if path == "/api/meta":
                meta = ROSTER.meta
                gw = sum(1 for u in ROSTER.units.values() if getattr(u, "source", "") == "gamewith")
                return self.json({
                    "characters": len(ROSTER.units),
                    "total_characters": len(ROSTER_JP.units),
                    "unreleased": len(ROSTER_ALL.units) - len(ROSTER.units),
                    "gw_characters": gw,
                    "version": meta.get("version", ""),
                    "sources": ["https://wiki.biligame.com/pcr/首页",
                                "https://pcredivewiki.tw/",
                                "https://gamewith.jp/pricone-re/"],
                })
            if path == "/api/feedback":
                limit = 20
                qs = urlparse(self.path).query or ""
                m = re.search(r"limit=(\d+)", qs)
                if m:
                    limit = max(1, min(100, int(m.group(1))))
                items = load_feedback()[-limit:]
                return self.json({"items": items, "categories": CATEGORIES})
            if path == "/api/config":
                cfg = load_config()
                cfg["api_key"] = mask_key(cfg.get("api_key", ""))
                cfg["has_key"] = bool(load_config().get("api_key"))
                return self.json(cfg)
            return self.static(path)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            self.json({"error": str(e)}, 500)

    def do_POST(self):
        path = urlparse(self.path).path
        try:
            payload = self.body()
            if path == "/api/plan":
                return self.handle_plan(payload)
            if path == "/api/analyze":
                return self.handle_analyze(payload)
            if path == "/api/recognize":
                return self.handle_recognize(payload)
            if path == "/api/recognize/learn":
                return self.handle_learn(payload)
            if path == "/api/feedback":
                return self.handle_feedback(payload)
            if path == "/api/config":
                return self.handle_config(payload)
            self.json({"error": "not found"}, 404)
        except ValueError as e:
            self.json({"error": str(e)}, 400)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            self.json({"error": f"{type(e).__name__}: {e}"}, 500)

    # ---------- handlers ----------
    def handle_plan(self, p: dict):
        chars = p.get("characters") or ""
        if isinstance(chars, list):
            chars = " ".join(str(c) for c in chars)
        exclude = p.get("exclude") or ""
        mode = p.get("mode") if p.get("mode") in ("pve", "pvp") else "pve"
        teams = int(p.get("teams") or 0)
        allow_reuse = bool(p.get("allowReuse"))
        beam = max(120, min(4000, int(p.get("beam") or 600)))
        roster = ROSTER_JP if p.get("includeJp") else (ROSTER_ALL if p.get("includeUnreleased") else ROSTER)
        raw_effects = p.get("enemyEffects") or []
        if isinstance(raw_effects, str):
            raw_effects = [x for x in re.split(r"[,，、;；]+", raw_effects) if x]
        enemy_effects = [e for e in raw_effects if e in ENEMY_EFFECTS][:8]

        units, unknown = roster.resolve(chars)
        if exclude:
            bad, _ = roster.resolve(exclude)
            drop = {u.name for u in bad}
            units = [u for u in units if u.name not in drop]
        if not units:
            return self.json({"error": "没有识别到任何角色，请检查名称。"}, 400)
        if len(units) < 5:
            return self.json({"error": f"只识别到 {len(units)} 名角色，至少需要 5 名。",
                              "unknown": unknown, "resolved": [u.name for u in units]}, 400)

        planner = TeamPlanner(roster, mode, beam=beam, enemy_effects=enemy_effects)
        plan = planner.plan(units, team_count=teams, allow_reuse=allow_reuse,
                            sequence=bool(p.get("sequence")))
        plan["unknown"] = unknown
        plan["resolved"] = [u.name for u in units]
        plan["auto"] = teams <= 0
        return self.json(plan)

    def handle_analyze(self, p: dict):
        plan = p.get("plan") or {}
        if not plan.get("teams"):
            return self.json({"error": "没有可分析的队伍，请先生成配队。"}, 400)
        cfg = load_config()
        patch = p.get("config") or {}
        for k in ("provider", "base_url", "model", "temperature", "max_tokens",
                  "timeout", "system_prompt", "extra_context"):
            if k in patch and patch[k] not in (None, ""):
                cfg[k] = patch[k]
        if patch.get("api_key"):
            cfg["api_key"] = patch["api_key"]
        if p.get("extra_context"):
            cfg["extra_context"] = p["extra_context"]
        plan = dict(plan)
        plan["extra_context"] = cfg.get("extra_context", "")
        result = analyze(plan, cfg)
        result.pop("prompt", None)
        result["config"] = {"provider": cfg.get("provider"), "model": cfg.get("model"),
                            "base_url": cfg.get("base_url")}
        return self.json(result)

    def handle_recognize(self, p: dict):
        from .recognition import decode_image, recognize
        img = decode_image(p.get("image") or "")
        if img is None:
            return self.json({"error": "图片解析失败，请上传 PNG/JPG 截图。"}, 400)
        h, w = img.shape[:2]
        if max(h, w) > 2000:
            import cv2
            scale = 2000.0 / max(h, w)
            img = cv2.resize(img, (int(w * scale), int(h * scale)))
        result = recognize(img, topk=int(p.get("topk") or 3))
        result["image_size"] = [int(img.shape[1]), int(img.shape[0])]
        return self.json(result)

    def handle_feedback(self, p: dict):
        text = p.get("text") or ""
        category = p.get("category") or "其他"
        contact = p.get("contact") or ""
        context = p.get("context") or {}
        if not isinstance(context, dict):
            context = {}
        try:
            res = handle_feedback(text, category, contact, context)
            return self.json(res)
        except ValueError as e:
            return self.json({"error": str(e)}, 400)

    def handle_learn(self, p: dict):
        from .recognition import decode_image, save_custom_icon
        name = (p.get("name") or "").strip()
        if not name or name not in ROSTER_ALL.units:
            return self.json({"error": "角色名无效"}, 400)
        img = decode_image(p.get("image") or "")
        if img is None:
            return self.json({"error": "头像解析失败"}, 400)
        return self.json(save_custom_icon(name, img))

    def handle_config(self, p: dict):
        cfg = load_config()
        for k in ("provider", "base_url", "model", "temperature", "max_tokens",
                  "timeout", "system_prompt", "extra_context", "api_key_env"):
            if k in p and p[k] is not None and p[k] != "":
                cfg[k] = p[k]
        key = p.get("api_key")
        if key and not (len(key) > 6 and set(key) <= set("*")):
            cfg["api_key"] = key
        if p.get("clear_key"):
            cfg["api_key"] = ""
        if isinstance(p.get("custom"), dict):
            cfg["custom"] = {**(cfg.get("custom") or {}), **p["custom"]}
        save_config(cfg)
        return self.json({"ok": True, "has_key": bool(cfg.get("api_key")),
                          "provider": cfg.get("provider")})


def run(host: str = "127.0.0.1", port: int = 8765):
    httpd = _FastHTTPServer((host, port), Handler)
    threading.Thread(target=_warmup_recognition, daemon=True).start()
    print(f"PCR 配队器已启动：http://{host}:{port}  （Ctrl+C 退出）")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    a = ap.parse_args()
    run(a.host, a.port)
