# -*- coding: utf-8 -*-
import json
import os
import sys
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pcr_team.analyzer import analyze, build_prompt, load_config  # noqa: E402
from pcr_team.engine import TeamPlanner, load_roster  # noqa: E402

ROSTER = load_roster()
ROSTER_TEXT = ("真琴 香织 克莉丝提娜 咲恋 优衣 佩可莉姆 宫子 凯露（夏日） 镜华 "
               "似似花 美里 莫妮卡 初音 杏奈 由加莉 可可萝")


def units(text=ROSTER_TEXT):
    u, unknown = ROSTER.resolve(text)
    assert not unknown, unknown
    return u


class TestRoster(unittest.TestCase):
    def test_size(self):
        self.assertGreaterEqual(len(ROSTER.units), 270)
        full = load_roster(include_unreleased=True)
        self.assertGreaterEqual(len(full.units), 290)
        self.assertGreater(len(full.units), len(ROSTER.units))

    def test_jp_alias_and_merged_roster(self):
        # 日文名可直接解析已有角色
        self.assertEqual(ROSTER.find("ペコリーヌ").name, "佩可莉姆")
        self.assertEqual(ROSTER.find("キャル(サマー)").name, "凯露（夏日）")
        # GameWith 补录的日服角色按国服已实装处理
        u = ROSTER.find("アグライア")
        self.assertIsNotNone(u)
        self.assertEqual(u.region, "cn")
        self.assertTrue(u.roles)

    def test_unreleased_excluded(self):
        # wiki 标记未实装的角色默认不进角色池，显式开启才可用
        self.assertIsNone(ROSTER.find("大和"))
        full = load_roster(include_unreleased=True)
        self.assertIsNotNone(full.find("大和"))
        self.assertFalse(full.find("大和").playable)

    def test_alias(self):
        self.assertEqual(ROSTER.find("吃货").name, "佩可莉姆")
        self.assertEqual(ROSTER.find("狼").name, "真琴")
        self.assertEqual(ROSTER.find("xcw").name, "镜华")
        self.assertEqual(ROSTER.find("水黑").name, "凯露（夏日）")

    def test_duo_name_with_separator(self):
        units, unknown = ROSTER.resolve("秋乃、咲恋 真琴 优衣")
        self.assertEqual([u.name for u in units], ["秋乃、咲恋", "真琴", "优衣"])
        self.assertEqual(unknown, [])

    def test_input_formats(self):
        a, _ = ROSTER.resolve("真琴,香织、克莉丝提娜\n咲恋")
        self.assertEqual(len(a), 4)


class TestPlanner(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan = TeamPlanner(ROSTER, "pve").plan(units(), team_count=2)

    def test_teams(self):
        self.assertEqual(len(self.plan["teams"]), 2)
        names = []
        for t in self.plan["teams"]:
            self.assertEqual(len(t["members"]), 5)
            self.assertEqual(len({m["name"] for m in t["members"]}), 5)
            names += [m["name"] for m in t["members"]]
        self.assertEqual(len(names), len(set(names)), "默认模式下角色不能跨队重复")

    def test_order_and_range(self):
        scores = [t["score"] for t in self.plan["teams"]]
        self.assertEqual(scores, sorted(scores, reverse=True))
        for s in scores:
            self.assertGreater(s, 0)
            self.assertLessEqual(s, 99)

    def test_best_team_has_structure(self):
        t = self.plan["teams"][0]
        roles = {m["slot"] for m in t["members"]}
        self.assertIn("主坦", roles)
        self.assertTrue(any(r in roles for r in ("治疗", "辅助")))
        self.assertIn(t["damage_type"], ("物理", "魔法", "混伤"))

    def test_auto_fill(self):
        pool = units("佩可莉姆 宫子 纯 流夏 真琴 香织 克莉丝提娜 咲恋 优衣 美里 "
                     "凯露（夏日） 镜华 似似花 莫妮卡 初音 杏奈 露娜 千歌 由加莉 可可萝 珠希 惠理子")
        plan = TeamPlanner(ROSTER, "pve").plan(pool, team_count=0)
        self.assertEqual(plan["team_count"], 4)  # 22 // 5
        self.assertEqual(len(plan["unused"]), 2)

    def test_sequence_three_teams(self):
        pool = units("佩可莉姆 宫子 纯 流夏 真琴 香织 克莉丝提娜 咲恋 优衣 美里 "
                     "凯露（夏日） 镜华 似似花 莫妮卡 初音 杏奈 露娜 千歌")
        plan = TeamPlanner(ROSTER, "pve").plan(pool, sequence=True)
        self.assertTrue(plan["sequence"])
        self.assertEqual(len(plan["teams"]), 3)
        names = [m["name"] for t in plan["teams"] for m in t["members"]]
        self.assertEqual(len(names), 15)
        self.assertEqual(len(set(names)), 15)
        self.assertEqual([t["phase"] for t in plan["teams"]],
                         ["第1队 · 开局", "第2队 · 承接", "第3队 · 收尾"])
        self.assertAlmostEqual(plan["sequence_score"],
                               round(sum(t["score"] for t in plan["teams"]), 1), places=1)

    def test_allow_reuse(self):
        pool = units("真琴 香织 克莉丝提娜 咲恋 优衣 佩可莉姆")
        plan = TeamPlanner(ROSTER, "pvp").plan(pool, team_count=3, allow_reuse=True)
        self.assertEqual(len(plan["teams"]), 3)
        self.assertTrue(all(len(t["members"]) == 5 for t in plan["teams"]))

    def test_deterministic(self):
        p1 = TeamPlanner(ROSTER, "pve").plan(units(), team_count=2)
        p2 = TeamPlanner(ROSTER, "pve").plan(units(), team_count=2)
        self.assertEqual([t["score"] for t in p1["teams"]],
                         [t["score"] for t in p2["teams"]])

    def test_pvp_control(self):
        pool = units("佩可莉姆 宫子 纯 流夏 望 静流 空花 真琴 香织 克莉丝提娜 咲恋 优衣 美里 千歌 "
                     "凯露（夏日） 镜华 似似花 莫妮卡 初音 杏奈 露娜 由加莉 可可萝 珠希")
        plan = TeamPlanner(ROSTER, "pvp").plan(pool, team_count=2)
        self.assertTrue(any("控制" in t["archetype"] for t in plan["teams"]) or
                        plan["teams"][0]["breakdown"]["coverage"]["control"] > 0)

    def test_full_roster_performance(self):
        pool = list(ROSTER.units.values())
        start = time.time()
        plan = TeamPlanner(ROSTER, "pve").plan(pool, team_count=5)
        elapsed = time.time() - start
        self.assertEqual(len(plan["teams"]), 5)
        self.assertLess(elapsed, 15, f"配队耗时 {elapsed:.1f}s")


class TestAnalysis(unittest.TestCase):
    def test_local_analysis(self):
        plan = TeamPlanner(ROSTER, "pve").plan(units(), team_count=1)
        out = analyze(plan, {"provider": "local"})
        self.assertEqual(out["source"], "local")
        self.assertIn("队伍", out["text"])
        self.assertIn("## 队伍 1", out["text"])

    def test_prompt_contains_members(self):
        plan = TeamPlanner(ROSTER, "pve").plan(units(), team_count=1)
        prompt = build_prompt(plan)
        self.assertIn(plan["teams"][0]["members"][0]["name"], prompt)
        self.assertIn("一句话结论", prompt)

    def test_config_defaults(self):
        cfg = load_config("/nonexistent/config.json")
        self.assertIn("provider", cfg)
        self.assertIn("system_prompt", cfg)

    def test_openai_without_key_falls_back(self):
        plan = TeamPlanner(ROSTER, "pve").plan(units(), team_count=1)
        out = analyze(plan, {"provider": "openai", "api_key": ""})
        self.assertEqual(out["source"], "local-fallback")
        self.assertIn("队伍", out["text"])


class TestDataset(unittest.TestCase):
    def test_fields(self):
        for name in ("真琴", "优衣", "凯露（夏日）", "佩可莉姆"):
            u = ROSTER.units[name]
            self.assertIn(u.attack, ("物理", "魔法", "支援"))
            self.assertIn(u.position, ("前卫", "中卫", "后卫"))
            self.assertTrue(u.roles)
            self.assertGreater(u.power, 0)

    def test_shred_detection(self):
        self.assertGreater(ROSTER.units["真琴"].caps["shred_phys"], 0.9)
        self.assertGreater(ROSTER.units["凯露（夏日）"].caps["shred_magic"], 0.9)
        self.assertGreater(ROSTER.units["凯露（夏日）"].caps["dps_magic"], 0.5)

    def test_healer_detection(self):
        self.assertGreater(ROSTER.units["优衣"].caps["heal"], 0.9)
        self.assertGreater(ROSTER.units["美里"].caps["heal"], 0.9)

    def test_tank_detection(self):
        for n in ("佩可莉姆", "宫子", "纯", "流夏", "望", "静华" if False else "静流"):
            self.assertGreater(ROSTER.units[n].caps["tank"], 0.85, n)

    def test_tp_battery(self):
        self.assertGreater(ROSTER.units["咲恋"].caps["tp"], 0.9)
        self.assertGreater(ROSTER.units["由加莉"].caps["tp"], 0.9)


if __name__ == "__main__":
    unittest.main(verbosity=2)
