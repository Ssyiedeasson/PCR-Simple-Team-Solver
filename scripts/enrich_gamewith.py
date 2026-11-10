# -*- coding: utf-8 -*-
"""Enrich the CN character DB with GameWith (JP) data.

- add Japanese names / aliases for existing characters
- add JP-ahead characters (region=jp) with position/attack/role from detail pages
- download GameWith unit icons and build a recognition index
"""
import json
import os
import re
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DATA = os.path.join(ROOT, "pcr_team", "data")
GW_ROWS = os.path.join(ROOT, "research2", "gw_rows.json")
RAW = os.path.join(DATA, "raw_wikitext.json")
CHARS = os.path.join(DATA, "characters.json")
GW_ICON_DIR = os.path.join(DATA, "icons_gw")
GW_INDEX = os.path.join(DATA, "icon_index_gw.json")
UA = "Mozilla/5.0 (PCR-TeamBuilder)"

VMAP = {
    "夏日": ["サマー"], "新年": ["ニューイヤー"], "公主": ["プリンセス"],
    "万圣节": ["ハロウィン"], "圣诞节": ["クリスマス"], "情人节": ["バレンタイン"],
    "大江户": ["オーエド"], "魔法少女": ["マジカル"], "插班生": ["編入生"],
    "游骑兵": ["レンジャー"], "礼服": ["ドレス", "儀装束"], "灰姑娘": ["シンデレラ"],
    "沙漠": ["砂漠", "サラサリア"], "咖啡厅": ["カフェ"], "工作服": ["作業服"],
    "黑暗": ["ノワール", "ダーク"], "超负荷": ["オーバーロード"], "圣学祭": ["聖学祭"],
    "偶像大师": ["デレマス"], "时间旅行": ["タイムトラベル"], "炼金术师": ["アルケミスト"],
    "教官": ["コマンダー", "教官"], "解放者": ["リベレイター"], "星幽": ["アストラル", "星幽"],
    "探索者": ["エクスプローラー"], "梦想乐园": ["ドリームパーク"], "仙境": ["ワンダー"],
    "舞台": ["ステージ"], "海盗": ["パイレーツ"], "怪盗": ["怪盗"], "露营": ["キャンプ"],
    "春日": ["スプリング", "春"], "冬日": ["ウィンター"], "天使": ["エンジェル"],
    "堕天使": ["堕天使"], "狂野": ["ワイルド"], "阿尔法": ["アルファ"],
    "枪手": ["ガンナー"], "女武神": ["ヴァルキュリア"], "潜水艇": ["サブマリン"],
    "哥特": ["ゴシック"], "伊邪那美": ["イサナミ"], "完美": ["パルフェクト"],
    "吉欧盖赫纳": ["ジオゲヘナ"], "终焉龙": ["ヴァードラッヘ"],
}

ROLE_MAP = {
    "アタッカー": ["输出"], "バッファー": ["辅助"], "デバッファー": ["破甲", "辅助"],
    "タンク": ["坦克"], "ヒーラー": ["治疗"], "サポート": ["辅助"],
}
POS_MAP = {"前衛": "前卫", "中衛": "中卫", "後衛": "后卫"}
ATK_MAP = {"物理攻撃": "物理", "魔法攻撃": "魔法"}


def norm(s: str) -> str:
    return s.replace("（", "(").replace("）", ")").replace("　", "").replace(" ", "").replace("＆", "&")


def field(w: str, k: str) -> str:
    m = re.search(r"^\|" + k + r"=(.*)$", w, re.M)
    return m.group(1).strip() if m else ""


def fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    for _ in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return r.read().decode("utf-8", "ignore")
        except Exception:  # noqa: BLE001
            time.sleep(1)
    return ""



JP_RULES = [
    ("phys_shred", r"物理防御力(を|ダウン|減少|下げ)"),
    ("magic_shred", r"魔法防御力(を|ダウン|減少|下げ)"),
    ("heal", r"(HP|ＨＰ)(を)?(小|中|大|特大)?回復|HP回復"),
    ("heal_aoe", r"味方全体.{0,24}(HP|ＨＰ).{0,8}回復"),
    ("tp_charge", r"TPを?(小|中|大|特大)?回復|TP上昇"),
    ("haste", r"行動速度(アップ|を上げ|上昇)"),
    ("control", r"(スタン|行動不能|束縛|魅了|睡眠|麻痺|吹き飛ばし|暗闇|恐怖)"),
    ("shield", r"(バリア|シールド)"),
    ("taunt", r"挑発"),
    ("atk_down", r"攻撃力(ダウン|を下げ|減少)"),
    ("speed_down", r"行動速度(ダウン|を下げ|減少)"),
    ("heal_down", r"回復量(ダウン|を下げ|減少)"),
    ("tp_drain", r"TP(を)?(減少|ダウン|吸収|奪)"),
    ("dot", r"(毒|火傷|継続ダメージ)"),
    ("aoe", r"(敵全体|範囲内の敵|最大[0-9]+キャラ)"),
    ("nuke", r"(特大ダメージ|大ダメージ)"),
    ("phys_buff", r"物理攻撃力(アップ|を上げ|上昇)"),
    ("magic_buff", r"魔法攻撃力(アップ|を上げ|上昇)"),
    ("regen", r"継続HP回復"),
    ("invuln", r"(無効|回避)"),
    ("mitigation", r"(ダメージ軽減|被ダメージ減少)"),
    ("cleanse", r"(弱体効果|デバフ).{0,8}(解除|回復)"),
]
TEAM_TAGS = {"heal", "heal_aoe", "shield", "tp_charge", "haste", "phys_buff",
             "magic_buff", "regen", "cleanse", "mitigation"}
ENEMY_TAGS = {"phys_shred", "magic_shred", "control", "atk_down", "speed_down",
              "heal_down", "tp_drain", "dot", "aoe", "nuke"}
JP_LABELS = {"phys_shred": "物理破甲", "magic_shred": "魔法破甲", "heal": "治疗",
             "heal_aoe": "群体治疗", "tp_charge": "TP回复", "haste": "加速",
             "control": "控制", "shield": "护盾", "taunt": "嘲讽", "atk_down": "减攻",
             "speed_down": "减速", "heal_down": "减疗", "tp_drain": "吸TP",
             "dot": "持续伤害", "aoe": "群攻", "nuke": "爆发", "phys_buff": "物攻增益",
             "magic_buff": "魔攻增益", "regen": "持续回复", "invuln": "无敌",
             "mitigation": "减伤", "cleanse": "净化"}


def jp_analyze(text: str):
    effects, labels = {}, {}
    for key, pat in JP_RULES:
        scopes = set()
        for clause in re.split(r"[。；;、,\n]", text):
            if not re.search(pat, clause):
                continue
            if key in TEAM_TAGS and re.search(r"(味方|全体|仲間)", clause):
                scopes.add("team")
            elif key in ENEMY_TAGS and re.search(r"(敵|相手)", clause):
                scopes.add("enemy")
            elif re.search(r"(自分|自身)", clause):
                scopes.add("self")
            else:
                scopes.add("enemy" if key in ENEMY_TAGS else "team")
        if scopes:
            effects[key] = sorted(scopes)
            labels[key] = JP_LABELS.get(key, key)
    return effects, labels


def parse_skills(text: str) -> dict:
    def seg(start, ends):
        i = text.find(start)
        if i < 0:
            return ""
        j = len(text)
        for e in ends:
            k = text.find(e, i + len(start))
            if k > i:
                j = min(j, k)
        return text[i:j][:600]
    return {
        "ub_desc": seg("ユニオンバースト", ["スキル1", "スキル2", "スキル3", "EXスキル"]),
        "skill1": seg("スキル1", ["スキル2", "スキル3", "EXスキル"]),
        "skill2": seg("スキル2", ["スキル3", "EXスキル"]),
    }


def parse_detail(html: str) -> dict:
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text)
    m = re.search(r"タイプ\s*(前衛|中衛|後衛)\s*/\s*(物理攻撃|魔法攻撃)", text)
    pos = POS_MAP.get(m.group(1)) if m else None
    atk = ATK_MAP.get(m.group(2)) if m else None
    role = None
    mr = re.search(r"ロール\s*([^\s]{1,12})", text)
    if mr:
        role = mr.group(1)
    skills = parse_skills(text)
    skill_text = " ".join(skills.values())
    effects, labels = jp_analyze(skill_text)
    return {"position": pos, "attack": atk, "gw_role": role,
            "skills": skills, "effects": effects, "labels": labels}


def main():
    rows = json.load(open(GW_ROWS, encoding="utf-8"))
    chars = json.load(open(CHARS, encoding="utf-8"))
    by_name = {c["name"]: c for c in chars}
    raw = json.load(open(RAW, encoding="utf-8"))

    # JP names per existing character
    base_kana = {}
    for c in chars:
        k = field(raw.get(c["page"], ""), "片假名")
        if k:
            base_kana[c["name"]] = k
    jp_names = {}
    for c in chars:
        names = set()
        n = c["name"]
        m = re.match(r"^(.*?)（(.*?)）$", n)
        if m:
            base, var = m.group(1), m.group(2)
            bk = base_kana.get(base) or base_kana.get(n)
            if bk and var in VMAP:
                for jp_var in VMAP[var]:
                    names.add(f"{bk}({jp_var})")
        if n in base_kana:
            names.add(base_kana[n])
        j = field(raw.get(c["page"], ""), "日文名")
        if j:
            names.add(j)
        if names:
            jp_names[n] = names

    gw_norm = {norm(k): k for k in rows}
    matched, unmatched = {}, []
    for n, al in jp_names.items():
        hit = next((gw_norm[a] for a in al if a in gw_norm), None)
        if hit:
            matched[n] = hit
        else:
            unmatched.append(n)

    # enrich matched
    for cn, jp in matched.items():
        c = by_name[cn]
        c["name_jp"] = jp
        c["aliases"] = sorted(set(c.get("aliases", [])) | jp_names.get(cn, set()) | {jp})
        c["region"] = "cn"
        c["gw_role"] = rows[jp]["text"].split(" ")[1] if len(rows[jp]["text"].split(" ")) > 1 else ""
        c["gw_icon"] = rows[jp].get("icon")
        c["gw_href"] = rows[jp].get("href")

    # new JP characters
    used = set(matched.values())
    new_names = [k for k in rows if k not in used]
    print("matched", len(matched), "new", len(new_names))

    def build_new(name):
        info = rows[name]
        detail_html = fetch(info["href"]) if info.get("href") else ""
        d = parse_detail(detail_html) if detail_html else {}
        text = info.get("text", "")
        role_txt = re.search(r"\s([^\s]*型)\s", " " + text + " ")
        role = d.get("gw_role") or (role_txt.group(1) if role_txt else "")
        roles = ROLE_MAP.get(role, ["辅助"])
        eff = d.get("effects") or {}
        if "heal" in eff and "治疗" not in roles:
            roles = ["治疗"] + roles
        if ("phys_shred" in eff or "magic_shred" in eff) and "破甲" not in roles:
            roles = ["破甲"] + roles
        score = 0.0
        nums = re.findall(r"\b\d\.\d\b", text)
        if nums:
            score = max(float(x) for x in nums)
        power = round(min(88, 55 + score * 3.2), 1) if score else 60.0
        return {
            "id": None, "name": name, "page": "", "aliases": [name],
            "playable": True, "region": "cn", "source": "gamewith",
            "position": d.get("position") or "中卫",
            "pos": {"前卫": 200, "中卫": 450, "后卫": 700}.get(d.get("position"), 500),
            "attack": d.get("attack") or "魔法",
            "rarity": 3, "limited": False, "six_star": False, "unique_weapon": False,
            "roles": roles, "role_scores": {}, "tags": sorted((d.get("effects") or {}).keys()),
            "tag_labels": sorted(set((d.get("labels") or {}).values())),
            "effects": d.get("effects") or {}, "coef": 3.0 if "nuke" in (d.get("effects") or {}) else 1.0,
            "power_base": power, "risk": 0.0,
            "risk_reasons": [], "power": power, "gw_role": role,
            "gw_icon": info.get("icon"), "gw_href": info.get("href"),
            "ub_name": "", "ub_desc": (d.get("skills") or {}).get("ub_desc", ""),
            "skill1": (d.get("skills") or {}).get("skill1", ""),
            "skill2": (d.get("skills") or {}).get("skill2", ""), "ue_skill": "",
            "intro": "", "guild": "",
        }

    new_chars = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        for c in ex.map(build_new, new_names):
            new_chars.append(c)
    chars.extend(new_chars)

    # icons
    os.makedirs(GW_ICON_DIR, exist_ok=True)
    icon_index = []
    jobs = []
    for c in chars:
        if c.get("gw_icon"):
            jobs.append((c["name"], c["gw_icon"]))

    def dl(job):
        name, url = job
        safe = re.sub(r"[\\/:*?\"<>|]", "_", name)
        path = os.path.join(GW_ICON_DIR, safe + ".png")
        if not os.path.exists(path):
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            try:
                with urllib.request.urlopen(req, timeout=25) as r:
                    data = r.read()
                if data[:8] == b"\x89PNG\r\n\x1a\n":
                    with open(path, "wb") as f:
                        f.write(data)
                else:
                    return None
            except Exception:  # noqa: BLE001
                return None
        return {"name": name, "file": safe + ".png", "source": "gw"}

    with ThreadPoolExecutor(max_workers=10) as ex:
        for r in ex.map(dl, jobs):
            if r:
                icon_index.append(r)

    json.dump(chars, open(CHARS, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    json.dump(icon_index, open(GW_INDEX, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("saved chars", len(chars), "icons", len(icon_index))


if __name__ == "__main__":
    main()
