# -*- coding: utf-8 -*-
"""Analysis layer with an API injection block.

Three modes:
  * openai  - any OpenAI-compatible /chat/completions endpoint (DeepSeek, vLLM,
              Ollama, OneAPI, ...), key from config or environment variable.
  * custom  - fully user-defined HTTP POST with a body template and a JSON path
              pointing at the answer, for self-hosted analysers.
  * local   - deterministic rule-based write-up, always available offline.
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Dict, List, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CONFIG = os.path.join(HERE, "..", "config.json")

DEFAULT_SYSTEM = (
    "你是一位《公主连结Re:Dive》资深配队顾问，精通公会战（PVE）与竞技场（PVP）环境，"
    "熟悉物理/魔法破甲、TP 循环、站位与承伤机制。你只会依据用户提供的角色数据与评分作答，"
    "不虚构技能或数值；不确定时明确说明。输出简洁、可执行的中文 Markdown。"
)

DEFAULT_CONFIG_DATA = {
    "provider": "local",
    "base_url": "https://api.deepseek.com/v1",
    "api_key": "",
    "api_key_env": "PCR_LLM_API_KEY",
    "model": "deepseek-chat",
    "temperature": 0.4,
    "max_tokens": 1500,
    "timeout": 90,
    "system_prompt": DEFAULT_SYSTEM,
    "extra_context": "",
    "custom": {
        "url": "",
        "headers": {"Content-Type": "application/json"},
        "body_template": "{\"prompt\": {{prompt_json}}, \"system\": {{system_json}}}",
        "response_path": "text",
    },
}


def config_path(path: Optional[str] = None) -> str:
    return path or os.environ.get("PCR_CONFIG") or DEFAULT_CONFIG


def load_config(path: Optional[str] = None) -> dict:
    path = config_path(path)
    cfg = json.loads(json.dumps(DEFAULT_CONFIG_DATA))
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                user = json.load(f)
            _deep_update(cfg, user)
        except Exception:
            pass
    env_key = os.environ.get(cfg.get("api_key_env") or "PCR_LLM_API_KEY", "")
    if env_key and not cfg.get("api_key"):
        cfg["api_key"] = env_key
    for env, key in (("PCR_PROVIDER", "provider"), ("PCR_BASE_URL", "base_url"),
                     ("PCR_MODEL", "model"), ("PCR_TEMPERATURE", "temperature")):
        val = os.environ.get(env)
        if val:
            cfg[key] = float(val) if key == "temperature" else val
    return cfg


def save_config(cfg: dict, path: Optional[str] = None) -> None:
    path = config_path(path)
    safe = json.loads(json.dumps(cfg))
    with open(path, "w", encoding="utf-8") as f:
        json.dump(safe, f, ensure_ascii=False, indent=2)


def _deep_update(base: dict, patch: dict) -> None:
    for k, v in patch.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_update(base[k], v)
        else:
            base[k] = v


def mask_key(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 8:
        return "*" * len(key)
    return key[:4] + "*" * (len(key) - 8) + key[-4:]


# --------------------------------------------------------------------------- #
# prompt building
# --------------------------------------------------------------------------- #
def team_to_prompt(team: dict, index: int) -> str:
    lines = [f"### 队伍 {index}｜{team.get('archetype','')}｜评分 {team.get('score',0)}"]
    bd = team.get("breakdown", {})
    lines.append(f"- 伤害类型：{team.get('damage_type','')}；均战力：{team.get('power',0)}")
    lines.append("- 阵容：" + "、".join(
        f"{m['name']}（{m['slot']}，{m['position']}/{m['attack']}，标签：{'/'.join(m.get('tags') or [])}）"
        for m in team.get("members", [])))
    cov = bd.get("coverage", {})
    if cov:
        lines.append("- 覆盖度：" + "、".join(f"{k}={v}" for k, v in cov.items()))
    if bd.get("notes"):
        lines.append("- 模型提示：" + "；".join(bd["notes"]))
    if bd.get("suggestions"):
        lines.append("- 模型建议：" + "；".join(bd["suggestions"]))
    return "\n".join(lines)


def build_prompt(payload: dict) -> str:
    teams = payload.get("teams", [])
    unused = payload.get("unused", []) or []
    extra = payload.get("extra_context", "") or ""
    mode = payload.get("mode_label", payload.get("mode", "PVE"))
    parts = [
        f"请分析以下《公主连结Re:Dive》配队方案（模式：{mode}）。",
        f"角色池规模：{payload.get('pool_size', '未知')} 人。",
        "\n".join(team_to_prompt(t, i + 1) for i, t in enumerate(teams)),
    ]
    enemy_effects = payload.get("enemy_effects") or []
    if enemy_effects:
        parts.append("对首领的有效效果（编队时必须优先考虑）：" + "、".join(enemy_effects))
    if unused:
        parts.append("未使用角色（可用于替换）：" + "、".join(unused[:60]))
    if extra:
        parts.append("用户补充信息（版本/练度/BOSS 机制等，需优先考虑）：\n" + extra)
    parts.append(
        "请按以下结构输出 Markdown：\n"
        "1. **一句话结论**（这套方案能不能打、适合什么场景）\n"
        "2. **逐队点评**（定位、赢点、明显短板）\n"
        "3. **对首领效果匹配**（逐项说明队伍如何利用这些有效效果）\n"
        "4. **替换建议**（从可用角色中给出更优替换及理由）\n"
        "5. **操作要点**（UB 顺序、破甲覆盖、TP 循环）\n"
        "6. **培养优先级**（先练谁、为什么）\n"
        "如果数据不足以判断，请直接说明缺少什么信息。"
    )
    return "\n\n".join(parts)


# --------------------------------------------------------------------------- #
# API calls
# --------------------------------------------------------------------------- #
def _post_json(url: str, body: dict, headers: dict, timeout: int) -> dict:
    req = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"), method="POST",
        headers={"User-Agent": "pcr-team-builder/1.0", **headers})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def call_openai(cfg: dict, prompt: str) -> dict:
    key = cfg.get("api_key") or ""
    if not key:
        raise RuntimeError("未配置 API Key（config.json 的 api_key 或环境变量 "
                           + (cfg.get("api_key_env") or "PCR_LLM_API_KEY") + "）")
    base = (cfg.get("base_url") or "").rstrip("/")
    url = base + "/chat/completions"
    body = {
        "model": cfg.get("model") or "deepseek-chat",
        "messages": [
            {"role": "system", "content": cfg.get("system_prompt") or DEFAULT_SYSTEM},
            {"role": "user", "content": prompt},
        ],
        "temperature": float(cfg.get("temperature", 0.4)),
        "max_tokens": int(cfg.get("max_tokens", 1500)),
    }
    data = _post_json(url, body, {"Authorization": "Bearer " + key,
                                  "Content-Type": "application/json"},
                      int(cfg.get("timeout", 90)))
    text = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
    if not text:
        raise RuntimeError("API 返回为空：" + json.dumps(data)[:300])
    return {"text": text, "usage": data.get("usage", {}),
            "model": data.get("model") or body["model"]}


def raw_chat(cfg: dict, system: str, prompt: str) -> dict:
    """Generic chat call used by non-team features (e.g. bug triage)."""
    provider = (cfg.get("provider") or "local").lower()
    if provider in ("openai", "deepseek", "openai-compatible", "ollama"):
        key = cfg.get("api_key") or ""
        if not key:
            raise RuntimeError("未配置 API Key")
        base = (cfg.get("base_url") or "").rstrip("/")
        body = {
            "model": cfg.get("model") or "deepseek-chat",
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": prompt}],
            "temperature": float(cfg.get("temperature", 0.3)),
            "max_tokens": int(cfg.get("max_tokens", 1200)),
        }
        data = _post_json(base + "/chat/completions", body,
                          {"Authorization": "Bearer " + key,
                           "Content-Type": "application/json"},
                          int(cfg.get("timeout", 90)))
        text = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
        if not text:
            raise RuntimeError("API 返回为空")
        return {"text": text, "model": data.get("model") or body["model"]}
    if provider == "custom":
        res = call_custom(cfg, prompt)
        return {"text": res["text"], "model": "custom"}
    raise RuntimeError("当前为本地模式")


def call_custom(cfg: dict, prompt: str) -> dict:
    custom = cfg.get("custom") or {}
    url = custom.get("url")
    if not url:
        raise RuntimeError("custom 模式需要在 custom.url 中填写分析服务地址")
    tpl = custom.get("body_template") or "{\"prompt\": {{prompt_json}}}"
    body_text = (tpl.replace("{{prompt_json}}", json.dumps(prompt, ensure_ascii=False))
                    .replace("{{prompt}}", json.dumps(prompt, ensure_ascii=False))
                    .replace("{{system_json}}", json.dumps(cfg.get("system_prompt") or DEFAULT_SYSTEM,
                                                          ensure_ascii=False)))
    try:
        body = json.loads(body_text)
    except json.JSONDecodeError:
        body = {"prompt": prompt}
    data = _post_json(url, body, custom.get("headers") or {"Content-Type": "application/json"},
                      int(cfg.get("timeout", 90)))
    path = custom.get("response_path") or "text"
    cur = data
    for part in str(path).split("."):
        if isinstance(cur, list):
            cur = cur[int(part)]
        else:
            cur = cur.get(part, "")
    if not isinstance(cur, str) or not cur.strip():
        raise RuntimeError("custom API 响应中未找到文本，检查 response_path")
    return {"text": cur, "usage": {}, "model": "custom"}


# --------------------------------------------------------------------------- #
# local rule-based analyser (always available)
# --------------------------------------------------------------------------- #
def local_analysis(payload: dict) -> str:
    teams = payload.get("teams", [])
    unused = payload.get("unused", []) or []
    mode = payload.get("mode_label", "PVE")
    if not teams:
        return "没有可分析的队伍。"
    out = [f"> 本地规则分析（未调用外部 API，模式：{mode}）。配置 API 后可获得更细致的点评。\n"]
    for i, t in enumerate(teams):
        bd = t.get("breakdown", {})
        cov = bd.get("coverage", {})
        members = t.get("members", [])
        out.append(f"## 队伍 {i+1}：{t.get('archetype','')}（{t.get('score',0)} 分）\n")
        out.append("**定位**：" + "、".join(f"{m['name']}·{m['slot']}" for m in members))
        out.append(f"\n**伤害构成**：{t.get('damage_type','')}队，平均战力 {t.get('power',0)}。")
        weak = [k for k, v in cov.items() if v < 0.6]
        strong = [k for k, v in cov.items() if v >= 0.9]
        label = {"tank": "承伤", "dps": "输出", "shred": "破甲", "support": "TP/增益",
                 "heal": "治疗", "control": "控制", "sustain": "续航"}
        if strong:
            out.append("\n**优势**：" + "、".join(label.get(k, k) for k in strong) + "覆盖到位。")
        if weak:
            out.append("\n**短板**：" + "、".join(label.get(k, k) for k in weak) + "不足。")
        for n in bd.get("notes", []):
            out.append(f"\n- ⚠️ {n}")
        for s in bd.get("suggestions", []):
            out.append(f"\n- 💡 {s}")
        if unused:
            out.append("\n**未上场角色**：" + "、".join(unused[:8]))
        out.append("")
    out.append("## 总体建议\n")
    out.append("- 优先保证「主坦 + 同属性破甲 + 两名输出 + 功能位」的骨架，再考虑堆叠强度。")
    out.append("- 多队规划时，把 TP 回复与群奶拆开分配，避免二队循环断档。")
    out.append("- 若面对高防御 BOSS，破甲覆盖率比单纯堆攻击力更重要。")
    return "\n".join(out)


# --------------------------------------------------------------------------- #
# public entry
# --------------------------------------------------------------------------- #
def analyze(payload: dict, cfg: Optional[dict] = None) -> dict:
    """Run the configured analyser, falling back to local rules."""
    cfg = cfg or load_config()
    prompt = build_prompt(payload)
    provider = (cfg.get("provider") or "local").lower()
    started = time.time()
    if provider in ("openai", "deepseek", "openai-compatible", "ollama"):
        try:
            res = call_openai(cfg, prompt)
            return {"ok": True, "source": provider, "provider": provider,
                    "text": res["text"], "usage": res.get("usage", {}),
                    "model": res.get("model", cfg.get("model")),
                    "elapsed": round(time.time() - started, 2), "prompt": prompt}
        except Exception as e:  # noqa: BLE001
            return {"ok": True, "source": "local-fallback", "provider": provider,
                    "text": local_analysis(payload) + f"\n\n---\n> API 调用失败，已回退本地分析：{e}",
                    "error": str(e), "elapsed": round(time.time() - started, 2), "prompt": prompt}
    if provider == "custom":
        try:
            res = call_custom(cfg, prompt)
            return {"ok": True, "source": "custom", "provider": "custom",
                    "text": res["text"], "usage": {}, "model": "custom",
                    "elapsed": round(time.time() - started, 2), "prompt": prompt}
        except Exception as e:  # noqa: BLE001
            return {"ok": True, "source": "local-fallback", "provider": "custom",
                    "text": local_analysis(payload) + f"\n\n---\n> API 调用失败，已回退本地分析：{e}",
                    "error": str(e), "elapsed": round(time.time() - started, 2), "prompt": prompt}
    return {"ok": True, "source": "local", "provider": "local",
            "text": local_analysis(payload), "usage": {},
            "elapsed": round(time.time() - started, 2), "prompt": prompt}
