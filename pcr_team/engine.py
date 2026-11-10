# -*- coding: utf-8 -*-
"""Team planning engine.

The engine turns a roster into capability vectors (tank / heal / shred / dps /
tp ...), scores every 5-man composition with a role-coverage model, searches the
combination space with beam search, and then packs multiple teams without
reusing characters (or with reuse, for PvP style planning).
"""
from __future__ import annotations

import json
import math
import os
import time
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
CHAR_FILE = os.path.join(DATA, "characters.json")
META_FILE = os.path.join(DATA, "meta.json")

TEAM_SIZE = 5

# mode profiles: quota weights for composition satisfaction
PROFILES = {
    "pve": {
        "label": "PVE / 公会战",
        "weights": {"tank": 12, "dps": 16, "shred": 12, "support": 7,
                    "heal": 7, "control": 2, "sustain": 3},
        "needs": {"tank": 0.9, "dps": 2.0, "shred": 1.0, "support": 0.8,
                  "heal": 0.7, "control": 0.6, "sustain": 0.5},
    },
    "pvp": {
        "label": "PVP / 竞技场",
        "weights": {"tank": 11, "dps": 15, "shred": 8, "support": 9,
                    "heal": 3, "control": 13, "sustain": 2},
        "needs": {"tank": 0.9, "dps": 1.8, "shred": 0.8, "support": 0.9,
                  "heal": 0.4, "control": 1.2, "sustain": 0.3},
    },
}

SLOT_LABEL = {"tank": "坦克", "heal": "治疗", "shred": "破甲", "dps": "输出",
              "tp": "TP", "haste": "加速", "buff": "增益", "control": "控制"}

# 对首领的有效效果（对应游戏内「队伍编组 → 对首领的有效效果」）
ENEMY_EFFECTS = {
    "物理攻击": "首领受物理攻击有效 / 物理伤害提升",
    "魔法攻击": "首领受魔法攻击有效 / 魔法伤害提升",
    "技能值下降": "削减首领技能值 / 延缓其 UB",
    "物理破甲": "降低首领物理防御",
    "魔法破甲": "降低首领魔法防御",
    "控制": "眩晕/束缚/击退等控制生效",
    "持续伤害": "中毒/灼烧等持续伤害生效",
    "减疗": "降低首领回复量",
    "减攻": "降低首领攻击力",
    "减速": "降低首领行动速度",
    "驱散": "驱散首领增益",
}


def _norm_name(name: str) -> str:
    import re
    return re.sub(r"[()（）\s]+", "", name or "")


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


@dataclass
class Unit:
    name: str
    position: str
    pos: int
    attack: str
    power: float
    roles: List[str]
    tags: List[str]
    effects: Dict[str, List[str]]
    coef: float
    risk: float = 0.0
    guild: str = ""
    playable: bool = True
    region: str = "cn"
    source: str = ""
    six_star: bool = False
    unique_weapon: bool = False
    ub_desc: str = ""
    intro: str = ""
    caps: Dict[str, float] = field(default_factory=dict)

    @property
    def short(self) -> str:
        return self.name.replace("（", "(").replace("）", ")")

    def to_dict(self) -> dict:
        return {
            "name": self.name, "position": self.position, "pos": self.pos,
            "attack": self.attack, "power": self.power, "roles": self.roles,
            "tags": self.tags, "coef": self.coef, "six_star": self.six_star,
            "playable": self.playable, "region": self.region, "risk": self.risk,
            "unique_weapon": self.unique_weapon, "caps": {k: round(v, 3) for k, v in self.caps.items()},
        }


@dataclass
class Breakdown:
    total: float = 0.0
    power: float = 0.0
    composition: float = 0.0
    synergy: float = 0.0
    position: float = 0.0
    penalty: float = 0.0
    enemy: float = 0.0
    enemy_matched: List[str] = field(default_factory=list)
    coverage: Dict[str, float] = field(default_factory=dict)
    archetype: str = ""
    damage_type: str = ""
    notes: List[str] = field(default_factory=list)
    strengths: List[str] = field(default_factory=list)
    suggestions: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "total": round(self.total, 1), "power": round(self.power, 1),
            "composition": round(self.composition, 1), "synergy": round(self.synergy, 1),
            "position": round(self.position, 1), "penalty": round(self.penalty, 1),
            "enemy": round(self.enemy, 1), "enemy_matched": self.enemy_matched,
            "coverage": {k: round(v, 2) for k, v in self.coverage.items()},
            "archetype": self.archetype, "damage_type": self.damage_type,
            "notes": self.notes, "strengths": self.strengths,
            "suggestions": self.suggestions,
        }


class Roster:
    def __init__(self, characters: List[dict], meta: Optional[dict] = None):
        self.meta = meta or {}
        self.units: Dict[str, Unit] = {}
        self.alias: Dict[str, str] = {}
        alias_candidates = {}
        for raw in characters:
            u = self._make_unit(raw)
            self.units[u.name] = u
            for a in raw.get("aliases", []) + [u.name]:
                alias_candidates.setdefault(a, []).append(u.name)
        overrides = self.meta.get("alias_overrides", {})
        for a, names in alias_candidates.items():
            if len(names) == 1:
                self.alias[a] = names[0]
            else:
                pref = overrides.get(a)
                self.alias[a] = pref if pref in names else names[0]
        # bracket/space-insensitive index, only for unambiguous entries
        self.alias_norm = {}
        for a, target in self.alias.items():
            key = _norm_name(a)
            if key and self.alias_norm.get(key, target) == target:
                self.alias_norm[key] = target
            else:
                self.alias_norm[key] = None

    # ---------- construction ----------
    def _make_unit(self, raw: dict) -> Unit:
        eff = raw.get("effects", {})
        roles = raw.get("roles", [])
        scores = raw.get("role_scores", {})
        attack = raw.get("attack", "物理")

        def has(tag: str, scopes: Sequence[str] = ()) -> bool:
            return tag in eff and (not scopes or bool(set(eff[tag]) & set(scopes)))

        tank = 1.0 if "坦克" in roles else _clamp(scores.get("坦克", 0) / 5.0, 0, 0.55)
        if has("heal_aoe", ("team",)):
            heal = 1.0
        elif has("heal", ("team",)):
            heal = 0.55
        elif has("regen", ("team",)):
            heal = 0.7
        elif has("heal", ("self",)):
            heal = 0.18
        else:
            heal = 0.0
        sustain = 1.0 if (has("heal", ("team",)) or has("regen", ("team",))) else 0.0
        shred_phys = 1.0 if has("phys_shred", ("enemy",)) else 0.0
        shred_magic = 1.0 if has("magic_shred", ("enemy",)) else 0.0
        dps = _clamp((raw.get("power", 60) - 40) / 45.0, 0.15, 1.0)
        base = dps if ("输出" in roles) else dps * 0.35
        dps_phys = base if attack == "物理" else 0.0
        dps_magic = base if attack == "魔法" else 0.0
        tp = 1.0 if has("tp_charge", ("team",)) else 0.0
        haste = 1.0 if has("haste", ("team",)) else 0.0
        buff_p = 1.0 if has("phys_buff", ("team",)) else 0.0
        buff_m = 1.0 if has("magic_buff", ("team",)) else 0.0
        control = 1.0 if has("control", ("enemy",)) else 0.0
        shield = 0.8 if has("shield", ("team",)) else (0.4 if has("shield") else 0.0)
        cleanse = 0.8 if has("cleanse", ("team",)) else 0.0
        tp_drain = 1.0 if has("tp_drain", ("enemy",)) else 0.0
        dot = 1.0 if has("dot", ("enemy",)) else 0.0
        atk_down = 1.0 if has("atk_down", ("enemy",)) else 0.0
        speed_down = 1.0 if has("speed_down", ("enemy",)) else 0.0
        heal_down = 1.0 if has("heal_down", ("enemy",)) else 0.0
        dispel = 1.0 if has("dispel", ("enemy",)) else 0.0

        tier = self.meta.get("tier_overrides", {}).get(raw["name"])
        power = float(tier) if tier is not None else float(raw.get("power", 60))

        caps = {
            "tank": tank, "heal": heal, "sustain": sustain,
            "shred_phys": shred_phys, "shred_magic": shred_magic,
            "dps_phys": dps_phys, "dps_magic": dps_magic,
            "tp": tp, "haste": haste, "buff_p": buff_p, "buff_m": buff_m,
            "control": control, "shield": shield, "cleanse": cleanse,
            "tp_drain": tp_drain, "dot": dot, "atk_down": atk_down,
            "speed_down": speed_down, "heal_down": heal_down, "dispel": dispel,
        }
        return Unit(
            name=raw["name"], position=raw.get("position", "中卫"),
            pos=int(raw.get("pos", 500)), attack=attack, power=power,
            roles=roles, tags=raw.get("tag_labels", raw.get("tags", [])),
            effects=eff, coef=float(raw.get("coef", 0)), risk=float(raw.get("risk", 0)),
            guild=raw.get("guild", ""), playable=bool(raw.get("playable", True)),
            region=raw.get("region", "cn"), source=raw.get("source", ""),
            six_star=bool(raw.get("six_star")),
            unique_weapon=bool(raw.get("unique_weapon")),
            ub_desc=raw.get("ub_desc", ""), intro=raw.get("intro", ""), caps=caps,
        )

    # ---------- lookup ----------
    def find(self, name: str) -> Optional[Unit]:
        import re
        name = name.strip()
        if not name:
            return None
        if name in self.units:
            return self.units[name]
        if name in self.alias:
            return self.units[self.alias[name]]
        # bracketed / spacing variants: 凯露夏日 == 凯露（夏日）
        norm = _norm_name(name)
        if norm:
            if norm in self.units:
                return self.units[norm]
            target = self.alias_norm.get(norm)
            if target:
                return self.units[target]
        # conservative fuzzy: >=3 chars and a unique candidate
        if len(name) >= 3:
            cands = {self.alias[a] for a in self.alias
                     if len(a) >= 3 and (a.startswith(name) or name in a)}
            cands |= {t for k, t in self.alias_norm.items()
                      if t and len(k) >= 3 and (k.startswith(norm) or norm in k)}
            if len(cands) == 1:
                return self.units[cands.pop()]
        return None

    def roster_aliases(self, name: str) -> List[str]:
        return [a for a, target in self.alias.items() if target == name]

    def resolve(self, text: str) -> Tuple[List[Unit], List[str]]:
        import re
        text = text.strip()
        # protect names that themselves contain separators (e.g. 秋乃、咲恋)
        protected = {}
        for name in sorted(list(self.units) + list(self.alias), key=len, reverse=True):
            if ("、" in name or "，" in name) and name in text:
                token = f"\u241f{len(protected)}\u241f"
                text = text.replace(name, token)
                protected[token] = name
        parts = [p for p in re.split(r"[\s,，、;；/|]+", text) if p]
        parts = [protected.get(p, p) for p in parts]
        found, unknown, seen = [], [], set()
        for p in parts:
            u = self.find(p)
            if not u:
                unknown.append(p)
            elif u.name not in seen:
                seen.add(u.name)
                found.append(u)
        return found, unknown


class TeamPlanner:
    def __init__(self, roster: Roster, mode: str = "pve", beam: int = 600,
                 enemy_effects: Optional[Sequence[str]] = None):
        self.roster = roster
        self.mode = mode if mode in PROFILES else "pve"
        self.enemy_effects = [e for e in (enemy_effects or []) if e in ENEMY_EFFECTS]
        self.profile = PROFILES[self.mode]
        self.beam = beam
        self.pairs = self._build_pairs()
        self.curated = roster.meta.get("synergy_pairs", [])

    # ---------- pair model ----------
    def _build_pairs(self) -> Dict[str, float]:
        return {}

    def pair_bonus(self, a: Unit, b: Unit) -> float:
        bonus = 0.0
        ca, cb = a.caps, b.caps
        # shred enables damage of the same type
        if ca["shred_phys"] and cb["dps_phys"] > 0.4:
            bonus += 5
        if cb["shred_phys"] and ca["dps_phys"] > 0.4:
            bonus += 5
        if ca["shred_magic"] and cb["dps_magic"] > 0.4:
            bonus += 5
        if cb["shred_magic"] and ca["dps_magic"] > 0.4:
            bonus += 5
        # tp battery / haste feeds damage
        feed = max(ca["tp"] + ca["haste"], cb["tp"] + cb["haste"])
        carry = max(ca["dps_phys"] + ca["dps_magic"], cb["dps_phys"] + cb["dps_magic"])
        if feed and carry > 0.5:
            bonus += 3
        # tank protects carries
        if (ca["tank"] >= 0.8 and cb["dps_phys"] + cb["dps_magic"] > 0.5) or \
           (cb["tank"] >= 0.8 and ca["dps_phys"] + ca["dps_magic"] > 0.5):
            bonus += 2.5
        # healer keeps tank alive
        if (ca["heal"] and cb["tank"] >= 0.8) or (cb["heal"] and ca["tank"] >= 0.8):
            bonus += 2
        # same damage school
        if ca["dps_phys"] > 0.3 and cb["dps_phys"] > 0.3:
            bonus += 1.5
        if ca["dps_magic"] > 0.3 and cb["dps_magic"] > 0.3:
            bonus += 1.5
        # redundant main tanks / healers
        if ca["tank"] >= 0.9 and cb["tank"] >= 0.9:
            bonus -= 4
        if ca["heal"] >= 0.9 and cb["heal"] >= 0.9:
            bonus -= 3
        for entry in self.curated:
            a1, b1, val = entry[0], entry[1], float(entry[2])
            if {a.name, b.name} == {a1, b1}:
                bonus += val
        return bonus

    # ---------- full team scoring ----------
    def score(self, units: Sequence[Unit]) -> Breakdown:
        prof = self.profile
        w, need = prof["weights"], prof["needs"]
        caps = [u.caps for u in units]

        def top_sum(values: Sequence[float], decay: Sequence[float]) -> float:
            vals = sorted(values, reverse=True)
            return sum(v * d for v, d in zip(vals, decay))

        tanks = [c["tank"] for c in caps]
        heals = [c["heal"] for c in caps]
        agg_tank = top_sum(tanks, (1.0, 0.35, 0.15))
        agg_heal = top_sum(heals, (1.0, 0.2))
        agg_sustain = min(1.3, sum(min(0.7, c["sustain"]) for c in caps))
        agg_control = min(2.0, sum(c["control"] for c in caps))
        agg_shred_p = top_sum([c["shred_phys"] for c in caps], (1.0, 0.55, 0.3))
        agg_shred_m = top_sum([c["shred_magic"] for c in caps], (1.0, 0.55, 0.3))
        agg_tp = top_sum([c["tp"] for c in caps], (1.0, 0.4))
        agg_haste = top_sum([c["haste"] for c in caps], (1.0, 0.4))
        agg_buff = max(top_sum([c["buff_p"] for c in caps], (1.0, 0.35)),
                       top_sum([c["buff_m"] for c in caps], (1.0, 0.35)))
        agg_support = agg_tp * 1.0 + agg_haste * 0.85 + agg_buff * 0.55 \
            + max(c["cleanse"] for c in caps) * 0.3

        phys = sum(c["dps_phys"] for c in caps)
        magic = sum(c["dps_magic"] for c in caps)
        total_dmg = phys + magic
        if total_dmg <= 0:
            dmg_type = "混伤"
        elif phys / total_dmg >= 0.62:
            dmg_type = "物理"
        elif magic / total_dmg >= 0.62:
            dmg_type = "魔法"
        else:
            dmg_type = "混伤"

        # off-school damage only counts at 25%: its shred/buffs do not line up
        dps_vals = []
        for c in caps:
            if dmg_type == "物理":
                v = c["dps_phys"] + c["dps_magic"] * 0.25
            elif dmg_type == "魔法":
                v = c["dps_magic"] + c["dps_phys"] * 0.25
            else:
                v = c["dps_phys"] + c["dps_magic"]
            dps_vals.append(v * (0.6 if c["tank"] >= 0.8 else 1.0))
        dps = top_sum(dps_vals, (1.0, 0.7, 0.3, 0.15, 0.1))
        shred = agg_shred_m if dmg_type == "魔法" else agg_shred_p if dmg_type == "物理" \
            else max(agg_shred_p, agg_shred_m)
        off_shred = agg_shred_p if dmg_type == "魔法" else agg_shred_m
        minority = min(phys, magic) / total_dmg if total_dmg else 0.0

        sat = {
            "tank": _clamp(agg_tank / need["tank"]),
            "dps": _clamp(dps / need["dps"]),
            "shred": _clamp((shred + 0.2 * off_shred) / need["shred"]),
            "support": _clamp(agg_support / need["support"]),
            "heal": _clamp(agg_heal / need["heal"]),
            "control": _clamp(agg_control / need["control"]),
            "sustain": _clamp(agg_sustain / need["sustain"]),
        }
        total_w = sum(w.values())
        composition = 50 * sum(w[k] * sat[k] for k in w) / total_w

        avg_power = sum(u.power for u in units) / len(units)
        power_score = 22 * _clamp((avg_power - 48) / 30)

        front = sum(1 for u in units if u.position == "前卫")
        back = sum(1 for u in units if u.position == "后卫")
        mid = len(units) - front - back
        if front == 0:
            pos_score = 2 if back <= 2 else 0
        elif front >= 4:
            pos_score = 3
        elif front >= 3:
            pos_score = 6
        else:
            pos_score = 10 if (mid >= 1 and back >= 1) else 8
        pos_score = min(10, pos_score)

        pair_sum = 0.0
        for i in range(len(units)):
            for j in range(i + 1, len(units)):
                pair_sum += min(7.0, self.pair_bonus(units[i], units[j]))
        synergy = _clamp(pair_sum / 58.0) * 11
        if dmg_type in ("物理", "魔法") and shred >= 1.0:
            synergy += 3.5
        synergy = min(14.0, synergy)

        penalty = 0.0
        notes, suggestions, strengths = [], [], []
        if dmg_type == "混伤" and (agg_shred_p + agg_shred_m) < 1.4:
            penalty += 7
            notes.append("物理与魔法输出各占一半，却无法同时破甲，实际伤害会明显缩水。")
        elif dmg_type in ("物理", "魔法") and minority >= 0.3:
            penalty += 14 * minority
            if (agg_shred_m if dmg_type == "物理" else agg_shred_p) < 0.5:
                penalty += 3  # off-school carries get no shred support at all
            notes.append("队内混入了不同伤害属性的角色，吃不到主属性的破甲与增益。")
        if agg_tank < 0.6 and self.mode == "pve":
            penalty += 9
            notes.append("没有可靠主坦，前排容易在 UB 循环成型前崩溃。")
        if max(heals) < 0.9 and self.mode == "pve":
            penalty += 6
            notes.append("没有专职群体治疗，长线作战的续航吃紧。")
        if front == 0:
            penalty += 7
            notes.append("全队站位于中后排，缺少承伤位。")
        if sum(1 for c in caps if c["tank"] >= 0.9) >= 2:
            penalty += 4
            notes.append("双主坦占用了输出/辅助位，节奏偏慢。")
        if sum(1 for c in caps if c["heal"] >= 0.9) >= 2 and self.mode == "pve":
            penalty += 4
            notes.append("两名专职治疗溢出，公会战通常只需要一名。")
        if dps >= 1.2 and shred < 0.5:
            penalty += 7
            notes.append("缺少破甲，伤害会被高防御目标大量吃掉。")
        risk_sum = sum(u.risk for u in units)
        if risk_sum >= 0.5:
            penalty += (4.5 if self.mode == "pve" else 2.0) * risk_sum
            if risk_sum >= 1.5:
                notes.append("队内高风险角色较多（自伤/自控/战斗不能），实战容错率低。")
        n_dps = sum(1 for c in caps if c["dps_phys"] + c["dps_magic"] >= 0.5
                    and c["tank"] < 0.8 and c["heal"] < 0.8)
        if n_dps >= 4:
            penalty += 5
            notes.append("输出位过多（4 人以上），功能位被挤占。")
        n_shred = sum(1 for c in caps if c["shred_phys"] + c["shred_magic"] >= 0.9)
        if n_shred >= 4:
            penalty += 3
            notes.append("破甲角色堆叠过多，收益递减。")
        if back >= 5:
            penalty += 6
            notes.append("五名后卫缺乏前排，开局就会被压线。")

        if sat["tank"] >= 0.9:
            strengths.append("前排承伤稳定")
        if sat["shred"] >= 0.9:
            strengths.append(f"{dmg_type}破甲覆盖完整")
        if sat["dps"] >= 0.9:
            strengths.append("主力输出充足")
        if sat["support"] >= 0.8:
            strengths.append("TP/增益循环顺畅")
        if sat["heal"] >= 0.8:
            strengths.append("续航能力强")
        if sat["control"] >= 0.9:
            strengths.append("控制链充足")

        if sat["shred"] < 0.6 and dps >= 1.0:
            suggestions.append("补一名同属性破甲角色，可显著提升实际伤害。")
        if sat["tank"] < 0.6:
            suggestions.append("补一名前卫主坦，避免后排被快速击穿。")
        if sat["heal"] < 0.5 and self.mode == "pve":
            suggestions.append("若为长线作战，建议补一名群体治疗。")
        if sat["support"] < 0.5:
            suggestions.append("补 TP 回复或加速角色，让 UB 循环更顺。")
        if front == 0:
            suggestions.append("至少安排一名前卫，改善站位与仇恨分配。")

        enemy_fit, enemy_matched = self._enemy_fit(caps, phys, magic, agg_shred_p,
                                                   agg_shred_m,
                                                   sum(c["tp_drain"] for c in caps),
                                                   sum(c["dot"] for c in caps),
                                                   sum(c["heal_down"] for c in caps),
                                                   sum(c["atk_down"] for c in caps),
                                                   sum(c["speed_down"] for c in caps),
                                                   max(c["dispel"] for c in caps))
        if self.enemy_effects and enemy_fit < 2.5:
            notes.append("已选对首领有效效果，但本队覆盖不足：" + "、".join(self.enemy_effects))
        archetype = self._archetype(dmg_type, sat, units)
        total = _clamp(power_score + composition + synergy + pos_score + enemy_fit - penalty, 0, 99)
        return Breakdown(
            total=total, power=power_score, composition=composition,
            synergy=synergy, position=pos_score, penalty=penalty,
            enemy=enemy_fit, enemy_matched=enemy_matched,
            coverage={k: round(v, 2) for k, v in sat.items()},
            archetype=archetype, damage_type=dmg_type, notes=notes,
            strengths=strengths, suggestions=suggestions,
        )

    def _enemy_fit(self, caps, phys: float, magic: float, shred_p: float, shred_m: float,
                   tp_drain: float, dot: float, heal_down: float, atk_down: float,
                   speed_down: float, dispel: float) -> Tuple[float, List[str]]:
        if not self.enemy_effects:
            return 0.0, []
        total = phys + magic
        share_p = phys / total if total else 0.0
        share_m = magic / total if total else 0.0
        matched: List[str] = []
        fit = 0.0
        for eff in self.enemy_effects:
            if eff == "物理攻击":
                v = 4.0 * share_p
            elif eff == "魔法攻击":
                v = 4.0 * share_m
            elif eff == "技能值下降":
                v = 3.0 * min(1.0, tp_drain / 0.8)
            elif eff == "物理破甲":
                v = 2.0 * min(1.0, shred_p / 0.9)
            elif eff == "魔法破甲":
                v = 2.0 * min(1.0, shred_m / 0.9)
            elif eff == "控制":
                v = 2.0 * min(1.0, sum(c["control"] for c in caps) / 0.9)
            elif eff == "持续伤害":
                v = 2.0 * min(1.0, dot / 0.7)
            elif eff == "减疗":
                v = 2.0 * min(1.0, heal_down / 0.7)
            elif eff == "减攻":
                v = 1.5 * min(1.0, atk_down / 0.9)
            elif eff == "减速":
                v = 1.5 * min(1.0, speed_down / 0.9)
            elif eff == "驱散":
                v = 1.5 * min(1.0, dispel / 0.5)
            else:
                v = 0.0
            if v >= 0.8:
                matched.append(eff)
            fit += v
        return min(12.0, fit), matched

    def _archetype(self, dmg_type: str, sat: Dict[str, float], units: Sequence[Unit]) -> str:
        control = sum(1 for u in units if u.caps["control"] >= 0.9)
        if self.mode == "pvp" and (control >= 2 or sat["control"] >= 1.0):
            return "控制爆发队"
        if sat["tank"] >= 0.9 and sat["heal"] >= 0.8 and sat["dps"] < 0.75:
            return "持久消耗队"
        if dmg_type == "物理":
            return "物理破甲队" if sat["shred"] >= 0.7 else "物理速攻队"
        if dmg_type == "魔法":
            return "魔法破甲队" if sat["shred"] >= 0.7 else "魔法爆发队"
        return "混伤速攻队"

    # ---------- search ----------
    def _unit_value(self, u: Unit) -> float:
        c = u.caps
        return (u.power * 0.55 + 6 * c["tank"] + 5 * c["heal"] + 4 * c["sustain"]
                + 6 * (c["dps_phys"] + c["dps_magic"]) + 5 * (c["shred_phys"] + c["shred_magic"])
                + 3 * c["tp"] + 2.5 * c["haste"] + 2 * c["control"]
                + 1.5 * (c["buff_p"] + c["buff_m"])
                + 0.8 * (c["tp_drain"] + c["dot"] + c["heal_down"] + c["speed_down"])
                - 4.0 * u.risk)

    def _candidates(self, units: List[Unit], limit: int) -> List[Unit]:
        scored = sorted(units, key=lambda u: -self._unit_value(u))
        if len(scored) <= limit:
            return scored
        # keep a balanced pool: strongest overall + strongest per capability
        keep = {u.name: u for u in scored[:limit // 2]}
        for cap in ("tank", "heal", "shred_phys", "shred_magic", "dps_phys",
                    "dps_magic", "tp", "haste", "control", "sustain"):
            for u in sorted(scored, key=lambda x: -x.caps[cap])[:16]:
                keep.setdefault(u.name, u)
        rest = sorted(keep.values(), key=lambda u: -self._unit_value(u))[:limit]
        return rest

    def _anchor_pool(self, units: List[Unit], k: int = 10) -> List[Unit]:
        """Role-diverse anchors: pure DPS units are often pruned by unit value."""
        picks: List[Unit] = []
        seen = set()
        for cap, kk in (("dps_magic", 3), ("dps_phys", 3), ("tank", 2),
                        ("heal", 2), ("shred_magic", 1), ("shred_phys", 1),
                        ("tp", 1), ("haste", 1)):
            for u in sorted(units, key=lambda x: -x.caps[cap])[:kk]:
                if u.name not in seen:
                    seen.add(u.name)
                    picks.append(u)
        return picks[:k]

    def _anchor_teams(self, pool: List[Unit], anchor: Unit, width: int = 80,
                      top: int = 6) -> List[Tuple[List[Unit], Breakdown]]:
        rest = [u for u in pool if u.name != anchor.name]
        cand = [anchor] + rest[:139]
        n = len(cand)
        vals = [self._unit_value(u) for u in cand]
        pair = [[self.pair_bonus(cand[i], cand[j]) for j in range(n)] for i in range(n)]
        beam: List[Tuple[Tuple[int, ...], float]] = [((0,), vals[0])]
        for depth in range(4):
            wd = width if depth < 2 else width * 2
            nxt: Dict[Tuple[int, ...], float] = {}
            for state, score in beam:
                for j in range(1, n):
                    if j in state:
                        continue
                    s = score + vals[j] + sum(pair[i][j] * 0.55 for i in state)
                    key = tuple(sorted(state + (j,)))
                    if s > nxt.get(key, -1e9):
                        nxt[key] = s
            beam = sorted(nxt.items(), key=lambda kv: -kv[1])[:wd]
        out = []
        seen = set()
        for state, _ in beam:
            team = [cand[i] for i in state]
            key = tuple(sorted(u.name for u in team))
            if key in seen:
                continue
            seen.add(key)
            out.append((team, self.score(team)))
        out.sort(key=lambda tb: -tb[1].total)
        return out[:top]

    def best_teams(self, units: List[Unit], count: int = 1, diversity: float = 0.6,
                   time_budget: float = 12.0,
                   use_anchors: bool = True) -> List[Tuple[List[Unit], Breakdown]]:
        """Beam search for the best 5-man teams from `units`.

        Partial states carry a coverage prior (tank/heal/shred/tp/dps) so that
        balanced compositions survive pruning even when their individual unit
        values are mediocre.
        """
        if len(units) < TEAM_SIZE:
            return []
        pool = self._candidates(units, limit=140)
        n = len(pool)
        values = [self._unit_value(u) for u in pool]
        caps_vec = [(u.caps["tank"], u.caps["heal"],
                     max(u.caps["shred_phys"], u.caps["shred_magic"]),
                     u.caps["tp"] + u.caps["haste"],
                     u.caps["dps_phys"] + u.caps["dps_magic"]) for u in pool]
        pair = [[self.pair_bonus(pool[i], pool[j]) for j in range(n)] for i in range(n)]
        width = min(self.beam, max(120, 4000 // max(1, n // 40)))
        start = time.time()

        def prior(caps):
            tank, heal, shred, tp, dps = caps
            return (2.5 * min(1.0, tank) + 2.0 * min(1.0, heal)
                    + 2.0 * min(1.0, shred) + 1.5 * min(1.0, tp)
                    + 1.2 * min(1.0, dps / 2.0))

        beam: List[Tuple[Tuple[int, ...], Tuple[float, Tuple[float, ...]]]] = [
            ((), (0.0, (0.0, 0.0, 0.0, 0.0, 0.0)))]
        for depth in range(TEAM_SIZE):
            # near the leaves, widen the beam: future pair synergy can rescue a
            # prefix that looks mediocre at depth 3-4
            width_d = width if depth < 3 else (width * 5 if depth == 3 else width * 8)
            nxt: Dict[Tuple[int, ...], Tuple[float, Tuple[float, ...]]] = {}
            for state, (score, caps) in beam:
                last = state[-1] if state else -1
                for j in range(last + 1, n):
                    s = score + values[j]
                    for i in state:
                        s += pair[i][j] * 0.55
                    nc = tuple(round(a + b, 4) for a, b in zip(caps, caps_vec[j]))
                    key = state + (j,)
                    if s > nxt.get(key, (-1e9, ()))[0]:
                        nxt[key] = (s, nc)
            if not nxt:
                break
            beam = sorted(nxt.items(),
                          key=lambda kv: -(kv[1][0] + prior(kv[1][1])))[:width_d]
            if time.time() - start > time_budget:
                break

        scored = []
        seen = set()
        for state, _ in beam:
            team = [pool[i] for i in state]
            key = tuple(sorted(u.name for u in team))
            if key in seen:
                continue
            seen.add(key)
            bd = self.score(team)
            scored.append((team, bd))
        if use_anchors:
            for anchor in self._anchor_pool(pool):
                for team, bd in self._anchor_teams(pool, anchor):
                    key = tuple(sorted(u.name for u in team))
                    if key not in seen:
                        seen.add(key)
                        scored.append((team, bd))
        scored.sort(key=lambda tb: -tb[1].total)

        picked: List[Tuple[List[Unit], Breakdown]] = []
        for team, bd in scored:
            if len(picked) >= count:
                break
            names = {u.name for u in team}
            if any(len(names & {u.name for u in t}) / 5.0 > diversity for t, _ in picked):
                continue
            picked.append((team, bd))
        return picked

    def plan(self, units: List[Unit], team_count: int = 1, allow_reuse: bool = False,
             diversity: float = 0.6, sequence: bool = False) -> dict:
        units = [u for u in units if u]
        unique = {u.name: u for u in units}
        units = list(unique.values())
        teams: List[dict] = []
        if allow_reuse:
            target = max(1, team_count)
            found = self.best_teams(units, count=target, diversity=0.85, use_anchors=True)
            for i, (team, bd) in enumerate(found):
                teams.append(self._team_payload(i + 1, team, bd, set()))
            unused = []
        else:
            if sequence:
                team_count = 3
            remaining = list(units)
            max_teams = len(remaining) // TEAM_SIZE
            target = max_teams if team_count <= 0 else min(team_count, max_teams)
            for i in range(target):
                if len(remaining) < TEAM_SIZE:
                    break
                found = self.best_teams(remaining, count=1, use_anchors=(i == 0))
                if not found:
                    break
                team, bd = found[0]
                teams.append(self._team_payload(i + 1, team, bd, set()))
                used = {u.name for u in team}
                remaining = [u for u in remaining if u.name not in used]
            if teams:
                teams = self._improve_packing(teams, units)
            unused = sorted(u.name for u in remaining)
        if sequence:
            phases = ["第1队 · 开局", "第2队 · 承接", "第3队 · 收尾"]
            for i, t in enumerate(teams):
                t["phase"] = phases[i] if i < len(phases) else f"第{i+1}队"
            for i, t in enumerate(teams):
                t["rank"] = i + 1
        return {
            "mode": self.mode,
            "mode_label": PROFILES[self.mode]["label"],
            "sequence": bool(sequence),
            "sequence_score": round(sum(t["score"] for t in teams), 1) if sequence else 0,
            "enemy_effects": self.enemy_effects,
            "team_count": len(teams),
            "teams": teams,
            "unused": unused,
            "pool_size": len(units),
        }

    def _team_payload(self, rank: int, team: List[Unit], bd: Breakdown, used: set) -> dict:
        members = sorted(team, key=lambda u: u.pos)
        slots = self._assign_slots(team)
        return {
            "rank": rank,
            "score": round(bd.total, 1),
            "archetype": bd.archetype,
            "damage_type": bd.damage_type,
            "power": round(sum(u.power for u in team) / len(team), 1),
            "members": [self._member_payload(u, team, slots) for u in members],
            "breakdown": bd.to_dict(),
            "enemy_fit": round(bd.enemy, 1),
            "enemy_matched": bd.enemy_matched,
            "notes": bd.notes,
            "strengths": bd.strengths,
            "suggestions": bd.suggestions,
        }

    def _member_payload(self, u: Unit, team: List[Unit], slots: Dict[str, str]) -> dict:
        slot = slots.get(u.name, "辅助")
        return {
            "name": u.name, "position": u.position, "pos": u.pos,
            "attack": u.attack, "power": round(u.power, 1), "roles": u.roles,
            "tags": u.tags[:6], "slot": slot, "ub": u.ub_desc, "risk": u.risk,
            "six_star": u.six_star, "unique_weapon": u.unique_weapon,
        }

    def _assign_slots(self, team: Sequence[Unit]) -> Dict[str, str]:
        """Pick each unit's dominant team role, then de-duplicate tank/healer/carry labels."""
        roles: Dict[str, str] = {}
        for u in team:
            c = u.caps
            weights = {
                "tank": c["tank"] * 1.80,
                "heal": c["heal"] * 1.40,
                "shred": (c["shred_phys"] + c["shred_magic"]) * 1.15,
                "dps": (c["dps_phys"] + c["dps_magic"]) * 1.00,
                "support": c["tp"] * 0.90 + c["haste"] * 0.70 + c["control"] * 0.50
                           + max(c["buff_p"], c["buff_m"]) * 0.50 + c["shield"] * 0.20,
            }
            best = max(weights, key=weights.get)
            roles[u.name] = best if weights[best] > 0.25 else "support"

        counts = {"tank": 0, "heal": 0, "shred": 0, "dps": 0, "support": 0}
        labels: Dict[str, str] = {}
        for u in team:
            r = roles[u.name]
            counts[r] += 1
            n = counts[r]
            if r == "tank":
                labels[u.name] = "主坦" if n == 1 else "副坦"
            elif r == "heal":
                labels[u.name] = "治疗" if n == 1 else "副治疗"
            elif r == "shred":
                labels[u.name] = "破甲" if n == 1 else "副破甲"
            elif r == "dps":
                labels[u.name] = "主C" if n == 1 else "副C"
            else:
                labels[u.name] = "辅助" if n == 1 else "副辅助"
        return labels

    def _improve_packing(self, teams: List[dict], units: List[Unit]) -> List[dict]:
        """Local search: swap members between teams to raise the total score."""
        name_map = {u.name: u for u in units}
        data = [[name_map[m["name"]] for m in t["members"]] for t in teams]
        scores = [self.score(t) for t in data]
        for _ in range(2):
            improved = False
            # keep the flagship team intact; optimize the rest around it
            for i in range(1, len(data)):
                for j in range(i + 1, len(data)):
                    base = scores[i].total + scores[j].total
                    best = (base, None)
                    for a in range(5):
                        for b in range(5):
                            ta, tb = list(data[i]), list(data[j])
                            ta[a], tb[b] = tb[b], ta[a]
                            sa, sb = self.score(ta), self.score(tb)
                            if sa.total + sb.total > best[0] + 1e-6:
                                best = (sa.total + sb.total, (ta, tb, sa, sb))
                    if best[1]:
                        ta, tb, sa, sb = best[1]
                        data[i], data[j], scores[i], scores[j] = ta, tb, sa, sb
                        improved = True
            if not improved:
                break
        payloads = []
        for team, bd in zip(data, scores):
            payloads.append(self._team_payload(0, team, bd, set()))
        payloads.sort(key=lambda t: -t["score"])
        for idx, p in enumerate(payloads):
            p["rank"] = idx + 1
        return payloads


def load_roster(char_file: str = CHAR_FILE, meta_file: str = META_FILE,
                include_unreleased: bool = False, include_jp: bool = False) -> Roster:
    characters = json.load(open(char_file, encoding="utf-8"))
    if not include_unreleased:
        characters = [c for c in characters if c.get("playable", True)]
    if not include_jp:
        characters = [c for c in characters if c.get("region", "cn") != "jp"]
    meta = {}
    if os.path.exists(meta_file):
        meta = json.load(open(meta_file, encoding="utf-8"))
    return Roster(characters, meta)
