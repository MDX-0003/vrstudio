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
  gen: [["generate", "生成"]],
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

// 拖框只影响页面上的 #innerBox，不影响视口图：拖拽中不拿服务端的旧 inner
// 覆盖正在拖的框；连续提交用序号保证旧响应不会盖住新结果。
let pickDrag = null;
let innerSeq = 0;
let previewTimer = null;     // 下方预览的防抖
let proxyPoll = null;        // 预览代理生成中的轮询
let pendingSeek = null;      // video 元数据未就绪时暂存的 seek

function pickPreviewUrl() {
  if (!info.previews || !info.previews.vp) return null;
  return info.previews.vp + (vpFrame === null ? "" : "&frame=" + vpFrame);
}

function maxFrame() {
  return Math.max(0, (info.frame_count || 1) - 1);
}

function fpsOf() {
  if (info.fps) return +info.fps;
  if (info.frame_count && info.duration_s)
    return info.frame_count / info.duration_s;
  return 0;
}

function frameNow() {
  return vpFrame === null ? +$("frameSlider").value : vpFrame;
}

function updateFrameLabel() {
  const max = maxFrame();
  const frame = Math.max(0, Math.min(max, Math.round(frameNow())));
  const fps = fpsOf();
  const total = info.duration_s || (fps ? max / fps : 0);
  const now = fps ? frame / fps : 0;
  $("frameLabel").textContent =
    `${frame}/${max} · ${now.toFixed(1)}s / ${total.toFixed(1)}s`;
}

function refreshPreview() {
  const url = pickPreviewUrl();
  if (!url) return;
  const img = $("vpImg");
  if (img.getAttribute("src") !== url) img.src = url;
}

function schedulePreview() {
  clearTimeout(previewTimer);
  previewTimer = setTimeout(refreshPreview, 150);
}

/** 顶部播放器优先用代理；代理没就绪时先用源片，Chrome 能播就先用着。 */
function videoUrl() {
  if (info.proxy && info.proxy.state === "ready") return info.proxy.url;
  return info.source_url;
}

function setVideoUrl(url) {
  const v = $("erpVid");
  if (!url || v.getAttribute("src") === url) return;
  const t = isFinite(v.currentTime) ? v.currentTime : 0;
  const playing = !v.paused && !v.ended;
  v.src = url;
  v.addEventListener("loadedmetadata", () => {
    if (t > 0 && isFinite(v.duration) && v.duration > 0)
      v.currentTime = Math.min(t, v.duration);
    if (playing) v.play().catch(() => {});
  }, { once: true });
}

function seekVideo(t) {
  const v = $("erpVid");
  if (!isFinite(t)) return;
  if (v.readyState >= 1) v.currentTime = t;
  else pendingSeek = t;
}

/** 滑块/上下一帧：seek 顶部视频，并请求同一帧的下方预览。 */
function seekToFrame(frame) {
  const f = Math.max(0, Math.min(maxFrame(), Math.round(frame)));
  vpFrame = f;
  $("frameSlider").value = f;
  updateFrameLabel();
  const fps = fpsOf();
  if (fps > 0) seekVideo(f / fps);
  schedulePreview();
}

function updateProxyState() {
  const el = $("proxyState");
  if (!el) return;
  const st = info.proxy && info.proxy.state;
  if (st === "ready")
    el.textContent = "预览代理：已就绪（顶部播放器与服务端预览共用）";
  else if (st === "disabled")
    el.textContent = "源片本身已是浏览器可播格式，使用原片预览";
  else if (st === "building")
    el.textContent = "预览代理：正在生成 4K 副本（首次访问约 10 秒）…";
  else if (st === "failed")
    el.textContent = "预览代理生成失败，详见服务日志";
  else
    el.textContent = "预览代理：准备中…";
}

/** 缺代理时触发一次生成并轮询；完成后重新取 info，顶部切到代理。 */
async function ensureProxy() {
  if (!info || !info.proxy) return;
  const st = info.proxy.state;
  if (st === "ready" || st === "disabled" || st === "failed") return;
  if (proxyPoll) return;
  const tick = async () => {
    try {
      const r = await fetch(`/api/case/${encodeURIComponent(CASE)}/proxy`,
                            { method: "POST" });
      const d = await r.json();
      if (!r.ok) {
        clearInterval(proxyPoll); proxyPoll = null;
        const el = $("proxyState");
        if (el) el.textContent = `预览代理生成失败：${d.error || r.statusText}`;
        return;
      }
      if (info.proxy && d.state) info.proxy.state = d.state;
      updateProxyState();
      if (d.state === "ready" || d.state === "disabled") {
        clearInterval(proxyPoll); proxyPoll = null;
        await loadInfo();
      } else if (d.state === "failed") {
        clearInterval(proxyPoll); proxyPoll = null;
        const el = $("proxyState");
        if (el) el.textContent = "预览代理生成失败，详见服务日志";
      }
    } catch (_) { /* 网络间隙继续轮询 */ }
  };
  proxyPoll = setInterval(tick, 2000);
  await tick();
}

/** 画唯一的那一个选区框：拖拽中虚线，保存后实线。 */
function setInnerBox(inner, committed) {
  const box = $("innerBox");
  const vp = (info && info.viewport) || {};
  if (!inner || !vp.width || !vp.height) {
    box.style.display = "none";
    return;
  }
  box.style.left = (inner.x / vp.width * 100) + "%";
  box.style.top = (inner.y / vp.height * 100) + "%";
  box.style.width = (inner.width / vp.width * 100) + "%";
  box.style.height = (inner.height / vp.height * 100) + "%";
  box.style.display = "block";
  box.classList.toggle("committed", !!committed);
}

function setInnerMeta(inner) {
  $("inMeta").textContent = inner
    ? `inner x=${inner.x} y=${inner.y} w=${inner.width} h=${inner.height}`
    : "inner -";
}

function renderPick() {
  if (FLOW !== "patch") return;
  setVideoUrl(videoUrl());
  refreshPreview();
  const vp = info.viewport || {};
  $("vpMeta").textContent =
    `yaw ${vp.yaw_deg ?? "-"}  pitch ${vp.pitch_deg ?? "-"}  fov ${vp.fov_h_deg ?? "-"}  ` +
    `${vp.width ?? "?"}×${vp.height ?? "?"}`;
  if (info.inner && (!pickDrag || !pickDrag.active)) {
    setInnerBox(info.inner, true);
    setInnerMeta(info.inner);
  } else if (!info.inner) {
    setInnerBox(null);
    setInnerMeta(null);
  }
  const fr = info.frames || {};
  if ($("fStart").value === "" && fr.start !== undefined) {
    $("fStart").value = fr.start; $("fEnd").value = fr.end;
  }
  const n = info.frame_count || 0;
  const slider = $("frameSlider");
  slider.max = Math.max(0, n - 1);
  if (+slider.value > +slider.max) slider.value = slider.max;
  if (vpFrame !== null && vpFrame > maxFrame()) vpFrame = maxFrame();
  updateFrameLabel();
  updateProxyState();
  if (!info.proxy || info.proxy.state === "missing"
      || info.proxy.state === "building") {
    ensureProxy();
  }
}

async function erpClick(e) {
  const p = PickCoords.point($("erpVid"), e);
  if (!p) return;
  await post("viewport", { fx: p.frac.x, fy: p.frac.y });
}

function setupPick() {
  if (FLOW !== "patch") return;
  const v = $("erpVid");
  v.addEventListener("click", erpClick);
  v.addEventListener("error", () => {
    const err = v.error;
    if (window.reportClientError) {
      window.reportClientError("warn", "erpVid decode failed", {
        context: "erpVid",
        code: err && err.code,
        source: v.currentSrc,
        detail: err && err.message,
      });
    }
    ensureProxy();               // 源片解不了时，代理是唯一的可播路径
  });
  v.addEventListener("loadedmetadata", () => {
    if (pendingSeek != null) {
      v.currentTime = Math.min(pendingSeek, v.duration || pendingSeek);
      pendingSeek = null;
    }
    updateFrameLabel();
  });
  v.addEventListener("seeked", () => {
    const fps = fpsOf();
    if (!fps) return;
    const f = Math.max(0, Math.min(maxFrame(), Math.round(v.currentTime * fps)));
    if (f !== vpFrame) {
      vpFrame = f;
      $("frameSlider").value = f;
      updateFrameLabel();
    }
    schedulePreview();           // 用户 seek：下方预览跟上一次
  });
  v.addEventListener("timeupdate", () => {
    if (v.paused) return;        // 播放中只让滑块跟视频，不逐帧请求预览
    const fps = fpsOf();
    if (!fps) return;
    const f = Math.max(0, Math.min(maxFrame(), Math.round(v.currentTime * fps)));
    vpFrame = f;
    $("frameSlider").value = f;
    updateFrameLabel();
  });
  v.addEventListener("play", () => {
    clearTimeout(previewTimer);
    $("btnPlay").textContent = "⏸";
  });
  v.addEventListener("pause", () => {
    $("btnPlay").textContent = "▶";
    schedulePreview();           // 暂停时补当前帧
  });
  $("frameSlider").addEventListener("input", () => {
    seekToFrame(+$("frameSlider").value);
  });
  $("btnPrev").onclick = () => seekToFrame(frameNow() - 1);
  $("btnNext").onclick = () => seekToFrame(frameNow() + 1);
  $("btnPlay").onclick = () => {
    if (v.paused) v.play().catch(() => {});
    else v.pause();
  };
  pickDrag = PickCoords.drag($("vpImg"), {
    onBegin: () => $("innerBox").classList.remove("committed"),
    onMove: (r) => PickCoords.drawOverlay($("innerBox"), $("vpImg"), r.css),
    onCommit: async (r) => {
      const vp = info.viewport || {};
      if (!vp.width || !vp.height) return;
      // 与服务端 int(fx * width) 的截断一致；界面立刻用这组值，不等写盘。
      const inner = {
        x: Math.floor(r.frac.x * vp.width),
        y: Math.floor(r.frac.y * vp.height),
        width: Math.floor(r.frac.w * vp.width),
        height: Math.floor(r.frac.h * vp.height),
      };
      const seq = ++innerSeq;
      setInnerBox(inner, true);
      setInnerMeta(inner);
      const d = await post("inner", { fx: r.frac.x, fy: r.frac.y,
                                      fw: r.frac.w, fh: r.frac.h },
                           { stale: () => seq !== innerSeq });
      if (!d && seq === innerSeq) {   // 写盘失败：回到上一次保存的框
        setInnerBox(info.inner, true);
        setInnerMeta(info.inner);
      }
    },
    onCancel: () => {
      setInnerBox(info.inner, true);
      setInnerMeta(info.inner);
    },
  });
  for (const b of document.querySelectorAll("#joy .jb")) {
    let timer = null;
    const step = () => post("nudge", { dyaw: +b.dataset.dy, dpitch: +b.dataset.dp });
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
    const usable = r.result && r.result !== "no";
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
  const gate = (info.gates || {}).generate || {};
  if (!gate.ready) {
    alert(gate.reason || "当前模式还不能提交；先补齐素材或抽取版本");
    return;
  }
  const body = {
    mode: ($("genMode") || {}).value || "",
    aspect_ratio: ($("genAspect") || {}).value || "",
    resolution: ($("genResolution") || {}).value || "",
    scale: ($("genScale") || {}).value || "",
  };
  const duration = ($("genDuration") || {}).value || "auto";
  if (duration !== "auto") body.duration = +duration;
  await run(dryRun ? "dry_run" : "submit", body);
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

let outProxyPoll = null;
let outProxyFile = null;

function _cmpSetSrc(v, url) {
  if (!url || v.dataset.want === url) return;
  v.dataset.want = url;
  const t = isFinite(v.currentTime) ? v.currentTime : 0;
  const playing = !v.paused && !v.ended;
  v.src = url;
  v.addEventListener("loadedmetadata", () => {
    if (t > 0 && isFinite(v.duration) && v.duration > 0)
      v.currentTime = Math.min(t, v.duration);
    if (playing) v.play().catch(() => {});
  }, { once: true });
}

function _updateOutProxyHint(out) {
  const el = $("cmpProxyHint");
  if (!el) return;
  if (!out) { el.textContent = ""; return; }
  const st = out.proxy_state;
  if (st === "ready")
    el.textContent = "右侧：4K 预览代理（与交付文件同帧率、同帧数）";
  else if (st === "disabled")
    el.textContent = "右侧：原片本身就是浏览器可播的 4K 以内文件";
  else if (st === "building")
    el.textContent = "右侧：正在生成 4K 预览代理（首次约 10 秒），当前先用原片";
  else if (st === "failed")
    el.textContent = "右侧：4K 预览生成失败，继续用原片；详见服务日志";
  else
    el.textContent = "右侧：准备 4K 预览代理…";
}

/** 成片代理缺失时触发一次构建；就绪后把右侧切到代理并保留进度。 */
async function ensureOutProxy(out) {
  if (!out || !out.file) return;
  if (outProxyFile === out.file && outProxyPoll) return;
  clearInterval(outProxyPoll);
  outProxyPoll = null;
  outProxyFile = out.file;
  const tick = async () => {
    try {
      const r = await fetch(`/api/case/${encodeURIComponent(CASE)}/proxy`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ file: out.file }),
      });
      const d = await r.json();
      if (!r.ok) {
        clearInterval(outProxyPoll); outProxyPoll = null;
        out.proxy_state = "failed";
        _updateOutProxyHint(out);
        return;
      }
      out.proxy_state = d.state;
      _updateOutProxyHint(out);
      if (d.state === "ready") {
        clearInterval(outProxyPoll); outProxyPoll = null;
        const sel = $("outSel");
        if (sel && sel.value === out.file) _cmpSetSrc($("cmpB"), out.proxy_url);
      } else if (d.state === "disabled") {
        clearInterval(outProxyPoll); outProxyPoll = null;
        const sel = $("outSel");
        if (sel && sel.value === out.file) _cmpSetSrc($("cmpB"), out.url);
      } else if (d.state === "failed") {
        clearInterval(outProxyPoll); outProxyPoll = null;
      }
    } catch (_) { /* 网络间隙继续轮询 */ }
  };
  outProxyPoll = setInterval(tick, 2000);
  await tick();
}

function renderCompare() {
  if (FLOW === "gen") return;
  gateText("compare", "cmpGate");
  const sel = $("outSel");
  const files = (info.outputs || []).map(o => o.file);
  const keep = sel.value;
  sel.innerHTML = (info.outputs || []).map(o =>
    `<option value="${o.file}">${o.file}${o.partial ? "（仅中间段）" : ""}</option>`
  ).join("");
  if (files.includes(keep)) sel.value = keep;
  if (files.length) loadCompare();
}

function loadCompare() {
  const f = $("outSel").value;
  const out = (info.outputs || []).find(o => o.file === f);
  if (!out) return;
  const a = $("cmpA"), b = $("cmpB");
  // 左路：源片代理就绪就用代理，否则先用原片（ensureProxy 就绪后会重新进来）。
  _cmpSetSrc(a, videoUrl());
  // 右路：有 4K 代理就用代理；没有先播原片并触发后台构建。
  _cmpSetSrc(b, out.proxy_state === "ready" ? out.proxy_url : out.url);
  _updateOutProxyHint(out);
  if (out.proxy_state !== "ready" && out.proxy_state !== "disabled") {
    ensureOutProxy(out);
  }
  if (info.proxy && (info.proxy.state === "missing"
      || info.proxy.state === "building")) {
    ensureProxy();
  }
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
