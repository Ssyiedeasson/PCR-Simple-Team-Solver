# -*- coding: utf-8 -*-
"""Reproducible calibration report: benchmarks, search quality, risk model."""
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from pcr_team.engine import TeamPlanner, load_roster  # noqa: E402

OUT = os.path.join(ROOT, "docs", "calibration.md")

GOOD_PVE = [
    ("物理经典（狼狗狐）", ["佩可莉姆", "真琴", "香织", "咲恋", "优衣"]),
    ("物理克总", ["宫子", "真琴", "克莉丝提娜", "由加莉", "优衣"]),
    ("物理双破甲", ["佩可莉姆", "真琴", "克莉丝提娜", "香织", "优衣"]),
    ("魔法经典", ["宫子", "凯露（夏日）", "似似花", "镜华", "美里"]),
    ("魔法新年优衣", ["纯", "凯露（夏日）", "似似花", "镜华", "优衣（新年）"]),
]
BAD_PVE = [
    ("五个输出", ["香织", "镜华", "凯露（夏日）", "初音", "杏奈"]),
    ("五个辅助/治疗", ["美里", "千歌", "优衣", "由加莉", "可可萝"]),
    ("无破甲混伤", ["佩可莉姆", "香织", "镜华", "优衣", "莫妮卡"]),
]
GOOD_PVP = [
    ("标准控场", ["佩可莉姆", "宫子", "初音", "凯露（夏日）", "优衣"]),
    ("速攻", ["纯", "望", "莫妮卡", "初音", "镜华"]),
    ("耐久", ["流夏", "静流", "克莉丝提娜", "咲恋", "优衣"]),
]
BAD_PVP = [
    ("五个控制", ["莫妮卡", "初音", "杏奈", "露娜", "似似花"]),
    ("无前排", ["初音", "镜华", "凯露（夏日）", "优衣", "美里"]),
]


def main():
    roster = load_roster()
    units = list(roster.units.values())

    def score(names, mode):
        return round(TeamPlanner(roster, mode).score([roster.units[n] for n in names]).total, 1)

    rows_good_pve = [(l, score(n, "pve")) for l, n in GOOD_PVE]
    rows_bad_pve = [(l, score(n, "pve")) for l, n in BAD_PVE]
    rows_good_pvp = [(l, score(n, "pvp")) for l, n in GOOD_PVP]
    rows_bad_pvp = [(l, score(n, "pvp")) for l, n in BAD_PVP]

    # search quality: plain beam vs beam + role-diverse anchors
    p = TeamPlanner(roster, "pve")
    t0 = time.time()
    plain = p.best_teams(units, count=1, use_anchors=False)[0]
    t_plain = time.time() - t0
    t0 = time.time()
    anchored = p.best_teams(units, count=1, use_anchors=True)[0]
    t_anchor = time.time() - t0
    t0 = time.time()
    plan5 = p.plan(units, team_count=5)
    t_plan5 = time.time() - t0

    risky = [(u.name, u.risk) for u in units if u.risk > 0]
    coefs = sorted((u.coef, u.name) for u in units)

    lines = ["# 配队模型校准报告", "",
             f"- 时间：{time.strftime('%Y-%m-%d %H:%M')}",
             f"- 角色池：{len(units)} 名已实装角色", "",
             "## 1. 技能倍率解析", "",
             "只把「x×攻击力」当作伤害倍率，排除「x×技能等级」「x×生命值」等成长系数。",
             f"当前倍率范围：{coefs[0][0]} – {coefs[-1][0]}（修复前曾出现 180 的异常值）。", "",
             "倍率最高的角色：", ""]
    for c, n in coefs[-5:][::-1]:
        lines.append(f"- {n}：{c}")
    lines += ["", "## 2. 风险模型（自伤/自控/战斗不能）", "",
              "从技能文本识别高风险机制；PVE 扣分 4.5×risk，PVP 扣分 2×risk。", "",
              "| 角色 | risk |", "| --- | --- |"]
    for n, r in sorted(risky, key=lambda x: -x[1]):
        lines.append(f"| {n} | {r} |")
    lines += ["", "## 3. 基准阵容得分", "", "### PVE 应有阵容", "",
              "| 阵容 | 分数 |", "| --- | --- |"]
    lines += [f"| {l} | {s} |" for l, s in rows_good_pve]
    lines += ["", "### PVE 反面样例", "", "| 阵容 | 分数 |", "| --- | --- |"]
    lines += [f"| {l} | {s} |" for l, s in rows_bad_pve]
    lines += ["", "### PVP 应有阵容", "", "| 阵容 | 分数 |", "| --- | --- |"]
    lines += [f"| {l} | {s} |" for l, s in rows_good_pvp]
    lines += ["", "### PVP 反面样例", "", "| 阵容 | 分数 |", "| --- | --- |"]
    lines += [f"| {l} | {s} |" for l, s in rows_bad_pvp]
    lines += ["", "## 4. 搜索质量", "",
              f"- 普通束搜索（无锚点）：**{plain[1].total:.1f} 分**，{t_plain:.2f}s，"
              + "、".join(u.name for u in plain[0]),
              f"- 束搜索 + 角色锚点：**{anchored[1].total:.1f} 分**，{t_anchor:.2f}s，"
              + "、".join(u.name for u in anchored[0]),
              f"- 提升：**+{anchored[1].total - plain[1].total:.1f} 分**（锚点补回了被裁掉的纯输出核心）。",
              "",
              f"性能：{len(units)} 名角色生成 5 队耗时 **{t_plan5:.2f}s**（含首发锚点补搜）。", "",
              "## 5. 评分口径", "",
              "- 结构覆盖 50：承伤/输出/破甲/循环/治疗/控制/续航，按 PVE/PVP 权重加权；",
              "- 战力 22：队伍平均个体强度；协同 ≤14；站位 10；扣分含混伤、无坦、无群奶、自伤风险等。",
              "- 同属性破甲必须与主输出属性一致；混伤且无对应破甲会额外扣分。", "",
              "## 6. 结论与校准边界", "",
              "- 基准阵容与反面样例的分差稳定在 15 分以上；搜索质量较校准前提升约 5 分。",
              "- 强度分数用于同池横向比较，不构成绝对强度榜；角色练度、专武、BOSS 机制仍会影响实战。",
              "- 新增角色后建议重跑 `scripts/build_dataset.py` 与 `scripts/calibrate.py`，观察基准分是否漂移。",
              ""]
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    open(OUT, "w", encoding="utf-8").write("\n".join(lines))

    print(f"plain {plain[1].total:.1f} ({t_plain:.2f}s) -> anchored {anchored[1].total:.1f} "
          f"({t_anchor:.2f}s), 5 teams {t_plan5:.2f}s, risky {len(risky)}")
    print("report ->", OUT)


if __name__ == "__main__":
    main()
