/** wizard.js —— 软向导的唯一前端逻辑。
 *
 * 原则（与后端 app.py 对应）：
 *  - 门控判据全部来自服务端（info.gates），前端只渲染 ok/reason，不自算；
 *  - 长操作一律 POST /api/case/<name>/run 进队列，前端只轮询 /api/task；
 *  - pick 坐标契约见 pick.js 头注：提交的永远是 0..1 分数。
 */
"use strict";

const CASE = document.body.dataset.name;
const FLOW = document.body.dataset.flow;
const STEPS = FLOW === "patch"
  ? [["pick", "选区"], ["extract", "抽取"], ["generate", "生成"], ["merge", "合并"], ["compare", "对比"]]
  : [["pad", "扩充画面"], ["generate", "生成"], ["recompose", "裁回原尺寸"], ["compare", "对比"]];

let info = null;            // 最近一次 /api/case 的应答
let curStep = null;         // 当前显示的步骤 id
let wasRunning = false;     // 任务从忙到闲时刷新一次 info
let vpFrame = null;         // 当前逐帧查看的帧号（null = 第 0 帧）

// 任务类型 → 页面上的说法（op id 是内部标识，不出现在页面上）
const KIND_LABEL = {
  extract: "抽取", pad: "扩充两侧画面", register: "登记外部视频",
  submit: "提交生成", adopt: "拷贝选中的视频到 case", merge: "合并", recompose: "裁回原尺寸",
};

const $ = (id) => document.getElementById(id);

// ── 步骤条与面板 ─────────────────────────────────────────────────────────

function renderSteps() {
  const bar = $("steps");
  bar.innerHTML = "";
  for (const [id, label] of STEPS) {
    const gate = (info.gates[id] || {});
    const tab = document.createElement("div");
    tab.className = "step-tab" + (id === curStep ? " cur" : "") + (gate.ok ? " done" : "");
    tab.innerHTML = `<span class="n">${label}</span>`;
    tab.onclick = () => showStep(id);
    bar.appendChild(tab);
  }
}

function showStep(id) {
  curStep = id;
  for (const [sid] of STEPS)
    $("pnl-" + sid).classList.toggle("cur", sid === id);
  renderSteps();
}

// ── info 渲染 ────────────────────────────────────────────────────────────

async function loadInfo() {
  const r = await fetch("/api/case/" + encodeURIComponent(CASE));
  if (!r.ok) { location.href = "/"; return; }
  info = await r.json();
  renderSteps();
  renderPick();
  renderExtract();
  renderGenerate();
  renderPrompt();
  renderFinish();
  renderCompare();
}

function gateText(id, elId) {
  const g = info.gates[id] || {};
  const el = $(elId);
  if (!el) return;
  el.textContent = g.ok ? ("✓ " + (g.evidence || "这一步已完成"))
                        : (g.reason || "这一步还没完成");
  el.className = "meta " + (g.ok ? "ok" : "bad");
}

// ── pick（patch 第一步）────────────────────────────────────────────────

function renderPick() {
  if (FLOW !== "patch") return;
  const vid = $("erpVid");
  if (!vid.src) vid.src = info.source_url;
  $("vpImg").src = info.previews.vp + (vpFrame === null ? "" : "&frame=" + vpFrame);
  const vp = info.viewport || {};
  $("vpMeta").textContent =
    `yaw ${vp.yaw_deg ?? "-"}  pitch ${vp.pitch_deg ?? "-"}  fov ${vp.fov_h_deg ?? "-"}  ` +
    `${vp.width ?? "?"}×${vp.height ?? "?"}`;
  const inner = info.inner;
  if (inner) {
    $("inMeta").textContent = `inner x=${inner.x} y=${inner.y} w=${inner.width} h=${inner.height}`;
    // inner 红框叠加：视口图是等比缩放的，分数即像素分数
    const box = $("innerBox");
    box.style.left = (inner.x / vp.width * 100) + "%";
    box.style.top = (inner.y / vp.height * 100) + "%";
    box.style.width = (inner.width / vp.width * 100) + "%";
    box.style.height = (inner.height / vp.height * 100) + "%";
    box.style.display = "block";
  }
  const fr = info.frames || {};
  if ($("fStart").value === "" && fr.start !== undefined) {
    $("fStart").value = fr.start; $("fEnd").value = fr.end;
  }
  const n = info.frame_count || 0;
  $("frameSlider").max = Math.max(0, n - 1);
  $("frameLabel").textContent = `${$("frameSlider").value}/${Math.max(0, n - 1)}`;
}

async function erpClick(e) {
  const p = PickCoords.point($("erpVid"), e);
  if (!p) return;
  // 点击处画十字（视口中心落点），实际红框随服务端重投影刷新
  const cross = $("vpCross"), foot = $("vpFoot");
  cross.style.display = "none"; foot.style.display = "none";
  await post("viewport", { fx: p.frac.x, fy: p.frac.y });
}

function setupPick() {
  if (FLOW !== "patch") return;
  // 全景点击定视口
  $("erpVid").addEventListener("click", erpClick);
  // 逐帧走查
  $("frameSlider").addEventListener("input", () => {
    vpFrame = +$("frameSlider").value;
    $("frameLabel").textContent = `${vpFrame}/${Math.max(0, (info.frame_count || 1) - 1)}`;
    $("vpImg").src = info.previews.vp + "&frame=" + vpFrame;
  });
  $("btnPrev").onclick = () => { $("frameSlider").value--; $("frameSlider").dispatchEvent(new Event("input")); };
  $("btnNext").onclick = () => { $("frameSlider").value++; $("frameSlider").dispatchEvent(new Event("input")); };
  $("btnPlay").onclick = () => {
    const v = $("erpVid");
    v.paused ? v.play() : v.pause();
    $("btnPlay").textContent = v.paused ? "▶" : "⏸";
  };
  // 视口图上拖框定 inner（提交 0..1 分数）
  PickCoords.drag($("vpImg"), {
    onMove: (r) => PickCoords.drawOverlay($("innerBox"), $("vpImg"), r.css),
    onCommit: async (r) => {
      const ir = $("vpImg").getBoundingClientRect();
      await post("inner", { fx: r.frac.x, fy: r.frac.y,
                            fw: r.frac.w, fh: r.frac.h });
      const _ = ir; //（frac 与显示尺寸无关，见 pick.js 契约）
    },
    onCancel: () => {},
  });
  // 摇杆：按住每 100ms 一次
  for (const b of document.querySelectorAll("#joy .jb")) {
    let timer = null;
    const step = () => post("nudge", { dyaw: +b.dataset.dy, dpitch: +b.dataset.dp })
      .then(() => { $("vpImg").src = info.previews.vp + "&" + Date.now(); });
    b.addEventListener("mousedown", () => { step(); timer = setInterval(step, 100); });
    window.addEventListener("mouseup", () => clearInterval(timer));
    b.addEventListener("mouseleave", () => clearInterval(timer));
  }
}

async function saveFrames() {
  await post("draft", { frames: { start: +$("fStart").value, end: +$("fEnd").value } });
}

async function resetDraft() {
  await post("reset-draft", {});
}

// ── extract（patch 第二步）─────────────────────────────────────────────

function renderExtract() {
  if (FLOW !== "patch") return;
  gateText("pick", "draftGate");
  $("btnExtract").disabled = !(info.gates.pick || {}).ok;
  const el = $("verList");
  el.innerHTML = info.extracts.length ? "" : '<span class="meta">还没有抽取版本</span>';
  for (const v of [...info.extracts].reverse()) {
    const cur = v.version === info.extract_current;
    el.innerHTML += `<div class="verline"><b class="${cur ? "ok" : ""}">${v.version}${cur ? " · current" : ""}</b>` +
      ` <span class="meta">${v.extracted ? "clip ✓" : "clip 缺"}</span>` +
      ` <span class="meta">${v.has_result ? "结果 ✓" : ""}</span></div>`;
  }
}

// ── generate（两流程共用）──────────────────────────────────────────────

function renderGenerate() {
  const rounds = info.rounds.rounds || [];
  const el = $("roundList");
  el.innerHTML = rounds.length ? "" : `<span class="meta">${info.rounds.hint || "还没有生成记录"}</span>`;
  const from = { mglc: "Seedance", manual: "外部视频" };
  // 轮次路径含反斜杠，拼进 HTML 属性会被转义吞掉——按钮只带序号，
  // 路径从 info 里按序号取（2026-10-16 反复失败事故的原因）
  rounds.forEach((r) => {
    const mine = r.round === info.adopt_round;
    const usable = r.result === "yes";
    const div = document.createElement("div");
    div.className = "verline";
    const b = document.createElement("b");
    if (mine) b.className = "ok";
    b.textContent = `${usable ? "✓" : "✗"} ${r.round}${mine ? " · 已选用" : ""}`;
    const span = document.createElement("span");
    span.className = "meta";
    span.textContent = ` ${from[r.backend] || r.backend} 生成 · ` +
      (usable ? "视频已生成，可选用" : "未生成视频，无法被后续流程使用");
    const btn = document.createElement("button");
    btn.textContent = "选用";
    btn.disabled = !usable;
    btn.onclick = () => run("adopt", { round: r.round_dir });
    div.append(b, span, btn);
    el.appendChild(div);
  });
  $("logBox").textContent = (info.log_tail && info.log_tail.length)
    ? info.log_tail.join("\n") : "（空）";
}

async function runSubmit() {
  // 变量值以「提示词」区保存的草稿为准（vrgen submit 自己读 prompt-vars.toml），
  // 这里不再传临时 --set，避免出现两份说不清谁生效的值。
  await run("submit", {});
}

// ── merge / pad / recompose ─────────────────────────────────────────────

function renderFinish() {
  // 完成后的按钮统一保持可点（重跑会覆盖，证据文案里写明了这一点）；
  // 置灰只表达"前置条件还没凑齐"。
  if (FLOW === "patch") {
    gateText("merge", "mergeGate");
    $("btnMerge").disabled = !(info.gates.merge || {}).ready;
  } else {
    gateText("pad", "padGate");
    $("btnPad").disabled = false;
    gateText("recompose", "recomposeGate");
    $("btnRecompose").disabled = !(info.gates.recompose || {}).ready;
  }
}

// ── compare ─────────────────────────────────────────────────────────────

function renderCompare() {
  gateText("compare", "cmpGate");
  const sel = $("outSel");
  const files = info.outputs.map(o => o.file);
  const keep = sel.value;
  sel.innerHTML = files.map(f => `<option>${f}</option>`).join("");
  if (files.includes(keep)) sel.value = keep;
  if (files.length) loadCompare();
}

function loadCompare() {
  const f = $("outSel").value;
  const out = info.outputs.find(o => o.file === f);
  if (!out) return;
  const a = $("cmpA"), b = $("cmpB");
  // src 没变就不重挂——重挂会让视频回到 0:00
  if (a.dataset.src !== info.source_url) { a.src = info.source_url; a.dataset.src = info.source_url; }
  if (b.dataset.src !== out.url) { b.src = out.url; b.dataset.src = out.url; }
}

function _fmtTime(s) {
  return `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
}

let cmpViewer = null;

function setupCompare() {
  const a = $("cmpA"), b = $("cmpB");
  cmpViewer = new Erp360Compare($("cmpCanvas"), a, b);
  // 成片跟着原片走：A 是时钟主，状态与进度偏差都从 A 的事件里对齐
  const sync = () => { if (Math.abs(b.currentTime - a.currentTime) > 0.05) b.currentTime = a.currentTime; };
  const refresh = () => {
    if (!isFinite(a.duration)) { $("cmpTime").textContent = "加载中…"; return; }
    if (!scrubbing) $("cmpSeek").value = a.currentTime;
    $("cmpTime").textContent = `${_fmtTime(a.currentTime)} / ${_fmtTime(a.duration)}`;
  };
  a.addEventListener("timeupdate", () => { sync(); refresh(); });
  a.addEventListener("seeked", () => { sync(); refresh(); });
  a.addEventListener("loadedmetadata", () => { $("cmpSeek").max = a.duration || 0; refresh(); });
  a.addEventListener("play", () => { b.play(); $("cmpPlay").textContent = "⏸"; });
  a.addEventListener("pause", () => { b.pause(); $("cmpPlay").textContent = "▶"; });
  a.addEventListener("ratechange", () => { b.playbackRate = a.playbackRate; });

  // 循环播放：A 是时钟主。播到结尾时把两路一起拉回 0:00 再继续，
  // 避免只循环一路造成下一轮左右错位（B 比 A 短，先停在最后一帧）。
  a.addEventListener("ended", () => {
    a.currentTime = 0;
    b.currentTime = 0;
    a.play();
  });

  // 统一传输控制：一套按钮/进度条同时驱动两份视频（A 的原生 controls 已移除）
  $("cmpPlay").onclick = () => { a.paused ? (a.play(), b.play()) : (b.pause(), a.pause()); };
  const seek = $("cmpSeek");
  let scrubbing = false;
  seek.addEventListener("input", () => {
    scrubbing = true;
    if (isFinite(a.duration)) { a.currentTime = +seek.value; b.currentTime = +seek.value; }
  });
  window.addEventListener("pointerup", () => { scrubbing = false; });
  $("cmpHome").onclick = () => cmpViewer && cmpViewer.resetView();

  // 分割线只改左右占比；视角拖动在画布上，erp360.js 自己接
  const wrap = $("cmpWrap"), bar = $("cmpBar");
  const setSplit = (fx) => {
    bar.style.left = (fx * 100) + "%";
    cmpViewer.setSplit(fx);
  };
  const move = (e) => {
    const r = wrap.getBoundingClientRect();
    setSplit(Math.min(0.98, Math.max(0.02, (e.clientX - r.left) / r.width)));
  };
  bar.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    e.stopPropagation();
    const mm = (ev) => move(ev);
    const mu = () => { removeEventListener("pointermove", mm); removeEventListener("pointerup", mu); };
    addEventListener("pointermove", mm); addEventListener("pointerup", mu);
  });
}

// ── 队列轮询 ─────────────────────────────────────────────────────────────

async function pollTasks() {
  try {
    const r = await fetch("/api/task");
    const s = await r.json();
    const lines = [];
    const label = (k) => KIND_LABEL[k] || k;
    if (s.current) {
      lines.push(`▶ 正在执行（${s.current.submitted} 提交）[${s.current.case}] ${label(s.current.kind)}…`);
      if (s.current.log) lines.push(`完整记录：${s.current.log}`);
      lines.push(...s.current.tail);
    } else lines.push("（当前没有任务在跑）");
    if (s.queued.length) {
      lines.push("── 排队等候（早提交的先跑）──");
      for (const q of s.queued) lines.push(`${q.submitted}  [${q.case}] ${label(q.kind)}`);
    }
    if (s.last.length) {
      lines.push("── 最近完成（新→旧，最多 10 条，行首是提交时间）──");
      for (const h of [...s.last].reverse()) {
        lines.push(`${h.submitted}  ${h.state === "done" ? "✓ 完成" : "✗ 失败"}：[${h.case}] ${label(h.kind)}` +
                   (h.log ? `（完整记录：${h.log}）` : ""));
        if (h.state !== "done") lines.push(...(h.tail || []));   // 失败时把出错原因端上来
      }
    }
    $("taskBox").textContent = lines.join("\n");
    const busy = !!(s.current || s.queued.length);
    if (wasRunning && !busy) loadInfo();      // 任务收尾 → 刷新门控与列表
    wasRunning = busy;
  } catch (_) { /* 服务重启间隙忽略 */ }
}

// ── 提示词（模板只读 + 变量编辑，决策 18）────────────────────────────────
// 数据来自 info.prompt（只在 loadInfo 与保存应答里带，post() 的应答没有这段，
// 所以提示词面板只在页面加载与保存动作时重画）。

function renderPrompt() {
  const p = info.prompt;
  const box = $("promptBox"), list = $("varList"), meta = $("promptMeta");
  if (!p || p.error) {
    box.textContent = (p && p.error) || "（提示词信息还没有加载）";
    list.innerHTML = "";
    meta.textContent = "";
    return;
  }
  box.textContent = p.template_text || "（还没有选定模板）";
  meta.textContent = p.template
    ? `模板 ${p.template}。正文里其余 ${p.geometry_keys.length} 个花括号占位是画布尺寸、` +
      "冗余带列号这类几何数值，提交时自动算好填入，不用手动改。"
    : "";
  const declared = (p.placeholders || []).filter(k => !p.geometry_keys.includes(k));
  const keys = [...new Set([...declared, ...Object.keys(p.vars || {})])];
  list.innerHTML = "";
  for (const k of keys) {
    const row = document.createElement("div");
    row.className = "varline";
    const name = document.createElement("b");
    name.textContent = k;
    const input = document.createElement("input");
    input.style.width = "340px";
    input.value = (p.vars || {})[k] ?? "";
    input.placeholder = "（还没填）";
    input.onchange = () => savePrompt({ set: k, value: input.value });
    row.append(name, input);
    if (!declared.includes(k)) {
      const del = document.createElement("button");
      del.textContent = "删除";
      del.onclick = () => savePrompt({ unset: k });
      row.append(del);
    } else {
      const lock = document.createElement("span");
      lock.className = "meta";
      lock.textContent = "模板自带，只能改值";
      row.append(lock);
    }
    list.appendChild(row);
  }
}

async function savePrompt(body) {
  const r = await fetch(`/api/case/${encodeURIComponent(CASE)}/prompt`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const d = await r.json();
  if (!r.ok) { alert(d.error || r.statusText); return; }
  info = d;                        // 应答是完整 info，prompt 段就是保存后的状态
  renderPrompt();
}

function addVar() {
  const k = $("newVarKey").value.trim(), v = $("newVarVal").value;
  if (!k) { alert("先填变量名"); return; }
  savePrompt({ set: k, value: v })
    .then(() => { $("newVarKey").value = ""; $("newVarVal").value = ""; });
}

// ── register 文件浏览器 ─────────────────────────────────────────────────

async function openBrowse() {
  $("browseBox").style.display = "block";
  await browse(location.pathname.startsWith("/case/") ? "E:\\VRAIGC" : ".");
}
async function browse(path) {
  const r = await fetch("/api/browse?path=" + encodeURIComponent(path));
  const d = await r.json();
  if (!r.ok) { $("bPath").textContent = d.error; return; }
  $("bPath").textContent = d.path;
  const el = $("bList");
  el.innerHTML = "";
  if (d.parent) {
    const up = document.createElement("div");
    up.className = "bitem"; up.textContent = "📁 ..";
    up.onclick = () => browse(d.parent);
    el.appendChild(up);
  }
  for (const dir of d.dirs) {
    const it = document.createElement("div");
    it.className = "bitem"; it.textContent = "📁 " + dir;
    it.onclick = () => browse(d.path + "\\" + dir);
    el.appendChild(it);
  }
  for (const f of d.files) {
    const it = document.createElement("div");
    it.className = "bitem"; it.textContent = "🎬 " + f.split(/[\\/]/).pop();
    it.onclick = () => { closeBrowse(); run("register", { file: f }); };
    el.appendChild(it);
  }
}
function closeBrowse() { $("browseBox").style.display = "none"; }

// ── 公共：POST 与队列提交 ───────────────────────────────────────────────

async function post(path, body) {
  const r = await fetch(`/api/case/${encodeURIComponent(CASE)}/${path}`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (r.status === 404) { location.href = "/"; return null; }
  const d = await r.json();
  if (!r.ok) { alert(d.error || r.statusText); return null; }
  info = d;                                   // 这些端点都回新 info
  renderSteps(); renderPick(); renderExtract(); renderGenerate(); renderFinish(); renderCompare();
  return d;
}

async function run(op, params = {}) {
  const r = await fetch(`/api/case/${encodeURIComponent(CASE)}/run`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ op, ...params }),
  });
  const d = await r.json();
  if (!r.ok) { alert(d.error || r.statusText); return; }
  wasRunning = true;
  pollTasks();
}
function runOp(op, params) { run(op, params); }

// ── 启动 ────────────────────────────────────────────────────────────────

setupPick();
setupCompare();
loadInfo().then(() => showStep(STEPS[0][0]));
setInterval(pollTasks, 2000);
pollTasks();
