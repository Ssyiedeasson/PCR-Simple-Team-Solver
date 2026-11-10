# -*- coding: utf-8 -*-
"""Command line interface."""
from __future__ import annotations

import argparse
import json
import os
import sys

from .analyzer import analyze, load_config
from .engine import TeamPlanner, load_roster

BAR = "█"


def _score_bar(score: float, width: int = 18) -> str:
    filled = int(round(score / 100 * width))
    return BAR * filled + "·" * (width - filled)


def print_plan(res: dict, roster) -> None:
    print(f"\n模式：{res['mode_label']}　角色池：{res['pool_size']} 人　生成队伍：{res['team_count']}")
    if not res["teams"]:
        print("可用角色不足 5 人，无法组队。")
        return
    for t in res["teams"]:
        bd = t["breakdown"]
        print("\n" + "═" * 72)
        print(f" #{t['rank']}  {t['archetype']}　评分 {t['score']}  {_score_bar(t['score'])}")
        print("─" * 72)
        for m in t["members"]:
            stars = "★6" if m["six_star"] else ""
            print(f"  [{m['slot']:<3}] {m['name']:<14} {m['position']} {m['attack']}"
                  f"  战力{m['power']:<5} {stars}")
        cov = bd["coverage"]
        print("  覆盖：" + "  ".join(f"{k}:{v:.2f}" for k, v in cov.items()))
        print(f"  拆分：战力{bd['power']} + 结构{bd['composition']} + 协同{bd['synergy']}"
              f" + 站位{bd['position']} - 扣分{bd['penalty']}")
        for n in t["notes"]:
            print(f"  ⚠ {n}")
        for s in t["suggestions"]:
            print(f"  💡 {s}")
    if res["unused"]:
        preview = "、".join(res["unused"][:18])
        print(f"\n未上场（{len(res['unused'])}）：{preview}{' ...' if len(res['unused']) > 18 else ''}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        prog="pcr-team", description="公主连结 Re:Dive 自动配队器（biligame wiki 数据）")
    ap.add_argument("chars", nargs="?", help="角色名，用空格/逗号分隔；也可用 --file")
    ap.add_argument("--file", help="从文本文件读取角色名（每行或逗号分隔）")
    ap.add_argument("--teams", type=int, default=1, help="要生成的队伍数，0=按角色池自动排满")
    ap.add_argument("--mode", choices=["pve", "pvp"], default="pve")
    ap.add_argument("--allow-reuse", action="store_true", help="允许角色跨队重复（PVP 变体）")
    ap.add_argument("--exclude", default="", help="排除的角色，逗号分隔")
    ap.add_argument("--beam", type=int, default=600, help="束搜索宽度（越大越准、越慢）")
    ap.add_argument("--include-unreleased", action="store_true",
                    help="包含 wiki 标记为未实装的角色（数据可能不完整）")
    ap.add_argument("--include-jp", action="store_true", help="包含日服先行角色（region=jp）")
    ap.add_argument("--sequence", action="store_true", help="三队连战模式：输出 3 支有顺序的队伍")
    ap.add_argument("--enemy-effects", default="",
                    help="对首领的有效效果，逗号分隔：物理攻击,魔法攻击,技能值下降,物理破甲,魔法破甲,控制,持续伤害,减疗,减攻,减速,驱散")
    ap.add_argument("--json", dest="json_out", help="把完整结果写入 JSON 文件")
    ap.add_argument("--analyze", action="store_true", help="调用配置的 API 分析（未配置则本地分析）")
    ap.add_argument("--config", default=None, help="配置文件路径")
    ap.add_argument("--list", action="store_true", help="列出全部角色")
    ap.add_argument("--find", help="按名称/外号搜索角色")
    ap.add_argument("--compact", action="store_true", help="精简输出")
    args = ap.parse_args(argv)

    roster = load_roster(include_unreleased=args.include_unreleased,
                         include_jp=args.include_jp)

    if args.list:
        for u in sorted(roster.units.values(),
                    key=lambda x: ({"前卫": 0, "中卫": 1, "后卫": 2}.get(x.position, 3), x.pos, x.name)):
            print(f"{u.name}\t{u.position}\t{u.attack}\t{','.join(u.roles)}\t{u.power}")
        return 0
    if args.find:
        q = args.find
        hits = [u for u in roster.units.values()
                if q in u.name or any(q in a for a in roster.roster_aliases(u.name))]
        for u in hits:
            print(f"{u.name}\t{u.position}/{u.attack}\t{','.join(u.roles)}\t外号："
                  + "、".join(sorted(roster.roster_aliases(u.name))[:8]))
        if not hits:
            print("没有找到匹配角色。")
        return 0

    text = args.chars or ""
    if args.file:
        text += "\n" + open(args.file, encoding="utf-8").read()
    if not text.strip():
        ap.print_help()
        return 2

    units, unknown = roster.resolve(text)
    if args.exclude:
        bad, _ = roster.resolve(args.exclude)
        drop = {u.name for u in bad}
        units = [u for u in units if u.name not in drop]
    if unknown:
        print("⚠ 未识别：" + "、".join(unknown), file=sys.stderr)
    if len(units) < 5:
        print(f"只识别到 {len(units)} 名角色，至少需要 5 名。", file=sys.stderr)
        return 1

    effects = [e for e in __import__("re").split(r"[,，、;；\s]+", args.enemy_effects) if e]
    planner = TeamPlanner(roster, args.mode, beam=args.beam, enemy_effects=effects)
    res = planner.plan(units, team_count=args.teams, allow_reuse=args.allow_reuse,
                       sequence=args.sequence)
    res["unknown"] = unknown
    if args.compact:
        for t in res["teams"]:
            print(f"{t['rank']}. {t['score']} {t['archetype']}: "
                  + " / ".join(m["name"] for m in t["members"]))
    else:
        print_plan(res, roster)

    if args.json_out:
        json.dump(res, open(args.json_out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print(f"\n完整结果已写入 {args.json_out}")

    if args.analyze:
        cfg = load_config(args.config) if args.config else load_config()
        cfg["extra_context"] = cfg.get("extra_context", "")
        payload = dict(res, extra_context=cfg.get("extra_context", ""))
        out = analyze(payload, cfg)
        print("\n" + "═" * 72)
        print(f"分析（{out['source']}）\n")
        print(out["text"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
