# -*- coding: utf-8 -*-
"""Integration tests for the HTTP layer (stdlib only)."""
import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pcr_team.server import Handler  # noqa: E402
from http.server import ThreadingHTTPServer  # noqa: E402

PORT = 8799
BASE = f"http://127.0.0.1:{PORT}"


def request(path, payload=None, method=None):
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(BASE + path, data=data, headers=headers,
                                 method=method or ("POST" if data else "GET"))
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


class TestServer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        cls.tmp.close()
        cls.fb = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        cls.fb.close()
        os.environ["PCR_CONFIG"] = cls.tmp.name
        os.environ["PCR_FEEDBACK"] = cls.fb.name
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        for _ in range(50):
            try:
                request("/api/meta")
                break
            except Exception:
                time.sleep(0.1)

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        os.environ.pop("PCR_CONFIG", None)
        os.environ.pop("PCR_FEEDBACK", None)
        for path in (cls.tmp.name, cls.fb.name):
            try:
                os.unlink(path)
            except OSError:
                pass

    def test_index(self):
        with urllib.request.urlopen(BASE + "/", timeout=10) as r:
            html = r.read().decode()
        self.assertIn("兰德索尔配队工坊", html)
        self.assertIn("app.js", html)

    def test_characters(self):
        d = request("/api/characters")
        self.assertGreaterEqual(d["count"], 270)
        self.assertTrue(all(c["playable"] for c in d["characters"]))
        self.assertTrue(any(c["name"] == "真琴" for c in d["characters"]))
        allc = request("/api/characters?all=1")
        self.assertGreater(allc["count"], d["count"])
        self.assertTrue(any(c["name"] == "大和" and not c["playable"]
                            for c in allc["characters"]))

    def test_plan(self):
        d = request("/api/plan", {"characters": "真琴 香织 咲恋 优衣 佩可莉姆 凯露（夏日） 镜华 美里",
                                  "teams": 1, "mode": "pve"})
        self.assertEqual(len(d["teams"]), 1)
        self.assertEqual(len(d["teams"][0]["members"]), 5)
        self.assertGreater(d["teams"][0]["score"], 50)

    def test_plan_unreleased(self):
        five = "大和 香织 咲恋 优衣 真琴"
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            request("/api/plan", {"characters": five, "teams": 1})
        self.assertEqual(ctx.exception.code, 400)
        d = request("/api/plan", {"characters": five, "teams": 1, "includeUnreleased": True})
        self.assertEqual(len(d["teams"]), 1)

    def test_plan_error(self):
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            request("/api/plan", {"characters": "真琴 香织", "teams": 1})
        self.assertEqual(ctx.exception.code, 400)

    def test_analyze_local(self):
        plan = request("/api/plan", {"characters": "真琴 香织 咲恋 优衣 佩可莉姆 凯露（夏日） 镜华",
                                     "teams": 1, "mode": "pve"})
        d = request("/api/analyze", {"plan": plan, "config": {"provider": "local"}})
        self.assertEqual(d["source"], "local")
        self.assertIn("队伍 1", d["text"])

    def test_feedback_endpoint(self):
        d = request("/api/feedback", {"text": "识别结果不全，只选中 3 个", "category": "截图识别",
                                      "context": {"mode": "pve"}})
        self.assertTrue(d["ok"])
        self.assertEqual(d["reply"]["source"], "local")
        self.assertIn("本地记录成功", d["reply"]["text"])
        listing = request("/api/feedback?limit=5")
        self.assertTrue(any(i["id"] == d["entry"]["id"] for i in listing["items"]))

    def test_config_roundtrip(self):
        d = request("/api/config", {"provider": "local", "model": "test-model"})
        self.assertTrue(d["ok"])
        cfg = request("/api/config")
        self.assertEqual(cfg["model"], "test-model")
        self.assertNotIn("api_key", json.dumps(cfg.get("api_key")))  # never returns a real key


if __name__ == "__main__":
    unittest.main(verbosity=2)
