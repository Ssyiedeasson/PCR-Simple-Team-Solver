# -*- coding: utf-8 -*-
"""Regression guards for the calibrated scoring / search model."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pcr_team.engine import TeamPlanner, load_roster  # noqa: E402

ROSTER = load_roster()

GOOD_PVE = [
    ["佩可莉姆", "真琴", "香织", "咲恋", "优衣"],
    ["宫子", "真琴", "克莉丝提娜", "由加莉", "优衣"],
    ["佩可莉姆", "真琴", "克莉丝提娜", "香织", "优衣"],
    ["宫子", "凯露（夏日）", "似似花", "镜华", "美里"],
    ["纯", "凯露（夏日）", "似似花", "镜华", "优衣（新年）"],
]
BAD_PVE = [
    ["香织", "镜华", "凯露（夏日）", "初音", "杏奈"],
    ["美里", "千歌", "优衣", "由加莉", "可可萝"],
    ["佩可莉姆", "香织", "镜华", "优衣", "莫妮卡"],
]
GOOD_PVP = [
    ["佩可莉姆", "宫子", "初音", "凯露（夏日）", "优衣"],
    ["纯", "望", "莫妮卡", "初音", "镜华"],
    ["流夏", "静流", "克莉丝提娜", "咲恋", "优衣"],
]
BAD_PVP = [
    ["莫妮卡", "初音", "杏奈", "露娜", "似似花"],
    ["初音", "镜华", "凯露（夏日）", "优衣", "美里"],
]


def score(names, mode):
    return TeamPlanner(ROSTER, mode).score([ROSTER.units[n] for n in names]).total


class TestCalibration(unittest.TestCase):
    def test_good_pve_scores(self):
        scores = [score(n, "pve") for n in GOOD_PVE]
        self.assertGreaterEqual(min(scores), 80, scores)

    def test_bad_pve_scores(self):
        self.assertLessEqual(score(BAD_PVE[0], "pve"), 55)
        self.assertLessEqual(score(BAD_PVE[1], "pve"), 30)
        self.assertLessEqual(score(BAD_PVE[2], "pve"), 80)

    def test_good_beats_bad_pvp(self):
        good = min(score(n, "pvp") for n in GOOD_PVP)
        bad = max(score(n, "pvp") for n in BAD_PVP)
        self.assertGreater(good, bad + 10, (good, bad))

    def test_risk_model(self):
        self.assertGreaterEqual(ROSTER.units["杏奈"].risk, 0.9)
        self.assertGreater(ROSTER.units["伊莉亚"].risk, 0)
        self.assertEqual(ROSTER.units["真琴"].risk, 0)

    def test_anchor_search_beats_plain_beam(self):
        units = list(ROSTER.units.values())
        p = TeamPlanner(ROSTER, "pve")
        plain = p.best_teams(units, count=1, use_anchors=False)[0][1].total
        anchored = p.best_teams(units, count=1, use_anchors=True)[0][1].total
        self.assertGreaterEqual(anchored, plain)
        self.assertGreaterEqual(anchored, 90)

    def test_top_team_avoids_self_destruct(self):
        plan = TeamPlanner(ROSTER, "pve").plan(list(ROSTER.units.values()), team_count=1)
        names = [m["name"] for m in plan["teams"][0]["members"]]
        self.assertNotIn("杏奈", names)

    def test_power_base_present(self):
        for u in ROSTER.units.values():
            self.assertLessEqual(u.power, 100)
            self.assertGreaterEqual(u.power, 40)


if __name__ == "__main__":
    unittest.main(verbosity=2)
