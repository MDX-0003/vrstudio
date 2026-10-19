/** wizard.js —— 软向导的流程骨架：步骤条、pick/extract/生成/合并/对比。
 *
 * 公共状态与请求在 common.js；素材、参数/会话、提示词、任务分别在
 * assets.js / params.js / prompt.js / tasks.js。门控判据全部来自服务端
 * （info.gates），前端只渲染 ok/reason，不自算。
 */
"use strict";

const FLOW_STEPS = {
  patch: [["pick", "选区"], ["extract", "抽取"], ["generate", "生成"],
          ["merge", "合并"], ["compare", "对比"]],
  fullredraw: [["pad", "扩充画面"], ["generate", "生成"],
               ["recompose", "裁回原尺寸"], ["compare", "对比"]],
  gen: [["assets", "素材"], ["generate", "生成"]],
};
const STEPS = FLOW_STEPS[FLOW] || [];

// ── 步骤条与面板 ─────────────────────────────────────────────────────────

function renderSteps() {
  const bar = $("steps");
  if (!bar) return;
  bar.innerHTML = "";
  for (const [id, label] of STEPS) {
    const gate = ((info && info.gates && info.gates[id]) || {});
    const tab = document.createElement("div");
    tab.className = "step-tab" + (id === curStep ? " cur" : "") + (gate.ok ? " done" : "");
    tab.innerHTML = `<span class="n">${label}</span>`;
    tab.onclick = () => showStep(id);
    bar.appendChild(tab);
  }
}

function showStep(id) {
  curStep = id;
  for (const [sid] of STEPS) {
    const panel = $("pnl-" + sid);
    if (panel) panel.classList.toggle("cur", sid === id);
  }
  renderSteps();
}

// ── info 加载 ────────────────────────────────────────────────────────────

async function loadInfo() {
  const r = await fetch("/api/case/" + encodeURIComponent(CASE));
  if (!r.ok) { location.href = "/"; return; }
  info = await r.json();
  renderAll();
  loadSessions();          // 每次打开/刷新页面都重新取项目与会话
}

// ── pick（patch 第一步）────────────────────────────────────────────────

function renderPick() {
  if (FLOW !== "patch") return;
  const vid = $("erpVid");
  if (!vid.src) vid.src = info.source_url;
  if (info.previews && info.previews.vp) {
    $("vpImg").src = info.previews.vp + (vpFrame === null ? "" : "&frame=" + vpFrame);
  }
  const vp = info.viewport || {};
  $("vpMeta").textContent =
    `yaw ${vp.yaw_deg ?? "-"}  pitch ${vp.pitch_deg ?? "-"}  fov ${vp.fov_h_deg ?? "-"}  ` +
    `${vp.width ?? "?"}×${vp.height ?? "?"}`;
  const inner = info.inner;
  if (inner) {
    $("inMeta").textContent = `inner x=${inner.x} y=${inner.y} w=${inner.width} h=${inner.height}`;
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
  await post("viewport", { fx: p.frac.x, fy: p.frac.y });
}

function setupPick() {
  if (FLOW !== "patch") return;
  $("erpVid").addEventListener("click", erpClick);
  $("frameSlider").addEventListener("input", () => {
    vpFrame = +$("frameSlider").value;
    $("frameLabel").textContent = `${vpFrame}/${Math.max(0, (info.frame_count || 1) - 1)}`;
    if (info.previews && info.previews.vp)
      $("vpImg").src = info.previews.vp + "&frame=" + vpFrame;
  });
  $("btnPrev").onclick = () => { $("frameSlider").value--; $("frameSlider").dispatchEvent(new Event("input")); };
  $("btnNext").onclick = () => { $("frameSlider").value++; $("frameSlider").dispatchEvent(new Event("input")); };
  $("btnPlay").onclick = () => {
    const v = $("erpVid");
    v.paused ? v.play() : v.pause();
    $("btnPlay").textContent = v.paused ? "▶" : "⏸";
  };
  PickCoords.drag($("vpImg"), {
    onMove: (r) => PickCoords.drawOverlay($("innerBox"), $("vpImg"), r.css),
    onCommit: async (r) => {
      await post("inner", { fx: r.frac.x, fy: r.frac.y,
                            fw: r.frac.w, fh: r.frac.h });
    },
    onCancel: () => {},
  });
  for (const b of document.querySelectorAll("#joy .jb")) {
    let timer = null;
    const step = () => post("nudge", { dyaw: +b.dataset.dy, dpitch: +b.dataset.dp })
      .then(() => {
        if (info.previews && info.previews.vp)
          $("vpImg").src = info.previews.vp + "&" + Date.now();
      });
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

// ── generate（三条流程共用）───────────────────────────────────────────

function renderGenerate() {
  const rounds = (info.rounds && info.rounds.rounds) || [];
  const el = $("roundList");
  if (!el) return;
  el.innerHTML = rounds.length ? "" : `<span class="meta">${(info.rounds && info.rounds.hint) || "还没有生成记录"}</span>`;
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

async function runSubmit(dryRun = false) {
  if (FLOW === "gen" && !((info.gates || {}).assets || {}).ok) {
    alert("先在「素材」步骤添加至少 1 个视频，并给每个素材选择角色");
    return;
  }
  await run(dryRun ? "dry_run" : "submit", {
    mode: ($("genMode") || {}).value || "",
    aspect_ratio: ($("genAspect") || {}).value || "",
    resolution: ($("genResolution") || {}).value || "",
    scale: ($("genScale") || {}).value || "",
  });
}

// ── merge / pad / recompose ─────────────────────────────────────────────

function renderFinish() {
  if (FLOW === "patch") {
    gateText("merge", "mergeGate");
    $("btnMerge").disabled = !(info.gates.merge || {}).ready;
  } else if (FLOW === "fullredraw") {
    gateText("pad", "padGate");
    $("btnPad").disabled = false;
    gateText("recompose", "recomposeGate");
    $("btnRecompose").disabled = !(info.gates.recompose || {}).ready;
  }
}

// ── 文件列表 ────────────────────────────────────────────────────────────

function _fmtBytes(n) {
  if (n == null) return "";
  const units = ["B", "KB", "MB", "GB"];
  let v = n, i = 0;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
  return (i === 0 ? v.toFixed(0) : v.toFixed(v >= 100 ? 0 : 1)) + " " + units[i];
}

function _fmtMtime(sec) {
  if (sec == null) return "—";
  const d = new Date(sec * 1000);
  const p = (x) => String(x).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ` +
         `${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

function _fileRow(f) {
  const row = document.createElement("div");
  row.className = "fileline" + (f.exists ? "" : " bad");
  row.title = f.path;
  const name = document.createElement("span");
  name.className = "fname";
  name.textContent = f.rel || f.path;
  const meta = document.createElement("span");
  meta.className = "fmeta";
  meta.textContent = f.exists
    ? `${_fmtMtime(f.mtime)}　${_fmtBytes(f.size)}`
    : "不存在";
  row.append(name, meta);
  return row;
}

function _appendFileGroup(box, label, files) {
  const group = document.createElement("div");
  group.className = "fgroup";
  const tag = document.createElement("b");
  tag.textContent = label;
  group.appendChild(tag);
  for (const f of files) group.appendChild(_fileRow(f));
  box.appendChild(group);
}

function renderStepFiles() {
  const sf = (info && info.step_files) || {};
  for (const step of Object.keys(sf)) {
    const box = $("files-" + step);
    if (!box) continue;
    box.textContent = "";
    let any = false;
    const spec = sf[step] || {};
    const stages = spec.groups || [{ label: null, inputs: spec.inputs, outputs: spec.outputs }];
    for (const stage of stages) {
      if (stage.label) {
        const title = document.createElement("div");
        title.className = "fstage";
        title.textContent = stage.label;
        box.appendChild(title);
      }
      for (const [label, files] of [["输入", stage.inputs], ["输出", stage.outputs]]) {
        if (!files || !files.length) continue;
        any = true;
        _appendFileGroup(box, label, files);
      }
    }
    if (!any) {
      const empty = document.createElement("span");
      empty.className = "meta";
      empty.textContent = "这一步还没有可显示的文件";
      box.appendChild(empty);
    }
  }
}

// ── compare ─────────────────────────────────────────────────────────────

function renderCompare() {
  if (FLOW === "gen") return;
  gateText("compare", "cmpGate");
  const sel = $("outSel");
  const files = (info.outputs || []).map(o => o.file);
  const keep = sel.value;
  sel.innerHTML = files.map(f => `<option>${f}</option>`).join("");
  if (files.includes(keep)) sel.value = keep;
  if (files.length) loadCompare();
}

function loadCompare() {
  const f = $("outSel").value;
  const out = (info.outputs || []).find(o => o.file === f);
  if (!out) return;
  const a = $("cmpA"), b = $("cmpB");
  if (a.dataset.src !== info.source_url) { a.src = info.source_url; a.dataset.src = info.source_url; }
  if (b.dataset.src !== out.url) { b.src = out.url; b.dataset.src = out.url; }
}

function _fmtTime(s) {
  return `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
}

let cmpViewer = null;

function setupCompare() {
  if (FLOW === "gen") return;
  const a = $("cmpA"), b = $("cmpB");
  cmpViewer = new Erp360Compare($("cmpCanvas"), a, b);
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

  a.addEventListener("ended", () => {
    a.currentTime = 0;
    b.currentTime = 0;
    a.play();
  });

  $("cmpPlay").onclick = () => { a.paused ? (a.play(), b.play()) : (b.pause(), a.pause()); };
  const seek = $("cmpSeek");
  let scrubbing = false;
  seek.addEventListener("input", () => {
    scrubbing = true;
    if (isFinite(a.duration)) { a.currentTime = +seek.value; b.currentTime = +seek.value; }
  });
  window.addEventListener("pointerup", () => { scrubbing = false; });
  $("cmpHome").onclick = () => cmpViewer && cmpViewer.resetView();

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

// ── register 文件浏览器（非 Seedance 结果）─────────────────────────────

async function openBrowse() {
  $("browseBox").style.display = "block";
  await browse("E:\\VRAIGC");
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

// ── 启动 ────────────────────────────────────────────────────────────────

setupPick();
setupCompare();
loadInfo().then(() => showStep(STEPS[0][0]));
setInterval(pollTasks, 2000);
pollTasks();
