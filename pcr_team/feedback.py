# -*- coding: utf-8 -*-
"""In-app bug / feedback collection with optional instant AI triage."""
from __future__ import annotations

import json
import os
import re
import time
import uuid

os.environ.setdefault("TZ", "Asia/Shanghai")
try:
    time.tzset()
except AttributeError:
    pass
from typing import Dict, List, Optional

from .analyzer import load_config, raw_chat

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
FEEDBACK_FILE = os.environ.get("PCR_FEEDBACK") or os.path.join(DATA, "feedback.json")

CATEGORIES = ["配队算法", "截图识别", "角色数据", "界面/交互", "性能/报错", "其他"]

TRIAGE_SYSTEM = (
    "你是《公主连结Re:Dive》配队工具的缺陷分诊助手。根据用户反馈与附带的运行上下文，"
    "判断问题类型、复现条件与可能原因，给出可执行的临时规避方案和修复建议。"
    "不虚构用户未提供的信息；无法判断时明确说明需要哪些补充信息。输出简洁的中文 Markdown。"
)


def _path() -> str:
    return os.environ.get("PCR_FEEDBACK") or FEEDBACK_FILE


def load_all() -> List[dict]:
    path = _path()
    if not os.path.exists(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:  # noqa: BLE001
        return []


def save_all(items: List[dict]) -> None:
    path = _path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(items[-500:], f, ensure_ascii=False, indent=1)


def add_feedback(text: str, category: str = "其他", contact: str = "",
                 context: Optional[dict] = None) -> dict:
    text = (text or "").strip()
    if len(text) < 4:
        raise ValueError("请至少写 4 个字描述问题")
    if len(text) > 3000:
        text = text[:3000]
    entry = {
        "id": time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4],
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
        "category": category if category in CATEGORIES else "其他",
        "text": text,
        "contact": (contact or "").strip()[:120],
        "context": context or {},
        "status": "open",
    }
    items = load_all()
    items.append(entry)
    save_all(items)
    return entry


def build_triage_prompt(entry: dict) -> str:
    ctx = entry.get("context") or {}
    lines = [
        f"用户反馈（分类：{entry['category']}）：",
        entry["text"],
        "",
        "运行上下文：",
        f"- 角色池输入：{ctx.get('roster') or '（空）'}",
        f"- 当前模式：{ctx.get('mode', '未知')}",
        f"- 最近一次配队：{json.dumps(ctx.get('plan'), ensure_ascii=False) if ctx.get('plan') else '无'}",
        f"- 识图结果：{json.dumps(ctx.get('recognition'), ensure_ascii=False) if ctx.get('recognition') else '无'}",
        f"- 页面：{ctx.get('page', '')}",
        f"- 浏览器：{ctx.get('ua', '')}",
        "",
        "请输出：1) 问题归类与严重度；2) 复现条件；3) 最可能的原因；4) 临时规避方法；"
        "5) 建议的修复方向；6) 如需补充信息，请列出。",
    ]
    return "\n".join(lines)


def local_triage(entry: dict) -> str:
    text = entry["text"]
    cat = entry["category"]
    hints = []
    if re.search(r"识别|图片|截图|头像|识图", text) or cat == "截图识别":
        hints = ["先确认截图中头像格完整、无遮挡；",
                 "低置信结果请用下拉手动校正，并勾选「记住此头像」；",
                 "若为新角色/异画，把对应图标补进 data/icons 或使用记忆功能。"]
    elif re.search(r"队伍|配队|阵容|评分|不出现|重复", text) or cat == "配队算法":
        hints = ["确认角色名与输入分隔符（双人卡如「秋乃、咲恋」会作为一个整体）；",
                 "检查是否开启了「包含未实装角色」或「允许跨队重复」；",
                 "把角色池与期望阵容一起贴出，便于复现评分差异。"]
    elif re.search(r"角色|数据|技能|标签|定位", text) or cat == "角色数据":
        hints = ["先跑 python3 scripts/audit_dataset.py --refresh 对照 Wiki；",
                 "技能标签由文本规则抽取，可在 meta.json / build_dataset.py 中修正；",
                 "附上角色名与错误字段，便于定位到具体页面。"]
    elif re.search(r"慢|卡|超时|性能|内存", text) or cat == "性能/报错":
        hints = ["记录角色数量、队伍数与是否开启识图；",
                 "全角色多队耗时随 beam 与锚点搜索增长，可先用 1–2 队验证；",
                 "附上浏览器控制台报错或服务端日志片段。"]
    else:
        hints = ["附上操作步骤、截图与浏览器版本；",
                 "如果是配队结果异常，请同时提供角色池与期望结果；",
                 "配置 API 后，反馈会连同上下文一起做即时分诊。"]
    lines = [f"> 本地记录成功（编号 `{entry['id']}`）。当前未配置 API，以下为本地排查清单。", "",
             "**建议排查**："]
    lines += [f"- {h}" for h in hints]
    lines += ["", "配置「API 注入」后重新提交，可获得针对上下文的即时分析与修复建议。"]
    return "\n".join(lines)


def triage(entry: dict, cfg: Optional[dict] = None) -> dict:
    cfg = cfg or load_config()
    provider = (cfg.get("provider") or "local").lower()
    if provider == "local":
        return {"source": "local", "text": local_triage(entry), "model": ""}
    prompt = build_triage_prompt(entry)
    try:
        res = raw_chat(cfg, TRIAGE_SYSTEM, prompt)
        return {"source": provider, "text": res["text"], "model": res.get("model", "")}
    except Exception as e:  # noqa: BLE001
        return {"source": "local-fallback",
                "text": local_triage(entry) + f"\n\n---\n> API 调用失败，已回退本地清单：{e}",
                "model": "", "error": str(e)}


def handle(text: str, category: str, contact: str, context: dict,
           cfg: Optional[dict] = None) -> dict:
    entry = add_feedback(text, category, contact, context)
    out = triage(entry, cfg)
    # persist the reply for later review
    items = load_all()
    for it in items:
        if it["id"] == entry["id"]:
            it["reply"] = out["text"][:4000]
            it["reply_source"] = out["source"]
    save_all(items)
    return {"ok": True, "entry": entry, "reply": out}
