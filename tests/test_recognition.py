# -*- coding: utf-8 -*-
"""Screenshot recognition tests (synthetic ground truth + cell detection)."""
import json
import os
import sys
import unittest

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pcr_team.recognition import detect_cells, recognize  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURE = os.path.join(ROOT, "tests", "fixtures", "party.jpg")
INDEX = os.path.join(ROOT, "pcr_team", "data", "icon_index.json")


def synthetic(names):
    with open(INDEX, encoding="utf-8") as f:
        idx = {e["name"]: e for e in json.load(f)}
    canvas = Image.new("RGB", (300 * len(names), 300), "white")
    for i, n in enumerate(names):
        im = Image.open(os.path.join(ROOT, "pcr_team", "data", "icons", idx[n]["file"])).convert("RGBA")
        bg = Image.new("RGBA", im.size, "white")
        im = Image.alpha_composite(bg, im).convert("RGB").resize((260, 260))
        d = ImageDraw.Draw(im)
        d.rectangle([0, 0, 259, 259], outline=(240, 120, 160), width=10)
        d.text((8, 4), "等级320", fill="white")
        canvas.paste(im, (i * 300 + 20, 20))
    return np.array(canvas)


class TestRecognition(unittest.TestCase):
    def test_synthetic_top1(self):
        names = ["佩可莉姆", "凯露（夏日）", "镜华", "优衣"]
        res = recognize(synthetic(names))
        self.assertEqual(res["count"], 4)
        got = [c["candidates"][0]["name"] for c in res["cells"]]
        self.assertEqual(got, names)

    def test_party_cells_detected(self):
        img = np.array(Image.open(FIXTURE).convert("RGB"))
        self.assertEqual(len(detect_cells(img)), 4)
        res = recognize(img)
        self.assertEqual(res["count"], 4)
        for c in res["cells"]:
            self.assertGreaterEqual(len(c["candidates"]), 3)
            self.assertIn("confidence", c)

    def test_blank_image(self):
        blank = np.full((200, 200, 3), 255, dtype=np.uint8)
        self.assertEqual(detect_cells(blank), [])
        self.assertEqual(recognize(blank)["count"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
