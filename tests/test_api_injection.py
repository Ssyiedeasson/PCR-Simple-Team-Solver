# -*- coding: utf-8 -*-
"""Verify the API injection block against mock OpenAI-compatible / custom servers."""
import json
import os
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pcr_team.analyzer import analyze  # noqa: E402
from pcr_team.engine import TeamPlanner, load_roster  # noqa: E402

ROSTER = load_roster()
PLAN = TeamPlanner(ROSTER, "pve").plan(
    ROSTER.resolve("真琴 香织 咲恋 优衣 佩可莉姆 凯露（夏日） 镜华 美里")[0], team_count=1)

SEEN = {}


class Mock(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n).decode() or "{}")
        SEEN[self.path] = {"body": body, "auth": self.headers.get("Authorization")}
        if self.path == "/v1/chat/completions":
            out = {"model": "mock-model",
                   "choices": [{"message": {"content": "## 模拟分析\n这支队伍可以打。"}}],
                   "usage": {"total_tokens": 42}}
        elif self.path == "/custom":
            out = {"data": {"answer": "自定义注入成功：" + body.get("text", "")[:20]}}
        else:
            out = {"error": "unknown"}
        data = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class TestInjection(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 8801), Mock)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()
        cls.base = "http://127.0.0.1:8801"

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def test_openai_compatible(self):
        cfg = {"provider": "openai", "base_url": self.base + "/v1",
               "api_key": "sk-test", "model": "mock-model", "timeout": 10}
        out = analyze(PLAN, cfg)
        self.assertEqual(out["source"], "openai")
        self.assertIn("模拟分析", out["text"])
        self.assertEqual(SEEN["/v1/chat/completions"]["auth"], "Bearer sk-test")
        body = SEEN["/v1/chat/completions"]["body"]
        self.assertEqual(body["model"], "mock-model")
        self.assertEqual(body["messages"][0]["role"], "system")
        self.assertIn("队伍 1", body["messages"][1]["content"])

    def test_custom(self):
        cfg = {"provider": "custom", "timeout": 10,
               "custom": {"url": self.base + "/custom",
                          "headers": {"Content-Type": "application/json"},
                          "body_template": '{"text": {{prompt_json}}}',
                          "response_path": "data.answer"}}
        out = analyze(PLAN, cfg)
        self.assertEqual(out["source"], "custom")
        self.assertIn("自定义注入成功", out["text"])

    def test_bad_endpoint_falls_back(self):
        cfg = {"provider": "openai", "base_url": "http://127.0.0.1:9/v1",
               "api_key": "x", "model": "m", "timeout": 2}
        out = analyze(PLAN, cfg)
        self.assertEqual(out["source"], "local-fallback")
        self.assertTrue(out.get("error"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
