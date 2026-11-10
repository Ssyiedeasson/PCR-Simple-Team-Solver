# -*- coding: utf-8 -*-
"""Enemy-effect targeting (对首领的有效效果) tests."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pcr_team.analyzer import build_prompt  # noqa: E402
from pcr_team.engine import ENEMY_EFFECTS, TeamPlanner, load_roster  # noqa: E402

ROSTER = load_roster()
POOL = "佩可莉姆 香织 真琴 凯露（超负荷） 似似花 镜华 优衣 美里 莫妮卡"


def plan(effects):
    units, _ = ROSTER.resolve(POOL)
    return TeamPlanner(ROSTER, "pve", enemy_effects=effects).plan(units, team_count=1)


class TestEnemyEffects(unittest.TestCase):
    def test_catalog(self):
        for k in ("物理攻击", "魔法攻击", "技能值下降", "物理破甲", "控制"):
            self.assertIn(k, ENEMY_EFFECTS)

    def test_tp_down_prefers_drainer(self):
        base = plan([])["teams"][0]
        tp = plan(["技能值下降"])["teams"][0]
        self.assertGreater(tp["enemy_fit"], 0)
        self.assertIn("技能值下降", tp["enemy_matched"])
        names = [m["name"] for m in tp["members"]]
        self.assertIn("凯露（超负荷）", names)
        self.assertGreaterEqual(tp["enemy_fit"], 2.0)
        self.assertNotEqual(base["score"], tp["score"])

    def test_physical_vs_magic(self):
        phys = plan(["物理攻击"])["teams"][0]
        mag = plan(["魔法攻击"])["teams"][0]
        self.assertIn("物理攻击", phys["enemy_matched"])
        self.assertIn("魔法攻击", mag["enemy_matched"])
        self.assertEqual(phys["damage_type"], "物理")
        self.assertEqual(mag["damage_type"], "魔法")

    def test_prompt_contains_effects(self):
        p = plan(["物理攻击", "技能值下降"])
        prompt = build_prompt(p)
        self.assertIn("对首领的有效效果", prompt)
        self.assertIn("技能值下降", prompt)


if __name__ == "__main__":
    unittest.main(verbosity=2)
