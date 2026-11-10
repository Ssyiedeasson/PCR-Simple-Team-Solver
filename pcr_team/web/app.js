/* 兰德索尔配队工坊 — 前端逻辑 */
const $ = (s) => document.querySelector(s);
const $$ = (s) => Array.from(document.querySelectorAll(s));

const state = {
  characters: [],       // server roster
  nameSet: new Set(),
  aliasMap: new Map(),  // alias -> name
  plan: null,
  config: null,
  busy: false,
  recog: { img: null, cells: [], selected: new Map(), dataUrl: "" },
};

const ENEMY_EFFECTS = [
  ["物理攻击", "首领受物理攻击有效 / 物理伤害提升"],
  ["魔法攻击", "首领受魔法攻击有效 / 魔法伤害提升"],
  ["技能值下降", "削减首领技能值 / 延缓 UB"],
  ["物理破甲", "降低首领物理防御"],
  ["魔法破甲", "降低首领魔法防御"],
  ["控制", "眩晕 / 束缚 / 击退等生效"],
  ["持续伤害", "中毒 / 灼烧等持续伤害生效"],
  ["减疗", "降低首领回复量"],
  ["减攻", "降低首领攻击力"],
  ["减速", "降低首领行动速度"],
  ["驱散", "驱散首领增益"],
];
const selectedEffects = new Set();

/* ------------------------------------------------------------------ */
/* utils                                                               */
/* ------------------------------------------------------------------ */
function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}
let toastTimer;
function toast(msg, isError = false) {
  const el = $("#toast");
  el.textContent = msg;
  el.className = "toast show" + (isError ? " err" : "");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.className = "toast"; }, 2800);
}
const API_BASE = (window.PCR_API_BASE || "").replace(/\/$/, "");

async function api(path, opts = {}) {
  const res = await fetch(API_BASE + path, {
    headers: { "Content-Type": "application/json" },
    ...opts,
  });
  let data = null;
  try { data = await res.json(); } catch (e) { /* ignore */ }
  if (!res.ok) throw new Error((data && (data.error || data.detail)) || `HTTP ${res.status}`);
  return data;
}
function splitNames(text) {
  return text.split(/[\s,，、;；/|]+/).map((s) => s.trim()).filter(Boolean);
}
function clamp(n, lo, hi) { return Math.max(lo, Math.min(hi, n)); }

/* ------------------------------------------------------------------ */
/* init                                                                */
/* ------------------------------------------------------------------ */
async function init() {
  try {
    const [meta, chars, cfg] = await Promise.all([
      api("/api/meta"), api("/api/characters"), api("/api/config"),
    ]);
    state.characters = chars.characters || [];
    state.nameSet = new Set(state.characters.map((c) => c.name));
    state.characters.forEach((c) => {
      state.aliasMap.set(c.name, c.name);
      (c.aliases || []).forEach((a) => { if (a) state.aliasMap.set(a, c.name); });
    });
    $("#statChars").textContent = meta.characters ?? state.characters.length;
    $("#statChars").title = `共 ${meta.characters} 名（含 GameWith 补录 ${meta.gw_characters || 0} 名）；` +
      `另有 ${meta.unreleased || 0} 名未实装`;
    $("#statVersion").textContent = meta.version || "—";
    state.config = cfg;
    fillConfig(cfg);
    renderEffectChips();
    updateCount();
    renderChipsHint();
  } catch (e) {
    toast("初始化失败：" + e.message, true);
  }
}

/* ------------------------------------------------------------------ */
/* roster                                                              */
/* ------------------------------------------------------------------ */
function updateCount() {
  let raw = $("#chars").value;
  const protectedNames = [];
  state.characters.forEach((c) => {
    if (c.name.includes("、") && raw.includes(c.name)) {
      const token = `\u241f${protectedNames.length}\u241f`;
      raw = raw.split(c.name).join(token);
      protectedNames.push(c.name);
    }
  });
  let tokens = splitNames(raw).map((t) => {
    const m = t.match(/^\u241f(\d+)\u241f$/);
    return m ? protectedNames[+m[1]] : t;
  });
  const resolved = new Set();
  const unknown = [];
  tokens.forEach((t) => {
    const name = state.aliasMap.get(t) || (state.nameSet.has(t) ? t : null);
    if (name) resolved.add(name);
    else unknown.push(t);
  });
  $("#count").textContent = resolved.size;
  $("#unknownWrap").hidden = unknown.length === 0;
  $("#unknownCount").textContent = unknown.length;
  $("#unknownWrap").title = unknown.slice(0, 20).join("、");
}
let hintTimer;
function renderChipsHint() {
  clearTimeout(hintTimer);
  hintTimer = setTimeout(updateCount, 120);
}

const EXAMPLE = [
  "佩可莉姆", "宫子", "空花", "纯", "流夏", "望", "静流",
  "真琴", "克莉丝提娜", "香织", "镜华", "凯露（夏日）", "凯露（新年）",
  "似似花", "露娜", "优衣", "优衣（新年）", "美里", "千歌",
  "咲恋", "由加莉", "莫妮卡", "可可萝", "初音", "杏奈", "伊莉亚",
  "珠希", "惠理子", "姬塔", "真琴（夏日）", "静流（夏日）",
];

/* ------------------------------------------------------------------ */
/* planning                                                            */
/* ------------------------------------------------------------------ */
async function doPlan() {
  if (state.busy) return;
  const text = $("#chars").value.trim();
  if (!text) { toast("请先输入角色", true); $("#chars").focus(); return; }
  const teams = clamp(parseInt($("#teamCount").value || "0", 10), 0, 200);
  const mode = $("#modeSeg button.on").dataset.mode;
  const allowReuse = $("#reuse").checked;
  const sequence = $("#sequence").checked;
  const exclude = $("#exclude").value.trim();
  const includeUnreleased = $("#unreleased").checked;

  state.busy = true;
  $("#btnPlan").disabled = true;
  $("#btnPlan").innerHTML = '<span class="spinner"></span> 正在搜索…';
  $("#teamList").innerHTML = Array.from({ length: 2 }, () => '<div class="skeleton"></div>').join("");
  $("#resultSub").textContent = "束搜索进行中…";
  try {
    const plan = await api("/api/plan", {
      method: "POST",
      body: JSON.stringify({
        characters: text, teams: sequence ? 3 : teams, mode, allowReuse, exclude,
        includeUnreleased, sequence, enemyEffects: [...selectedEffects],
      }),
    });
    state.plan = plan;
    renderPlan(plan);
    $("#btnExport").disabled = false;
    $("#btnAnalyze").disabled = false;
    if (plan.unknown && plan.unknown.length) {
      toast(`已忽略 ${plan.unknown.length} 个未识别名称`, true);
    }
  } catch (e) {
    state.plan = null;
    $("#teamList").innerHTML = emptyState("配队失败", e.message);
    $("#resultSub").textContent = "出错了";
    $("#btnExport").disabled = true;
    $("#btnAnalyze").disabled = true;
    toast(e.message, true);
  } finally {
    state.busy = false;
    $("#btnPlan").disabled = false;
    $("#btnPlan").innerHTML = '<svg width="16" height="16" viewBox="0 0 24 24" fill="none"><path d="M13 2 4.5 13.5H11L9.5 22 19 10h-6.5L13 2Z" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/></svg> 开始配队';
  }
}

function emptyState(title, desc) {
  return `<div class="empty"><h3>${esc(title)}</h3><p>${esc(desc)}</p></div>`;
}

const POS_CLASS = { "前卫": "front", "中卫": "mid", "后卫": "back" };
const COV_LABEL = { tank: "承伤", dps: "输出", shred: "破甲", support: "循环", heal: "治疗", control: "控制", sustain: "续航" };

function renderPlan(plan) {
  const teams = plan.teams || [];
  if (!teams.length) {
    $("#teamList").innerHTML = emptyState("无法组队", "可用角色不足 5 人，或全部被排除。");
    return;
  }
  $("#resultSub").innerHTML = plan.sequence
    ? `${plan.mode_label}　·　三队连战　·　角色池 <b style="color:#f8e7bd">${plan.pool_size}</b> 人　·　` +
      `顺序总评 <b style="color:#f8e7bd">${plan.sequence_score}</b>`
    : `${plan.mode_label}　·　角色池 <b style="color:#f8e7bd">${plan.pool_size}</b> 人　·　` +
      `生成 <b style="color:#f8e7bd">${teams.length}</b> 队` +
      (plan.auto ? "（自动排满）" : "");
  $("#teamList").innerHTML = teams.map((t, i) => teamCard(t, i)).join("");
}

function teamCard(t, i) {
  const bd = t.breakdown || {};
  const cov = bd.coverage || {};
  const rankCls = i === 0 ? "" : i === 1 ? "r2" : i === 2 ? "r3" : "rx";
  const dmgCls = t.damage_type === "物理" ? "phys" : t.damage_type === "魔法" ? "magic" : "";
  const members = (t.members || []).map(memberHtml).join("");
  const bars = Object.keys(COV_LABEL).map((k) => {
    const v = Math.round((cov[k] ?? 0) * 100);
    const cls = v < 45 ? "low" : v < 75 ? "mid" : "";
    return `<div class="bar-row">
      <span>${COV_LABEL[k]}</span>
      <div class="bar-track"><div class="bar-fill ${cls}" style="width:${v}%"></div></div>
      <span class="num">${v}</span>
    </div>`;
  }).join("");

  const notes = [
    ...(t.strengths || []).map((n) => `<div class="note good"><span class="ic">✓</span><span>${esc(n)}</span></div>`),
    ...(t.notes || []).map((n) => `<div class="note warn"><span class="ic">!</span><span>${esc(n)}</span></div>`),
    ...(t.suggestions || []).map((n) => `<div class="note tip"><span class="ic">→</span><span>${esc(n)}</span></div>`),
  ].join("") || '<div class="note"><span class="ic">·</span><span>结构均衡，没有明显短板。</span></div>';

  return `<article class="team-card" style="animation-delay:${i * 60}ms">
    <div class="team-top">
      <div class="rank ${rankCls}">${t.rank}</div>
      <div class="team-title">
        <h3>${t.phase ? esc(t.phase) + " · " : ""}${esc(t.archetype)}</h3>
        <div class="team-sub">
          ${dmgCls ? `<span class="badge ${dmgCls}">${esc(t.damage_type)}</span>` : `<span class="badge">${esc(t.damage_type)}</span>`}
          ${t.enemy_fit ? `<span class="badge gold">对策 +${t.enemy_fit}</span>` : ""}
          <span>均战力 ${t.power}</span>
          <span>·</span>
          <span>拆分 ${bd.power}+${bd.composition}+${bd.synergy}+${bd.position}${bd.enemy > 0 ? "+" + bd.enemy : ""}${bd.penalty > 0 ? `−${bd.penalty}` : ""}</span>
          ${t.enemy_matched && t.enemy_matched.length ? `<span>· 对策 ${t.enemy_matched.map(esc).join(" / ")}</span>` : ""}
        </div>
      </div>
      <div class="score-wrap">
        <div class="score-ring" style="--p:${t.score}"><b>${t.score}<small>/100</small></b></div>
      </div>
    </div>
    <div class="members">${members}</div>
    <div class="team-body">
      <div>
        <div class="cov-title">阵容覆盖</div>
        <div class="bars">${bars}</div>
      </div>
      <div>
        <div class="note-title">评估与建议</div>
        <div class="notes">${notes}</div>
      </div>
    </div>
    <div class="team-foot">
      <button class="btn small ghost" data-copy="${i}">复制阵容</button>
      <button class="btn small ghost" data-detail="${i}">查看技能</button>
    </div>
  </article>`;
}

function memberHtml(m) {
  const cls = POS_CLASS[m.position] || "mid";
  const tags = (m.tags || []).slice(0, 3).map((x) => `<span>${esc(x)}</span>`).join("");
  const star = m.six_star ? '<span class="m-star">★6</span>' : "";
  return `<div class="member ${cls}" title="${esc(m.name)}｜${esc(m.position)}/${esc(m.attack)}｜${esc((m.roles || []).join("、"))}">
    <span class="pos-line"></span>${star}
    <div class="m-slot">${esc(m.slot)}</div>
    <div class="m-name">${esc(m.name)}</div>
    <div class="m-meta">
      <span>${esc(m.attack)}</span><span>·</span><span class="m-power">${m.power}</span>
    </div>
    <div class="m-tags">${tags}</div>
  </div>`;
}

/* ------------------------------------------------------------------ */
/* analysis                                                            */
/* ------------------------------------------------------------------ */
function currentConfigPatch() {
  const provider = $("#provider").value;
  const patch = { provider };
  if (provider === "openai") {
    patch.base_url = $("#baseUrl").value.trim();
    patch.model = $("#model").value.trim();
    const key = $("#apiKey").value.trim();
    if (key && !key.includes("*")) patch.api_key = key;
  }
  if (provider === "custom") {
    patch.custom = {
      url: $("#customUrl").value.trim(),
      body_template: $("#customBody").value.trim(),
      response_path: $("#customPath").value.trim() || "text",
    };
  }
  patch.system_prompt = $("#systemPrompt").value.trim();
  patch.extra_context = $("#extraContext").value.trim();
  return patch;
}

async function doAnalyze() {
  if (!state.plan) { toast("请先生成配队", true); return; }
  const btn = $("#btnAnalyze");
  btn.disabled = true;
  const old = btn.textContent;
  btn.textContent = "分析中…";
  $("#analysisSource").textContent = "";
  $("#analysisBody").innerHTML = '<div class="skeleton" style="height:120px"></div>';
  try {
    const res = await api("/api/analyze", {
      method: "POST",
      body: JSON.stringify({ plan: state.plan, config: currentConfigPatch() }),
    });
    const src = { openai: "外部 API", custom: "自定义 API", local: "本地规则", "local-fallback": "本地回退" }[res.source] || res.source;
    $("#analysisSource").textContent = `${src}${res.model ? " · " + res.model : ""}${res.elapsed ? " · " + res.elapsed + "s" : ""}`;
    $("#analysisBody").className = "md";
    $("#analysisBody").innerHTML = markdown(res.text || "");
    if (res.error) toast("API 调用失败，已回退本地分析", true);
    const panel = document.querySelector(".analysis");
    if (panel && panel.getBoundingClientRect().top > window.innerHeight * 0.6) {
      panel.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  } catch (e) {
    $("#analysisBody").className = "analysis-empty";
    $("#analysisBody").textContent = "分析失败：" + e.message;
    toast(e.message, true);
  } finally {
    btn.disabled = false;
    btn.textContent = old;
  }
}

/* tiny markdown renderer (safe: escape first) */
function markdown(md) {
  const lines = esc(md).split(/\r?\n/);
  let html = "", inList = null, para = [];
  const flushPara = () => {
    if (para.length) { html += `<p>${para.join("<br>")}</p>`; para = []; }
  };
  const flushList = () => {
    if (inList) { html += `</${inList}>`; inList = null; }
  };
  const inline = (s) => s
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*([^*]+)\*/g, "<em>$1</em>");

  for (const raw of lines) {
    const line = raw.trim();
    if (!line) { flushPara(); flushList(); continue; }
    if (/^---+$/.test(line)) { flushPara(); flushList(); html += "<hr>"; continue; }
    const h = line.match(/^(#{1,4})\s+(.*)$/);
    if (h) { flushPara(); flushList(); html += `<h${h[1].length}>${inline(h[2])}</h${h[1].length}>`; continue; }
    if (/^&gt;\s?/.test(line)) {
      flushPara(); flushList();
      html += `<blockquote>${inline(line.replace(/^&gt;\s?/, ""))}</blockquote>`;
      continue;
    }
    const ul = line.match(/^[-*+]\s+(.*)$/);
    if (ul) {
      flushPara();
      if (inList !== "ul") { flushList(); html += "<ul>"; inList = "ul"; }
      html += `<li>${inline(ul[1])}</li>`;
      continue;
    }
    const ol = line.match(/^\d+[.、]\s*(.*)$/);
    if (ol) {
      flushPara();
      if (inList !== "ol") { flushList(); html += "<ol>"; inList = "ol"; }
      html += `<li>${inline(ol[1])}</li>`;
      continue;
    }
    flushList();
    para.push(inline(line));
  }
  flushPara(); flushList();
  return html;
}

/* ------------------------------------------------------------------ */
/* enemy effects                                                       */
/* ------------------------------------------------------------------ */
function renderEffectChips() {
  const wrap = $("#effectChips");
  if (!wrap) return;
  wrap.innerHTML = ENEMY_EFFECTS.map(([label, hint]) =>
    `<button type="button" class="effect-chip" data-eff="${esc(label)}" title="${esc(hint)}">${esc(label)}</button>`
  ).join("");
  wrap.querySelectorAll(".effect-chip").forEach((b) => {
    b.onclick = () => {
      const v = b.dataset.eff;
      if (selectedEffects.has(v)) selectedEffects.delete(v); else selectedEffects.add(v);
      b.classList.toggle("on", selectedEffects.has(v));
      $("#effectHint").textContent = selectedEffects.size
        ? `已选：${[...selectedEffects].join("、")}（会提高对应阵容评分）`
        : "不选则按通用强度配队";
    };
  });
}

/* ------------------------------------------------------------------ */
/* screenshot recognition                                              */
/* ------------------------------------------------------------------ */
function openRecog() {
  $("#recogModal").hidden = false;
  document.body.style.overflow = "hidden";
}
function closeRecog() {
  $("#recogModal").hidden = true;
  document.body.style.overflow = "";
}
function resetRecog() {
  state.recog = { items: [], selected: new Map(), dataUrl: "" };
  $("#recogResults").innerHTML = "";
  $("#recogStatus").textContent = "";
  $("#recogHint").textContent = "低置信度的结果请手动确认";
  $("#recogAdd").disabled = true;
  const fi = $("#fileInput");
  if (fi) fi.value = "";
}

function fileToCanvas(file, maxSide = 1600) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const img = new Image();
      img.onload = () => {
        const scale = Math.min(1, maxSide / Math.max(img.width, img.height));
        const canvas = document.createElement("canvas");
        canvas.width = Math.round(img.width * scale);
        canvas.height = Math.round(img.height * scale);
        canvas.getContext("2d").drawImage(img, 0, 0, canvas.width, canvas.height);
        resolve(canvas);
      };
      img.onerror = reject;
      img.src = reader.result;
    };
    reader.onerror = reject;
    reader.readAsDataURL(file);
  });
}

async function handleFiles(files) {
  const list = [...files].filter((f) => f && f.type.startsWith("image/"));
  if (!list.length) { toast("请选择图片文件", true); return; }
  for (const file of list) {
    const item = { name: file.name, status: "reading", canvas: null, cells: [] };
    state.recog.items.push(item);
    renderRecogResults();
    try {
      item.canvas = await fileToCanvas(file);
      item.dataUrl = item.canvas.toDataURL("image/jpeg", 0.9);
      item.status = "recognizing";
      renderRecogResults();
      const res = await api("/api/recognize", {
        method: "POST",
        body: JSON.stringify({ image: item.dataUrl, topk: 3 }),
      });
      if (!res.ok) throw new Error(res.error || "识别失败");
      item.cells = res.cells || [];
      item.status = "done";
      item.library = res.library;
      const idx = state.recog.items.indexOf(item);
      item.cells.forEach((c, ci) => {
        if (c.margin >= 0.08 && c.candidates[0]) {
          state.recog.selected.set(`${idx}:${ci}`, c.candidates[0].name);
        }
      });
    } catch (e) {
      item.status = "error";
      item.error = e.message;
    }
    renderRecogResults();
  }
}

function cropCell(item, cell, size = 132) {
  const [x, y, w, h] = cell.bbox;
  const c = document.createElement("canvas");
  c.width = size; c.height = size;
  c.getContext("2d").drawImage(item.canvas, x, y, w, h, 0, 0, size, size);
  return c.toDataURL("image/jpeg", 0.85);
}

function renderRecogResults() {
  const items = state.recog.items;
  const done = items.filter((i) => i.status === "done").length;
  const totalCells = items.reduce((n, i) => n + (i.cells ? i.cells.length : 0), 0);
  $("#recogStatus").textContent = items.length
    ? `共 ${items.length} 张，已识别 ${done} 张，检测到 ${totalCells} 个头像`
    : "";
  const wrap = $("#recogResults");
  if (!items.length) { wrap.innerHTML = ""; $("#recogAdd").disabled = true; return; }
  wrap.innerHTML = items.map((item, ii) => {
    if (item.status === "reading") {
      return `<div class="recog-image-block"><div class="recog-image-head">读取中…</div></div>`;
    }
    if (item.status === "recognizing") {
      return `<div class="recog-image-block"><div class="recog-image-head"><img class="recog-thumb" src="${item.dataUrl}"><b>${esc(item.name)}</b><span class="conf-pill mid">识别中…</span></div></div>`;
    }
    if (item.status === "error") {
      return `<div class="recog-image-block"><div class="recog-image-head"><img class="recog-thumb" src="${item.dataUrl}"><b>${esc(item.name)}</b><span class="conf-pill low">失败：${esc(item.error)}</span></div></div>`;
    }
    const rows = (item.cells || []).map((c, ci) => {
      const key = `${ii}:${ci}`;
      const chosen = state.recog.selected.get(key);
      const conf = c.candidates[0] ? c.candidates[0].score : 0;
      const level = c.margin >= 0.08 ? "high" : c.margin >= 0.035 ? "mid" : "low";
      const label = level === "high" ? "较可信" : level === "mid" ? "待确认" : "低置信";
      const cands = c.candidates.map((cand) =>
        `<button type="button" class="recog-cand ${chosen === cand.name ? "on" : ""}"
          data-img="${ii}" data-cell="${ci}" data-name="${esc(cand.name)}">
          ${esc(cand.name)}<b>${Math.round(cand.score * 100)}</b></button>`).join("");
      return `<div class="recog-row ${chosen ? "" : "skipped"}" data-row="${ii}:${ci}">
        <img class="recog-crop" src="${cropCell(item, c)}" alt="">
        <div>
          <div class="recog-cands">${cands}</div>
          <div class="recog-manual-wrap">
            <select class="recog-manual" data-img="${ii}" data-cell="${ci}">
              <option value="">手动选择正确角色…</option>
              ${state.characters.map((ch) => `<option value="${esc(ch.name)}" ${chosen === ch.name ? "selected" : ""}>${esc(ch.name)}</option>`).join("")}
            </select>
            <label class="recog-remember"><input type="checkbox" data-remember="${ii}:${ci}" checked> 记住</label>
            <span class="recog-toggle" data-skip="${ii}:${ci}">不导入</span>
          </div>
        </div>
        <div class="recog-conf">
          <span class="conf-pill ${level}">${label}</span><br>
          ${Math.round(conf * 100)}% · Δ${Math.round(c.margin * 100)}
        </div>
      </div>`;
    }).join("");
    return `<div class="recog-image-block">
      <div class="recog-image-head"><img class="recog-thumb" src="${item.dataUrl}">
        <b>${esc(item.name)}</b><span>${(item.cells || []).length} 个头像</span></div>
      ${rows || '<div class="recog-status">没有检测到头像格。</div>'}
    </div>`;
  }).join("");
  $("#recogAdd").disabled = state.recog.selected.size === 0;
  const low = items.reduce((n, i) => n + (i.cells || []).filter((c) => c.margin < 0.035).length, 0);
  $("#recogHint").textContent = low
    ? `${low} 个结果置信度较低，请手动选择正确角色`
    : "确认候选后即可加入角色池";
}

async function addRecognized() {
  if (!state.recog.selected.size) return;
  const btn = $("#recogAdd");
  btn.disabled = true;
  const entries = [...state.recog.selected.entries()];
  const names = [...new Set(entries.map(([, n]) => n))];
  let learned = 0;
  for (const [key, name] of entries) {
    const [ii, ci] = key.split(":").map(Number);
    const remember = document.querySelector(`[data-remember="${key}"]`);
    const item = state.recog.items[ii];
    if (remember && remember.checked && item && item.cells[ci]) {
      try {
        await api("/api/recognize/learn", {
          method: "POST",
          body: JSON.stringify({ name, image: cropCell(item, item.cells[ci], 200) }),
        });
        learned++;
      } catch (e) { /* best effort */ }
    }
  }
  const cur = $("#chars").value.trim();
  $("#chars").value = (cur ? cur + " " : "") + names.join(" ");
  updateCount();
  toast(learned ? `已加入 ${names.length} 名角色，并记住 ${learned} 个头像` : `已加入 ${names.length} 名角色`);
  closeRecog();
}

/* ------------------------------------------------------------------ */
/* feedback                                                            */
/* ------------------------------------------------------------------ */
function feedbackContext() {
  let plan = null;
  if (state.plan && state.plan.teams && state.plan.teams[0]) {
    const t = state.plan.teams[0];
    plan = { score: t.score, archetype: t.archetype,
             members: (t.members || []).map((m) => m.name),
             enemy_effects: state.plan.enemy_effects || [] };
  }
  let recognition = null;
  if (state.recog.cells && state.recog.cells.length) {
    recognition = {
      cells: state.recog.cells.length,
      picked: [...state.recog.selected.values()],
      top1: state.recog.cells.map((c) => c.candidates[0] && c.candidates[0].name).filter(Boolean),
    };
  }
  const modeBtn = document.querySelector("#modeSeg button.on");
  return {
    roster: $("#chars").value.trim(),
    mode: modeBtn ? modeBtn.dataset.mode : "pve",
    plan,
    recognition,
    page: location.href,
    ua: navigator.userAgent,
  };
}

async function submitFeedback() {
  const text = $("#fbText").value.trim();
  if (text.length < 4) { toast("请至少写 4 个字描述问题", true); $("#fbText").focus(); return; }
  const btn = $("#fbSubmit");
  btn.disabled = true;
  const old = btn.textContent;
  btn.textContent = "提交中…";
  try {
    const res = await api("/api/feedback", {
      method: "POST",
      body: JSON.stringify({
        text,
        category: $("#fbCategory").value,
        contact: $("#fbContact").value.trim(),
        context: feedbackContext(),
      }),
    });
    const reply = res.reply || {};
    const srcLabel = { openai: "外部 API", custom: "自定义 API", local: "本地记录", "local-fallback": "本地回退" }[reply.source] || reply.source;
    $("#fbReply").hidden = false;
    $("#fbReply").innerHTML = `<div class="recog-status">反馈编号 ${esc(res.entry.id)} · 来源 ${esc(srcLabel)}${reply.model ? " · " + esc(reply.model) : ""}</div>` + markdown(reply.text || "");
    $("#fbText").value = "";
    toast(reply.source === "local" ? "已记录（本地模式）" : "已提交并完成分析");
  } catch (e) {
    toast(e.message, true);
  } finally {
    btn.disabled = false;
    btn.textContent = old;
  }
}

async function loadFeedback() {
  const box = $("#fbList");
  try {
    const res = await api("/api/feedback?limit=20");
    const items = res.items || [];
    box.hidden = false;
    if (!items.length) { box.innerHTML = '<div class="fb-item">还没有反馈记录。</div>'; return; }
    box.innerHTML = items.slice().reverse().map((it) => `
      <div class="fb-item">
        <div class="fb-meta">${esc(it.created)} · ${esc(it.category)} · ${esc(it.reply_source || "未分析")} · ${esc(it.id)}</div>
        <b>${esc(it.text.slice(0, 120))}</b>
        ${it.reply ? `<div style="margin-top:4px;white-space:pre-wrap">${esc(it.reply.slice(0, 180))}…</div>` : ""}
      </div>`).join("");
  } catch (e) {
    toast("读取反馈失败：" + e.message, true);
  }
}

/* ------------------------------------------------------------------ */
/* config                                                              */
/* ------------------------------------------------------------------ */
function fillConfig(cfg) {
  $("#provider").value = cfg.provider || "local";
  $("#baseUrl").value = cfg.base_url || "";
  $("#model").value = cfg.model || "";
  $("#apiKey").value = "";
  $("#apiKey").placeholder = cfg.has_key
    ? "已保存：" + (cfg.api_key || "****") + "（留空则保持不变）"
    : "sk-... （仅保存在本地 config.json）";
  $("#systemPrompt").value = cfg.system_prompt || "";
  $("#extraContext").value = cfg.extra_context || "";
  const custom = cfg.custom || {};
  $("#customUrl").value = custom.url || "";
  $("#customBody").value = custom.body_template || "";
  $("#customPath").value = custom.response_path || "text";
  $("#cfgDot").className = "dot" + (cfg.has_key || cfg.provider === "local" ? " ok" : "");
  $("#cfgText").textContent = cfg.provider === "local"
    ? "当前使用本地规则分析"
    : cfg.has_key ? "API Key 已配置" : "尚未配置 API Key";
  $("#keyState").textContent = cfg.has_key ? "已配置" : "";
  syncProvider();
}

function syncProvider() {
  const p = $("#provider").value;
  $("#openaiFields").hidden = p !== "openai";
  $("#customFields").hidden = p !== "custom";
}

async function saveConfig(clearKey = false) {
  const body = {
    ...currentConfigPatch(),
    clear_key: clearKey,
  };
  if (clearKey) body.api_key = "";
  try {
    const res = await api("/api/config", { method: "POST", body: JSON.stringify(body) });
    state.config = { ...(state.config || {}), ...body, has_key: res.has_key };
    if (clearKey) { $("#apiKey").value = ""; $("#apiKey").placeholder = "sk-... （仅保存在本地 config.json）"; }
    $("#cfgDot").className = "dot" + (res.has_key || body.provider === "local" ? " ok" : "");
    $("#cfgText").textContent = res.has_key ? "配置已保存" : "配置已保存（无 Key）";
    $("#keyState").textContent = res.has_key ? "已配置" : "";
    toast("配置已保存");
  } catch (e) {
    toast("保存失败：" + e.message, true);
  }
}

/* ------------------------------------------------------------------ */
/* events                                                              */
/* ------------------------------------------------------------------ */
function bind() {
  $("#chars").addEventListener("input", renderChipsHint);

  $("#btnExample").onclick = () => {
    $("#chars").value = EXAMPLE.join(" ");
    updateCount();
  };
  $("#btnAll").onclick = () => {
    $("#chars").value = state.characters.map((c) => c.name).join(" ");
    updateCount();
  };
  $("#btnClear").onclick = () => {
    $("#chars").value = "";
    updateCount();
    $("#chars").focus();
  };
  $("#btnPick").onclick = () => {
    const pool = state.characters.slice();
    for (let i = pool.length - 1; i > 0; i--) {
      const j = Math.floor(Math.random() * (i + 1));
      [pool[i], pool[j]] = [pool[j], pool[i]];
    }
    $("#chars").value = pool.slice(0, 20).map((c) => c.name).join(" ");
    updateCount();
  };

  $$("#modeSeg button").forEach((b) => {
    b.onclick = () => {
      $$("#modeSeg button").forEach((x) => x.classList.remove("on"));
      b.classList.add("on");
    };
  });

  const teamsInput = $("#teamCount");
  const syncHint = () => {
    const v = parseInt(teamsInput.value || "0", 10);
    $("#teamHint").textContent = !v || v <= 0 ? "自动排满" : `${v} 队`;
  };
  teamsInput.addEventListener("input", syncHint);
  $("#teamMinus").onclick = () => {
    teamsInput.value = Math.max(0, (parseInt(teamsInput.value || "0", 10) || 0) - 1);
    syncHint();
  };
  $("#teamPlus").onclick = () => {
    teamsInput.value = Math.min(200, (parseInt(teamsInput.value || "0", 10) || 0) + 1);
    syncHint();
  };
  syncHint();

  $("#btnRecognize").onclick = () => { resetRecog(); openRecog(); };
  $("#recogClose").onclick = closeRecog;
  $("#recogReset").onclick = resetRecog;
  $("#recogAdd").onclick = addRecognized;
  $("#recogModal").addEventListener("click", (e) => { if (e.target === $("#recogModal")) closeRecog(); });
  const dz = $("#dropzone");
  dz.onclick = () => $("#fileInput").click();
  $("#fileInput").onchange = (e) => { if (e.target.files.length) handleFiles(e.target.files); };
  ["dragenter", "dragover"].forEach((ev) => dz.addEventListener(ev, (e) => {
    e.preventDefault(); dz.classList.add("drag");
  }));
  ["dragleave", "drop"].forEach((ev) => dz.addEventListener(ev, (e) => {
    e.preventDefault(); dz.classList.remove("drag");
  }));
  dz.addEventListener("drop", (e) => {
    const files = e.dataTransfer.files;
    if (files && files.length) handleFiles(files);
  });
  $("#recogResults").addEventListener("click", (e) => {
    const cand = e.target.closest(".recog-cand");
    if (cand) {
      const key = `${cand.dataset.img}:${cand.dataset.cell}`;
      const row = cand.closest(".recog-row");
      row.querySelectorAll(".recog-cand").forEach((x) => x.classList.remove("on"));
      cand.classList.add("on");
      row.classList.remove("skipped");
      state.recog.selected.set(key, cand.dataset.name);
      const sel = row.querySelector(".recog-manual");
      if (sel) sel.value = cand.dataset.name;
      $("#recogAdd").disabled = state.recog.selected.size === 0;
      return;
    }
    const skip = e.target.closest("[data-skip]");
    if (skip) {
      const key = skip.dataset.skip;
      state.recog.selected.delete(key);
      const row = skip.closest(".recog-row");
      row.classList.add("skipped");
      row.querySelectorAll(".recog-cand").forEach((x) => x.classList.remove("on"));
      const sel = row.querySelector(".recog-manual");
      if (sel) sel.value = "";
      $("#recogAdd").disabled = state.recog.selected.size === 0;
    }
  });
  $("#recogResults").addEventListener("change", (e) => {
    const sel = e.target.closest(".recog-manual");
    if (!sel) return;
    const key = `${sel.dataset.img}:${sel.dataset.cell}`;
    const row = sel.closest(".recog-row");
    if (sel.value) {
      state.recog.selected.set(key, sel.value);
      row.classList.remove("skipped");
      row.querySelectorAll(".recog-cand").forEach((x) => {
        x.classList.toggle("on", x.dataset.name === sel.value);
      });
    } else {
      state.recog.selected.delete(key);
      row.querySelectorAll(".recog-cand").forEach((x) => x.classList.remove("on"));
    }
    $("#recogAdd").disabled = state.recog.selected.size === 0;
  });

  $("#fbSubmit").onclick = submitFeedback;
  $("#fbRecent").onclick = loadFeedback;

  $("#btnPlan").onclick = doPlan;
  $("#btnAnalyze").onclick = doAnalyze;
  $("#btnSave").onclick = () => saveConfig(false);
  $("#btnReset").onclick = () => saveConfig(true);
  $("#provider").onchange = syncProvider;

  $("#btnExport").onclick = () => {
    if (!state.plan) return;
    const blob = new Blob([JSON.stringify(state.plan, null, 2)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "pcr-teams-" + new Date().toISOString().slice(0, 10) + ".json";
    a.click();
    URL.revokeObjectURL(a.href);
  };

  $("#teamList").addEventListener("click", (e) => {
    const copy = e.target.closest("[data-copy]");
    if (copy && state.plan) {
      const t = state.plan.teams[+copy.dataset.copy];
      const text = `【${t.archetype}】${t.score}分\n` +
        t.members.map((m) => `${m.slot}：${m.name}（${m.position}/${m.attack}）`).join("\n");
      (navigator.clipboard ? navigator.clipboard.writeText(text) : Promise.reject())
        .then(() => toast("阵容已复制"))
        .catch(() => { prompt("复制阵容：", text); });
    }
    const detail = e.target.closest("[data-detail]");
    if (detail && state.plan) {
      const t = state.plan.teams[+detail.dataset.detail];
      $("#analysisBody").className = "md";
      $("#analysisSource").textContent = "角色技能速览";
      $("#analysisBody").innerHTML = t.members.map((m) =>
        `<h3>${esc(m.name)}　<span class="badge">${esc(m.slot)}</span></h3>` +
        `<p><strong>${esc(m.position)} · ${esc(m.attack)}</strong>　标签：${esc((m.tags || []).join(" / ") || "—")}</p>` +
        `<blockquote>${esc(m.ub || "暂无技能描述")}</blockquote>`
      ).join("");
    }
  });

  document.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === "Enter") doPlan();
  });
}

init().then(bind);
