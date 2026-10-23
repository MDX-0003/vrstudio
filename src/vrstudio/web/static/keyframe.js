/** keyframe.js —— 首尾帧步骤：两帧底图、图片步提示词、候选挑选与交接。
 *
 * 图片步的参考图是固定契约（图1 原始帧、图2 三视图、图3 首帧定稿），
 * 由服务端按 keyframe.toml 与当前 extract 解析；这里的变量草稿写在
 * keyframe-vars.toml，与视频提示词草稿互不覆盖。挑选首帧后服务端自动排队
 * 尾帧批次，页面只负责展示候选与状态。
 */
"use strict";

const KF_FIELDS = ["figure", "pose_first", "pose_last", "preserve"];

let kfPrompts = { first: null, last: null };   // prompt show 的应答
let kfPromptLoading = false;
let kfRounds = [];
let kfPending = new Map();                      // 键 -> 尚未保存的值
let kfSaveTimer = null;
let kfHint = "";

function _kf() {
  return (info && info.keyframe) || {};
}

function _kfRaw(step) {
  const id = step === "first" ? "first_raw" : "last_raw";
  return ((info && info.raw_frames) || []).find(f => f.id === id) || null;
}

/** 两个步骤模板的显示名、说明与值合并：共享字段以先读到的为准。 */
function _kfMeta() {
  const labels = {}, help = {}, vars = {};
  for (const p of [kfPrompts.first, kfPrompts.last]) {
    if (!p) continue;
    Object.assign(labels, p.labels || {});
    Object.assign(help, p.help || {});
    Object.assign(vars, p.vars || {});
  }
  for (const [key, value] of kfPending) vars[key] = value;
  return { labels, help, vars };
}

function renderKeyframe() {
  if (!$("kfBody") || FLOW !== "patch") return;
  renderKfStatus();
  renderKfRaw();
  renderKfSheet();
  renderKfSession();
  renderKfVars();
  renderKfStep("first");
  renderKfStep("last");
  renderKfHistory();
  const hint = $("kfHint");
  if (hint) hint.textContent = kfHint;
}

function renderKfStatus() {
  const el = $("kfStatus");
  if (!el) return;
  const st = _kf();
  if (st.error) {
    el.className = "meta bad";
    el.textContent = st.error;
  } else if (st.stale) {
    el.className = "meta bad";
    el.textContent = "抽取版本已更新，需要重新定稿两帧。";
  } else if (st.complete) {
    el.className = "meta ok";
    el.textContent = "两帧已定稿；可以交给视频生成。";
  } else if (st.first) {
    el.className = "meta";
    el.textContent = "首帧已定稿；接着定尾帧。";
  } else {
    el.className = "meta";
    el.textContent = "首帧待定。";
  }
  const handoff = $("kfHandoff");
  if (handoff) handoff.disabled = !(st.complete && !st.stale);
}

function renderKfRaw() {
  const box = $("kfRawFrames");
  if (!box) return;
  const rows = (info && info.raw_frames) || [];
  box.innerHTML = "";
  if (!rows.length) {
    box.innerHTML = '<span class="meta">先完成抽取；首尾原始帧会随 clip 一起产出。</span>';
    return;
  }
  for (const row of rows) {
    const fig = document.createElement("figure");
    if (!row.exists) {
      const missing = document.createElement("div");
      missing.className = "bad";
      missing.textContent = `${row.label}不存在；重新抽取一次。`;
      fig.appendChild(missing);
    } else {
      const img = document.createElement("img");
      img.src = row.url + "?v=" + (row.size || 0);
      img.title = "打开原图：" + row.path;
      img.onclick = () => window.open(row.url, "_blank");
      const cap = document.createElement("figcaption");
      const size = row.width && row.height ? `${row.width}×${row.height}` : "尺寸未知";
      const mb = row.size ? ` · ${(row.size / 1e6).toFixed(1)} MB` : "";
      cap.textContent = `${row.label} · ${size}${mb}`;
      fig.append(img, cap);
    }
    box.appendChild(fig);
  }
}

function renderKfSheet() {
  const sel = $("kfSheet");
  if (!sel) return;
  const st = _kf();
  const rows = st.sheet_candidates || [];
  const sig = JSON.stringify([rows.map(r => r.id), st.sheet]);
  if (sel.dataset.sig !== sig) {
    sel.dataset.sig = sig;
    sel.innerHTML = "";
    if (!rows.length) {
      sel.appendChild(new Option("（素材库还没有三视图）", ""));
    } else {
      if (rows.length > 1 && !st.sheet) {
        sel.appendChild(new Option("请选择三视图", ""));
      }
      for (const row of rows) {
        sel.appendChild(new Option(`${row.label} · ${row.id}`, row.id));
      }
      sel.value = st.sheet || "";
    }
    sel.disabled = !rows.length;
  }
  const hint = $("kfSheetHint");
  if (hint) {
    hint.textContent = rows.length
      ? "只影响图片步；视频提交集是否使用它由「生成」步骤的勾选决定"
      : "先到「生成」步骤的素材库添加一张主体参考图";
  }
}

function _kfSessionLabel() {
  const current = ((info && info.generation_params) || {}).current || {};
  const sid = Number(current.session_id) || 0;
  if (!sid) return "";
  for (const project of (sessionTree && sessionTree.projects) || []) {
    for (const episode of project.episodes || []) {
      const session = (episode.sessions || []).find(s => s.id === sid);
      if (session) {
        return `${project.name || project.id} / 第 ${episode.no} 集 / ${session.name || session.id}`;
      }
    }
  }
  return `会话 ${sid}`;
}

function renderKfSession() {
  const el = $("kfSession");
  if (!el) return;
  const label = _kfSessionLabel();
  el.className = "meta " + (label ? "ok" : "bad");
  el.textContent = label
    ? `计费会话：${label}`
    : "还没有选择项目生成会话；图片生成按项目额度计费。";
}

/** 会话控件只在「生成」步骤的底栏出现；这里只负责把用户送过去。 */
function gotoSession() {
  showStep("generate");
  const sel = $("sessSession");
  if (sel) {
    sel.focus();
    sel.scrollIntoView({ behavior: "smooth", block: "center" });
  }
}

async function setKeyframeSheet(assetId) {
  if (!assetId) return;
  const r = await fetch(`/api/case/${encodeURIComponent(CASE)}/keyframe/sheet`, {
    method: "PUT", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ asset: assetId }),
  });
  const d = await r.json();
  if (!r.ok) { alert(d.error || r.statusText); return; }
  info = d;
  kfHint = "三视图已绑定；图片步只改这里，不影响视频提交集。";
  renderAll();
}

function renderKfVars() {
  const box = $("kfVars");
  if (!box) return;
  if (!kfPrompts.first) {
    box.innerHTML = '<span class="meta">（提示词草稿还没读到；进入本步骤后自动读取）</span>';
    return;
  }
  const meta = _kfMeta();
  const sig = JSON.stringify(KF_FIELDS);
  if (box.dataset.sig === sig && box.children.length) {
    for (const card of box.querySelectorAll(".varCard")) {
      const ta = card.querySelector("textarea");
      if (!ta || document.activeElement === ta) continue;
      ta.value = meta.vars[card.dataset.key] ?? "";
    }
    return;
  }
  box.dataset.sig = sig;
  box.innerHTML = "";
  for (const key of KF_FIELDS) {
    const card = document.createElement("details");
    card.className = "varCard";
    card.dataset.key = key;
    const summary = document.createElement("summary");
    const name = document.createElement("span");
    name.className = "vname";
    name.textContent = meta.labels[key] || key;
    const keyEl = document.createElement("span");
    keyEl.className = "vkey";
    keyEl.textContent = key;
    summary.append(name, keyEl);
    const help = meta.help[key];
    if (help) {
      const helpEl = document.createElement("span");
      helpEl.className = "vhelp";
      helpEl.textContent = help;
      summary.appendChild(helpEl);
    }
    card.appendChild(summary);
    const ta = document.createElement("textarea");
    ta.rows = 3;
    ta.value = meta.vars[key] ?? "";
    ta.placeholder = "（还没填）";
    ta.oninput = () => {
      autoGrow(ta);
      scheduleKeyframeSave(key, ta.value);
    };
    card.appendChild(ta);
    card.open = key !== "preserve";     // 三张要填的卡默认展开；保持项可折叠
    card.addEventListener("toggle", () => { if (card.open) autoGrow(ta); });
    box.appendChild(card);
  }
  for (const ta of box.querySelectorAll("textarea")) autoGrow(ta);
}

function scheduleKeyframeSave(key, value) {
  kfPending.set(key, value);
  clearTimeout(kfSaveTimer);
  kfSaveTimer = setTimeout(() => { flushKeyframeSaves(); }, 400);
}

async function flushKeyframeSaves() {
  clearTimeout(kfSaveTimer);
  kfSaveTimer = null;
  if (!kfPending.size) return true;
  const vars = Object.fromEntries(kfPending);
  kfPending.clear();
  const step = _kf().first ? "last" : "first";
  const r = await fetch(`/api/case/${encodeURIComponent(CASE)}/keyframe/prompt`, {
    method: "PUT", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ step, vars }),
  });
  const d = await r.json();
  if (!r.ok) {
    for (const [key, value] of Object.entries(vars)) kfPending.set(key, value);
    alert(d.error || r.statusText);
    return false;
  }
  kfPrompts[step] = d;
  const other = step === "first" ? "last" : "first";
  if (kfPrompts[other]) {
    kfPrompts[other].vars = { ...(kfPrompts[other].vars || {}), ...vars };
    kfPrompts[other].rendered = null;
  }
  renderKfVars();
  return true;
}

function renderKfStep(step) {
  const box = $(step === "first" ? "kfFirst" : "kfLast");
  if (!box) return;
  const st = _kf();
  const role = step === "first" ? "first_frame" : "last_frame";
  const pick = step === "first" ? st.first : st.last;
  const locked = step === "last" && !st.first;
  box.innerHTML = "";

  const head = document.createElement("div");
  head.className = "kfStepHead";
  const title = document.createElement("b");
  title.textContent = step === "first" ? "③ 首帧定稿" : "④ 尾帧定稿";
  const status = document.createElement("span");
  status.className = "meta" + (pick ? " ok" : "");
  status.textContent = locked
    ? "先定首帧"
    : pick
      ? `已定稿 · ${pick.asset} · ${pick.round} 第 ${pick.index} 张`
      : "待定";
  head.append(title, status);
  box.appendChild(head);

  const refs = document.createElement("div");
  refs.className = "meta kfRef";
  refs.textContent = step === "first"
    ? "参考图：图1 首帧原始图 · 图2 人物三视图"
    : "参考图：图1 尾帧原始图 · 图2 人物三视图 · 图3 首帧定稿图";
  box.appendChild(refs);

  const raw = _kfRaw(step);
  const canRun = !locked && !st.stale && raw && raw.exists && st.sheet;
  const run = document.createElement("button");
  run.textContent = pick ? "重新生成候选" : "生成候选";
  run.disabled = !canRun;
  run.title = canRun
    ? "请求 4 张；平台实际返回数量以候选区为准。会消耗图片生成额度"
    : "缺原始帧、三视图或当前步骤尚未解锁";
  run.onclick = () => runKeyframe(step);
  box.appendChild(run);

  if (locked) {
    const note = document.createElement("div");
    note.className = "meta";
    note.textContent = "首帧定稿后，尾帧会用这张图作为图3自动开始生成。";
    box.appendChild(note);
  }

  const grid = document.createElement("div");
  grid.className = "kfGrid";
  const rounds = kfRounds.filter(r => (r.step || "") === step && (r.results || []).length);
  if (!rounds.length) {
    const empty = document.createElement("span");
    empty.className = "meta";
    empty.textContent = "还没有候选；点上方按钮生成。";
    grid.appendChild(empty);
  }
  for (const round of rounds) {
    const block = document.createElement("div");
    block.className = "kfRound";
    const rhead = document.createElement("div");
    rhead.className = "rhead";
    const asked = Number(round.num) || 0;
    rhead.textContent = `${round.round} · ${round.stage}` +
      (asked ? ` · 请求 ${asked} 张 / 返回 ${round.results.length} 张` : "");
    const row = document.createElement("div");
    row.className = "kfRow";
    for (const result of round.results) {
      const item = document.createElement("div");
      const current = pick && pick.round === round.round && pick.index === result.index;
      item.className = "kfItem" + (current ? " current" : "");
      const img = document.createElement("img");
      img.src = result.url + "?v=" + Date.now();
      img.title = "打开原图";
      img.onclick = () => window.open(result.url, "_blank");
      const btn = document.createElement("button");
      btn.textContent = current
        ? "当前定稿"
        : step === "first" ? "设为首帧定稿" : "设为尾帧定稿";
      btn.disabled = !!current;
      btn.onclick = () => pickKeyframe(round.round, result.index, role);
      item.append(img, btn);
      row.appendChild(item);
    }
    block.append(rhead, row);
    grid.appendChild(block);
  }
  box.appendChild(grid);
}

function renderKfHistory() {
  const el = $("kfOldRounds");
  if (!el) return;
  const old = kfRounds.filter(r => !["first", "last"].includes(r.step || "")
    && (r.results || []).length);
  el.innerHTML = "";
  if (!old.length) {
    el.textContent = "（没有历史批次）";
    return;
  }
  for (const round of old) {
    const line = document.createElement("div");
    line.textContent = `${round.round} · ${round.stage} · ${round.results.length} 张候选`;
    el.appendChild(line);
  }
}

async function loadKeyframePrompts(force = false) {
  if (kfPromptLoading) return;
  if (kfPrompts.first && kfPrompts.last && !force) {
    renderKeyframe();
    return;
  }
  kfPromptLoading = true;
  try {
    for (const step of ["first", "last"]) {
      const r = await fetch(
        `/api/case/${encodeURIComponent(CASE)}/keyframe/prompt?step=${step}`);
      const d = await r.json();
      if (r.ok) {
        kfPrompts[step] = d;
      } else {
        kfPrompts[step] = null;
        if (step === "first") kfHint = d.error || "提示词草稿读取失败";
      }
    }
  } finally {
    kfPromptLoading = false;
  }
  renderKeyframe();
}

async function loadKeyframeRounds() {
  if (!$("kfBody")) return;
  const r = await fetch(`/api/case/${encodeURIComponent(CASE)}/keyframe/rounds`);
  const d = await r.json();
  if (!r.ok) {
    kfHint = d.error || r.statusText;
    renderKeyframe();
    return;
  }
  kfRounds = d.rounds || [];
  renderKeyframe();
}

async function runKeyframe(step) {
  if (!(await flushKeyframeSaves())) return;
  const meta = _kfMeta();
  const need = step === "first" ? ["figure", "pose_first"] : ["figure", "pose_last"];
  const missing = need.filter(key => !String(meta.vars[key] || "").trim());
  if (missing.length) {
    alert(`先填写：${missing.map(key => meta.labels[key] || key).join("、")}`);
    return;
  }
  const body = { step };
  const model = $("kfModel");
  if (model && model.value) body.model_id = +model.value;
  const resolution = $("kfResolution");
  if (resolution && resolution.value) body.resolution = resolution.value;
  const r = await fetch(`/api/case/${encodeURIComponent(CASE)}/keyframe/run`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const d = await r.json();
  if (!r.ok) { alert(d.error || r.statusText); return; }
  kfHint = "图片任务已进入队列；完成后候选会自动出现在下面。";
  renderKeyframe();
  pollTasks();
}

async function pickKeyframe(round, index, role) {
  const r = await fetch(`/api/case/${encodeURIComponent(CASE)}/keyframe/pick`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ round, index, role }),
  });
  const d = await r.json();
  if (!r.ok) { alert(d.error || r.statusText); return; }
  info = d;
  kfHint = d.keyframe_hint || "已写入素材库。";
  renderAll();
  loadKeyframeRounds();
  if (d.keyframe_queued) pollTasks();
}

async function handoffKeyframe() {
  if (!(await flushKeyframeSaves())) return;
  const r = await fetch(`/api/case/${encodeURIComponent(CASE)}/keyframe/handoff`, {
    method: "POST",
  });
  const d = await r.json();
  if (!r.ok) { alert(d.error || r.statusText); return; }
  info = d;
  kfHint = "已切到 firstLastFrame 并勾选这一对；补动作段后提交。";
  renderAll();
  showStep("generate");
  applyClipDuration();
}

/** 首尾帧成片长度跟随 clip：把底栏时长选到最接近的整秒。 */
function applyClipDuration() {
  const sel = $("genDuration");
  if (!sel) return;
  const frames = (info && info.frames) || {};
  const fps = Number(info && info.fps) || 30;
  const count = Number(frames.end || 0) - Number(frames.start || 0) + 1;
  if (!(count > 0)) return;
  const seconds = Math.max(1, Math.round(count / fps));
  const values = [...sel.options].map(o => o.value).filter(v => v !== "auto");
  if (!values.length) return;
  const closest = values.reduce(
    (best, value) => Math.abs(+value - seconds) < Math.abs(+best - seconds) ? value : best);
  sel.value = closest;
}
