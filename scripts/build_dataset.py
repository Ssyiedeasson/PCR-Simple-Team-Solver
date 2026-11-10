# -*- coding: utf-8 -*-
"""Parse biligame wikitext into a structured, tag-rich character dataset.

Derived per character:
  position row (前/中/后) + numeric position, damage type, rarity flags,
  skill texts, effect tags with scope (self / team / enemy), roles & role scores,
  burst coefficient, and a structural power estimate.
"""
import json
import math
import os
import re
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
RAW = os.path.join(HERE, "..", "pcr_team", "data", "raw_wikitext.json")
OUT = os.path.join(HERE, "..", "pcr_team", "data", "characters.json")

# (key, label, regex, default scope)
# distances are deliberately loose (skills often insert "前方范围内所有敌人的")
RULES = [
    ("phys_shred", "物理破甲",
     r"(降低|下降|削弱|减少)[^。\n]{0,18}?物理(?:[/·]魔法)?防御力|物理(?:[/·]魔法)?防御力[^。\n]{0,12}?(降低|下降|削弱|减少)",
     "enemy"),
    ("magic_shred", "魔法破甲",
     r"(降低|下降|削弱|减少)[^。\n]{0,18}?(?:物理[/·])?魔法防御力|(?:物理[/·])?魔法防御力[^。\n]{0,12}?(降低|下降|削弱|减少)",
     "enemy"),
    ("def_shred", "双防破甲",
     r"(降低|下降|削弱|减少)[^。\n]{0,18}?(物理|魔法)[/·]?(物理|魔法)?防御力",
     "enemy"),
    ("phys_buff", "物攻增益",
     r"物理攻击力[^。\n]{0,10}?提升|提升[^。\n]{0,14}?物理攻击力", "team"),
    ("magic_buff", "魔攻增益",
     r"魔法攻击力[^。\n]{0,10}?提升|提升[^。\n]{0,14}?魔法攻击力", "team"),
    ("tp_charge", "TP回复",
     r"(?<!不会)(?<!无法)(?<!不能)(?<!不再)(回复|恢复|提升|增加)[^。\n]{0,26}?技能值", "team"),
    ("tp_drain", "吸TP",
     r"(减少|吸收|夺取|削弱|降低)[^。\n]{0,14}?技能值", "enemy"),
    ("haste", "加速",
     r"行动速度[^。\n]{0,10}?提升|提升[^。\n]{0,22}?行动速度", "team"),
    ("heal", "治疗",
     r"(回复|恢复)[^。\n]{0,16}?(生命值|生命力)"
     r"|(生命值|生命力)[^。\n]{0,6}?(回复|恢复)(?!量?\s*(降低|下降|减少))", "team"),
    ("heal_aoe", "群体治疗",
     r"(回复|恢复)[^。\n]{0,20}?(全体|所有)[^。\n]{0,12}?(生命值|生命力)"
     r"|(全体|所有)[^。\n]{0,14}?(回复|恢复)[^。\n]{0,10}?(生命值|生命力)", "team"),
    ("heal_single", "单体治疗",
     r"(回复|恢复)[^。\n]{0,24}?(一名|单个|单体)[^。\n]{0,16}?(生命值|生命力)", "team"),
    ("atk_down", "减攻",
     r"(降低|下降|削弱|减少)[^。\n]{0,18}?攻击力", "enemy"),
    ("speed_down", "减速",
     r"(降低|下降|削弱|减少)[^。\n]{0,12}?行动速度", "enemy"),
    ("heal_down", "减疗",
     r"(降低|下降|削弱|减少)[^。\n]{0,18}?(回复量|治疗量|生命回复)"
     r"|(回复量|治疗量)[^。\n]{0,8}?(降低|下降|削弱)", "enemy"),
    ("shield", "护盾", r"(屏障|护盾)", "team"),
    ("taunt", "嘲讽", r"嘲讽", "self"),
    ("control", "控制",
     r"(眩晕|晕眩|束缚|冰冻|冻结|魅惑|睡眠|麻痹|击退|击飞|挑衅|混乱|暗黑|恐慌)", "enemy"),
    ("invuln", "无敌",
     r"(?:自身|自己)[^。\n]{0,10}?无敌|(?:处于|进入|变为|变身为)[^。\n]{0,10}?无敌|不会受到伤害", "self"),
    ("mitigation", "减伤", r"(伤害减免|减少受到的伤害|降低受到的伤害|减轻受到的伤害)", "self"),
    ("regen", "持续回复",
     r"持续(?!\d+\.?\d*\s*秒)[^。\n]{0,8}?(回复|恢复)[^。\n]{0,8}?(生命值|生命力)"
     r"|持续[^。\n]{0,6}?(生命值|生命力)[^。\n]{0,4}?(回复|恢复)", "team"),
    ("dot", "持续伤害", r"(中毒|灼烧|燃烧|持续伤害)", "enemy"),
    ("summon", "召唤", r"召唤", "team"),
    ("dispel", "驱散", r"驱散", "enemy"),
    ("cleanse", "净化", r"解除[^。\n]{0,12}?(异常状态|减益|弱化)", "team"),
    ("revive", "复活", r"复活", "team"),
    ("lifesteal", "吸血", r"吸血", "self"),
    ("counter", "反击", r"反击", "self"),
    ("nuke", "爆发", r"造成[^。\n]{0,16}?伤害[（(](大|特大|极大)", "enemy"),
]

# tags only meaningful at this scope for team building (display + audit)
TAG_SCOPES = {
    "phys_shred": {"enemy"}, "magic_shred": {"enemy"}, "def_shred": {"enemy"},
    "control": {"enemy"}, "phys_buff": {"team"}, "magic_buff": {"team"},
    "tp_charge": {"team"}, "haste": {"team"},
    "atk_down": {"enemy"}, "speed_down": {"enemy"}, "heal_down": {"enemy"},
    "tp_drain": {"enemy"}, "dot": {"enemy"},
}

RISK_RULES = [
    ("self_ko", 1.0, r"自身[^。\n]{0,8}(无法战斗|倒下|战斗不能)"),
    ("self_stun", 0.8, r"(眩晕|麻痹|睡眠|冰冻)自己|自身[^。\n]{0,8}(眩晕|麻痹|睡眠)"),
    ("self_debuff", 0.5, r"(降低|下降|减少)自身[^。\n]{0,10}防御力"),
    ("self_hp", 0.5, r"消耗自身[^。\n]{0,10}生命值"),
]

SELF_MARK = ("自身", "自己", "本身")
TEAM_MARK = ("己方", "我方", "友方", "队友", "伙伴", "同伴")
ENEMY_MARK = ("敌人", "敌方", "对手")

BASE_ALIASES = {
    "佩可莉姆": ["吃货", "佩可", "公主"],
    "可可萝": ["妈", "可可罗"],
    "凯露": ["黑猫", "凯留", "猫猫"],
    "凯露（夏日）": ["水黑", "泳装凯露", "水猫"],
    "凯露（新年）": ["春黑", "新年凯露"],
    "凯露（公主）": ["公黑", "公主凯露"],
    "优衣": ["ue", "由衣", "优依"],
    "优衣（新年）": ["春田", "新年优衣"],
    "优衣（公主）": ["公ue", "公主优衣"],
    "真琴": ["狼", "狼姐"],
    "真琴（夏日）": ["水狼"],
    "克莉丝提娜": ["克总", "克里斯蒂娜", "克里斯"],
    "静流": ["静流姐", "姐姐"],
    "宫子": ["布丁", "幽灵"],
    "空花": ["抖M"],
    "纯": ["纯哥", "纯姐"],
    "望": ["偶像"],
    "咲恋": ["充电宝", "咲恋妈妈"],
    "咲恋（夏日）": ["水咲恋", "泳装咲恋"],
    "由加莉": ["酒鬼"],
    "美里": ["美里妈妈"],
    "千歌": ["歌姬"],
    "镜华": ["xcw"],
    "镜华（夏日）": ["水镜华"],
    "杏奈": ["中二"],
    "伊莉亚": ["吸血鬼", "伊莉雅"],
    "莫妮卡": ["毛二力"],
    "珠希": ["猫剑"],
    "香织": ["狗"],
    "香织（夏日）": ["水狗"],
    "惠理子": ["病娇"],
    "姬塔": ["吉他", "团长"],
    "流夏": ["大姐头"],
    "纺希": ["裁缝"],
    "美美": ["兔子"],
    "美美（夏日）": ["水兔"],
    "未奏希": ["炸弹人"],
    "初音": ["miku"],
    "初音（夏日）": ["水初音"],
    "栞": ["诗音"],
    "绫音": ["熊"],
    "铃奈": ["暴击弓"],
    "美冬": ["美冬姐"],
    "静流（夏日）": ["水静流"],
    "佩可莉姆（公主）": ["公吃", "公主佩可"],
    "可可萝（公主）": ["公主可可萝"],
}

# curated corrections where keyword parsing under/over-reads the kit
ROLE_OVERRIDE = {
    "佩可莉姆": ["坦克", "输出"],
    "宫子": ["坦克"],
    "空花": ["坦克"],
    "纯": ["坦克"],
    "流夏": ["坦克"],
    "望": ["坦克", "辅助"],
    "静流": ["坦克", "辅助"],
    "真琴": ["破甲", "输出"],
    "真琴（夏日）": ["破甲", "输出"],
    "克莉丝提娜": ["破甲", "输出"],
    "香织": ["输出"],
    "镜华": ["输出"],
    "凯露（夏日）": ["破甲", "输出"],
    "凯露（新年）": ["输出"],
    "优衣": ["治疗", "辅助"],
    "优衣（新年）": ["治疗", "辅助"],
    "美里": ["治疗", "辅助"],
    "千歌": ["治疗", "辅助"],
    "咲恋": ["辅助", "输出"],
    "咲恋（夏日）": ["辅助"],
    "由加莉": ["辅助", "治疗"],
    "莫妮卡": ["辅助", "控制"],
    "露娜": ["辅助", "输出"],
    "似似花": ["辅助", "输出"],
    "似似花（新年）": ["辅助", "输出"],
    "可可萝": ["辅助", "治疗"],
    "可可萝（公主）": ["辅助", "治疗"],
}


def field(text, key):
    m = re.search(r"^\|" + re.escape(key) + r"=(.*)$", text, re.M)
    return m.group(1).strip() if m else ""


def clean(s):
    s = s.replace("<br/>", "\n").replace("<br>", "\n")
    s = re.sub(r"\[\[([^|\]]*\|)?([^\]]*)\]\]", r"\2", s)
    s = re.sub(r"\{\{[^}]*\}\}", "", s)
    s = re.sub(r"<[^>]+>", "", s)
    s = s.replace("&nbsp;", " ")
    return s.strip()


def clauses(text):
    """Split into effect-level clauses; commas separate independent effects."""
    out = []
    for part in re.split(r"[\n。；;，,]", text):
        part = part.strip(" 、")
        if part:
            out.append(part)
    return out


def scope_of(sentence, default):
    # team markers win: "为友方回复生命值，并吸收敌人伤害" is a team effect
    if any(m in sentence for m in TEAM_MARK):
        return "team"
    # a damaging clause targets enemies even if a self-buff follows in the same sentence
    if re.search(r"(造成|施加)[^。\n]{0,12}?(伤害|攻击)", sentence):
        return "enemy"
    if any(m in sentence for m in ENEMY_MARK) and not any(m in sentence for m in SELF_MARK):
        return "enemy"
    if any(m in sentence for m in SELF_MARK):
        return "self"
    return default


def analyse(text):
    effects = defaultdict(set)   # tag -> scopes
    hits = []
    for sent in clauses(text):
        for key, label, pat, default in RULES:
            if re.search(pat, sent):
                sc = scope_of(sent, default)
                effects[key].add(sc)
                hits.append({"tag": key, "label": label, "scope": sc, "text": sent})
        # AoE requires an actual damaging clause, not just "all enemies" in a debuff
        if (re.search(r"(全体|所有|范围内)", sent)
                and re.search(r"(敌人|敌方|对手)", sent)
                and re.search(r"(造成|施加)[^。\n]{0,12}?(伤害|攻击)", sent)):
            effects["aoe"].add("enemy")
            hits.append({"tag": "aoe", "label": "群攻", "scope": "enemy", "text": sent})
    return effects, hits


def burst_coef(formula_text):
    """Attack multiplier attached to 攻击力, e.g. 【3.2*物理攻击力】.

    Only numbers directly multiplying attack are damage ratios; numbers before
    *技能等级 or *生命值 are scaling constants and must be ignored.
    """
    vals = [float(x) for x in re.findall(
        r"([0-9]+(?:\.[0-9]+)?)\s*\*\s*(?:物理|魔法)?攻击力", formula_text)]
    vals += [float(x) for x in re.findall(
        r"(?:物理|魔法)?攻击力\s*\*\s*([0-9]+(?:\.[0-9]+)?)", formula_text)]
    return max(vals) if vals else 0.0


def main():
    raw = json.load(open(RAW, encoding="utf-8"))
    chars, skipped = [], []
    for page, wt in raw.items():
        if not wt.startswith("{{角色"):
            skipped.append(page)
            continue
        name = field(wt, "翻译名") or page
        pos_row, attack, pos_num = field(wt, "类型"), field(wt, "攻击方式"), field(wt, "位置")
        desc_keys = ("UB描述", "6星UB描述", "技能1描述", "技能2描述", "特殊技能1描述",
                     "特殊技能2描述", "特殊技能3描述", "EX技能描述", "专属技能描述", "二专技能描述")
        formula_keys = ("UB效果", "6星UB效果", "技能1效果", "技能2效果",
                        "专属技能效果", "二专技能效果")
        desc = "\n".join(clean(field(wt, k)) for k in desc_keys)
        formulas = " ".join(field(wt, k) for k in formula_keys)
        text = desc + "\n" + clean(formulas)

        if attack not in ("物理", "魔法"):
            p, m = text.count("物理伤害"), text.count("魔法伤害")
            attack = "物理" if p > m else ("魔法" if m > p else "支援")
        if pos_row not in ("前卫", "中卫", "后卫"):
            n = int(pos_num) if pos_num.isdigit() else 500
            pos_row = "前卫" if n < 300 else ("中卫" if n < 600 else "后卫")

        effects, hits = analyse(text)
        risk, risk_reasons = 0.0, []
        for key, weight, pat in RISK_RULES:
            if re.search(pat, text):
                risk = max(risk, weight)
                risk_reasons.append({"key": key, "weight": weight})
        tags = sorted(k for k in effects if effects[k] & TAG_SCOPES.get(k, effects[k]))
        coef = burst_coef(formulas)

        def has(tag, *scopes):
            return tag in effects and (not scopes or effects[tag] & set(scopes))


        # role scores
        tank = 0.0
        if pos_row == "前卫":
            tank += 1.0
            tank += 3.0 if has("taunt") else 0
            tank += 2.0 if has("mitigation", "self") else 0
            tank += 1.5 if has("shield") else 0
            tank += 1.0 if has("regen", "self") else 0
            tank += 0.8 if has("heal", "self") else 0
            tank += 0.6 if has("invuln") else 0
        support = 0.0
        for tag, w in (("tp_charge", 3), ("haste", 2.5), ("cleanse", 1.5),
                       ("dispel", 1.2), ("shield", 1.2), ("summon", 1.0)):
            if has(tag, "team"):
                support += w
        if has("phys_buff", "team") or has("magic_buff", "team"):
            support += 2.0
        if has("phys_buff", "self") or has("magic_buff", "self"):
            support += 0.4
        heal = 2.0 if has("heal", "team") else 0.0
        if has("regen", "team"):
            heal += 1.0
        shred = 3.0 if (has("phys_shred", "enemy") or has("magic_shred", "enemy")) else 0.0
        control = 2.0 if has("control") else 0.0
        dps = 0.0
        if has("nuke"):
            dps += 2.0
        if has("aoe"):
            dps += 1.2
        if has("dot"):
            dps += 0.6
        if has("counter"):
            dps += 0.4

        scores = {"坦克": tank, "治疗": heal, "破甲": shred, "辅助": support,
                  "控制": control, "输出": dps}
        roles = ROLE_OVERRIDE.get(name)
        if not roles:
            thresh = {"坦克": 2.4, "治疗": 2.0, "破甲": 3.0, "辅助": 1.5, "控制": 2.0, "输出": 1.0}
            roles = [r for r, s in sorted(scores.items(), key=lambda kv: -kv[1])
                     if s >= thresh[r]][:3]
        roles = [r for r in roles if r in scores]

        aliases = set()
        for a in re.split(r"[、,，/]", field(wt, "外号")):
            if a.strip():
                aliases.add(a.strip())
        aliases.update(BASE_ALIASES.get(name, []))
        aliases.update({name, page, field(wt, "kana")})
        aliases.discard("")

        playable = field(wt, "是否实装") == "是"
        six = field(wt, "是否6星") not in ("", "否", "无")
        ue = field(wt, "是否专武") not in ("", "否", "无")
        rarity = int(field(wt, "初始星级") or 1) or 1
        limited = field(wt, "限定") in ("1", "是")
        # structural power: burst multiplier + progression investment
        power_base = 46 + 5.2 * math.log2(1 + min(coef, 16)) + (6 if six else 0) \
            + (4 if ue else 0) + (3 if rarity >= 3 else 1) + (2 if limited else 0)
        power_base = round(min(power_base, 100), 1)
        power = power_base

        chars.append({
            "id": int(field(wt, "角色ID") or 0) or None,
            "name": name,
            "page": page,
            "aliases": sorted(aliases),
            "playable": playable,
            "position": pos_row,
            "pos": int(pos_num) if pos_num.isdigit() else 500,
            "attack": attack,
            "rarity": rarity,
            "limited": limited,
            "six_star": six,
            "unique_weapon": ue,
            "roles": roles,
            "role_scores": {k: round(v, 1) for k, v in scores.items()},
            "tags": tags,
            "tag_labels": sorted({h["label"] for h in hits if h["tag"] in tags}),
            "effects": {k: sorted(v) for k, v in effects.items()},
            "coef": coef,
            "power_base": power_base,
            "risk": risk,
            "risk_reasons": risk_reasons,
            "power": power,
            "ub_name": clean(field(wt, "UB")),
            "ub_desc": clean(field(wt, "UB描述")),
            "skill1": clean(field(wt, "技能1描述")),
            "skill2": clean(field(wt, "技能2描述")),
            "ue_skill": clean(field(wt, "专属技能描述")),
            "intro": clean(field(wt, "角色介绍")),
            "guild": field(wt, "所属"),
        })

    chars.sort(key=lambda c: (c["pos"], c["name"]))
    json.dump(chars, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("characters:", len(chars), "skipped:", skipped)
    print("roles:", Counter(r for c in chars for r in c["roles"]))
    print("attack:", Counter(c["attack"] for c in chars))
    print("no tags:", [c["name"] for c in chars if not c["tags"]])


if __name__ == "__main__":
    main()
