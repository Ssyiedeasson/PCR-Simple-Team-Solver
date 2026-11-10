# -*- coding: utf-8 -*-
"""Bug feedback: storage, local triage, API triage."""
import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pcr_team import feedback  # noqa: E402


class MockLLM(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n).decode() or "{}")
        text = "## 分诊结果\n问题已定位，建议优先检查截图头像格。"
        data = json.dumps({"model": "mock", "choices": [{"message": {"content": text}}]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class TestFeedback(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        cls.tmp.close()
        os.environ["PCR_FEEDBACK"] = cls.tmp.name
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 8813), MockLLM)
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        os.environ.pop("PCR_FEEDBACK", None)
        try:
            os.unlink(cls.tmp.name)
        except OSError:
            pass

    def test_local_record_and_triage(self):
        res = feedback.handle("截图识别只选中了 3 个", "截图识别", "", {"mode": "pve"})
        self.assertTrue(res["ok"])
        self.assertEqual(res["reply"]["source"], "local")
        self.assertIn("本地记录成功", res["reply"]["text"])
        items = feedback.load_all()
        self.assertTrue(any(i["id"] == res["entry"]["id"] for i in items))
        self.assertTrue(any(i.get("reply") for i in items))

    def test_api_triage(self):
        cfg = {"provider": "openai", "base_url": "http://127.0.0.1:8813/v1",
               "api_key": "x", "model": "mock", "timeout": 10}
        res = feedback.handle("提交后没有返回", "性能/报错", "", {"mode": "pve"}, cfg=cfg)
        self.assertEqual(res["reply"]["source"], "openai")
        self.assertIn("分诊结果", res["reply"]["text"])

    def test_validation(self):
        with self.assertRaises(ValueError):
            feedback.add_feedback("短", "其他")

    def test_prompt_contains_context(self):
        entry = feedback.add_feedback("配队结果重复", "配队算法", "",
                                      {"roster": "真琴 香织", "plan": {"score": 80}})
        prompt = feedback.build_triage_prompt(entry)
        self.assertIn("真琴 香织", prompt)
        self.assertIn("配队算法", prompt)


if __name__ == "__main__":
    unittest.main(verbosity=2)
