/** wizard.js —— 软向导的流程骨架：步骤条、pick/extract/生成/合并/对比。
 *
 * 公共状态与请求在 common.js；素材、参数/会话、提示词、任务分别在
 * assets.js / params.js / prompt.js / tasks.js。门控判据全部来自服务端
 * （info.gates），前端只渲染 ok/reason，不自算。
 */
"use strict";

const FLOW_STEPS = {
  patch: [["pick", "选区"], ["extract", "抽取"], ["keyframe", "首尾帧"],
          ["generate", "生成"], ["merge", "合并"], ["compare", "对比"]],
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
  document.body.classList.toggle("gen-step", id === "generate");
  for (const [sid] of STEPS) {
    const panel = $("pnl-" + sid);
    if (panel) panel.classList.toggle("cur", sid === id);
  }
  renderSteps();
  syncSubmitBarHeight();
  if (id === "generate") renderSubmitBar();
  if (id === "keyframe") {
    if (typeof loadKeyframePrompts === "function") loadKeyframePrompts();
    if (typeof loadKeyframeRounds === "function") loadKeyframeRounds();
  }
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

/** 几何交互的轻量应答：只合并 viewport / inner / 预览 URL，不重扫素材。 */
async function fetchGeometry(path, body) {
  try {
    const r = await fetch(`/api/case/${encodeURIComponent(CASE)}/${path}`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
    if (r.status === 404) { location.href = "/"; return null; }
    const d = await r.json();
    if (!r.ok) { alert(d.error || r.statusText); return null; }
    return d;
  } catch (_) {
    return null;
  }
}

function applyGeometry(d) {
  if (!d) return;
  if (d.viewport) info.viewport = d.viewport;
  if (d.inner) info.inner = d.inner;
  if (d.previews) info.previews = d.previews;
  if (d.viewport_footprint) info.viewport_footprint = d.viewport_footprint;
  renderPick();
}

/** 视口在 ERP 上的示意框；坐标是 ERP 的 0..1 分数。 */
function drawFootprint(rects) {
  const boxes = document.querySelectorAll("#vpFoots .vpFoot");
  const video = $("erpVid"), wrap = $("erpWrap");
  if (!boxes.length || !video || !wrap) return;
  const vr = video.getBoundingClientRect();
  const wr = wrap.getBoundingClientRect();
  const list = rects || [];
  for (let i = 0; i < boxes.length; i++) {
    const box = boxes[i], r = list[i];
    if (!r || !vr.width || !vr.height) {
      box.style.display = "none";
      continue;
    }
    box.style.left = (vr.left - wr.left + r.x * vr.width) + "px";
    box.style.top = (vr.top - wr.top + r.y * vr.height) + "px";
    box.style.width = (r.w * vr.width) + "px";
    box.style.height = (r.h * vr.height) + "px";
    box.style.display = "block";
  }
}

/** 视口尺寸下拉：档位由 info.viewport_options 决定，只有一档时禁用。 */
function renderViewportSize() {
  const sel = $("vpSize");
  if (!sel) return;
  const vp = (info && info.viewport) || {};
  const opts = (info && info.viewport_options) || [];
  const sig = JSON.stringify(opts.map(o => [o.width, o.height, o.label]));
  if (sel.dataset.sig !== sig) {
    sel.dataset.sig = sig;
    sel.innerHTML = "";
    for (const o of opts) {
      const el = document.createElement("option");
      el.value = `${o.width}x${o.height}`;
      el.dataset.width = o.width;
      el.dataset.height = o.height;
      el.textContent = o.label || `${o.width}×${o.height}`;
      sel.appendChild(el);
    }
    sel.disabled = opts.length < 2;
  }
  if (vp.width && vp.height) {
    const value = `${vp.width}x${vp.height}`;
    if ([...sel.options].some(o => o.value === value)) sel.value = value;
  }
}

async function changeViewportSize() {
  const sel = $("vpSize");
  const opt = sel && sel.selectedOptions[0];
  if (!opt) return;
  applyGeometry(await fetchGeometry("viewport-size", {
    width: +opt.dataset.width, height: +opt.dataset.height,
  }));
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
  drawFootprint(info.viewport_footprint);
  renderViewportSize();
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
  applyGeometry(await fetchGeometry("viewport", { fx: p.frac.x, fy: p.frac.y }));
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
    drawFootprint((info && info.viewport_footprint) || []);
  });
  window.addEventListener("resize", () => {
    drawFootprint((info && info.viewport_footprint) || []);
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
      const d = await fetchGeometry("inner", { fx: r.frac.x, fy: r.frac.y,
                                               fw: r.frac.w, fh: r.frac.h });
      if (seq !== innerSeq) return;
      if (!d) {                       // 写盘失败：回到上一次保存的框
        setInnerBox(info.inner, true);
        setInnerMeta(info.inner);
      } else {
        applyGeometry(d);
      }
    },
    onCancel: () => {
      setInnerBox(info.inner, true);
      setInnerMeta(info.inner);
    },
  });
  for (const b of document.querySelectorAll("#joy .jb")) {
    let timer = null;
    const step = async () => {
      applyGeometry(await fetchGeometry(
        "nudge", { dyaw: +b.dataset.dy, dpitch: +b.dataset.dp }));
    };
    b.addEventListener("mousedown", () => { step(); timer = setInterval(step, 100); });
    window.addEventListener("mouseup", () => clearInterval(timer));
    b.addEventListener("mouseleave", () => clearInterval(timer));
  }
}

async function saveFrames() {
  await post("draft", { frames: { start: +$("fStart").value, end: +$("fEnd").value } });
}

async function resetDraft() {
  applyGeometry(await fetchGeometry("reset-draft", {}));
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

function _roundSource(r) {
  return { mglc: "Seedance", manual: "外部视频" }[r.backend] || r.backend || "";
}

function _fmtRoundTime(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return iso;
  const p = (x) => String(x).padStart(2, "0");
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ` +
         `${p(d.getHours())}:${p(d.getMinutes())}`;
}

function _roundsSorted() {
  return ((info && info.rounds && info.rounds.rounds) || []).slice()
    .sort((a, b) => String(b.updated || "").localeCompare(String(a.updated || "")));
}

/** patch 看当前抽取版本有没有结果；fullredraw/gen 看 case/result.mp4。 */
function _targetPresent() {
  if (!info) return false;
  if (info.flow === "patch") {
    const cur = (info.extracts || []).find(v => v.version === info.extract_current);
    return !!(cur && cur.has_result);
  }
  return !!(((info.gates || {}).generate) || {}).ok;
}

/** 最近成片：只在需要采用或最近一次失败时占一行。 */
function renderResultStrip() {
  const el = $("resultStrip");
  if (!el) return;
  el.className = "resultStrip";
  el.innerHTML = "";
  const rows = _roundsSorted();
  const latest = rows.find(r => r.result === "yes");
  const adopted = (info && info.adopt_round) || "";
  if (latest) {
    const t = _fmtRoundTime(latest.updated);
    if (latest.round === adopted && _targetPresent()) {
      // 已经采用且目标文件在：没有可执行的动作，整行不显示。
      el.style.display = "none";
      return;
    }
    const label = document.createElement("span");
    const strong = document.createElement("b");
    strong.textContent = "成片已生成";
    label.append(strong, ` · ${t} · ${_roundSource(latest)}`);
    const btn = document.createElement("button");
    btn.textContent = "选用成片";
    btn.onclick = () => run("adopt", { round: latest.round_dir });
    el.append(label, btn);
    el.style.display = "flex";
    return;
  }
  const meaningful = rows.find(r => r.stage !== "prepared");
  if (meaningful && meaningful.stage === "failed") {
    el.classList.add("failed");
    el.append(`上次生成失败 · ${_fmtRoundTime(meaningful.updated)} · ` +
              `失败原因见页面底部任务记录`);
    el.style.display = "flex";
    return;
  }
  el.style.display = "none";
}

// ── 预演状态与提交条 ───────────────────────────────────────────────────

// sig = 点击预演那一刻的页面输入快照；state: none | pending | ok | failed
let preview = { sig: null, state: "none" };
let submitBarObserver = null;

function _selectValue(id) {
  const el = $(id);
  return el ? el.value : "";
}

/** 页面可控输入的签名；不试图复刻服务端的 run_key。 */
function submissionSignature() {
  if (!info) return null;
  const sel = info.submit_selected || [];
  const assets = (info.assets || [])
    .filter(a => sel.includes(a.id))
    .map(a => [a.id, a.role, a.mtime, a.size]);
  const p = info.prompt || {};
  return {
    assets: JSON.stringify(assets),
    params: JSON.stringify([
      _selectValue("genMode"), _selectValue("genAspect"),
      _selectValue("genResolution"), _selectValue("genScale"),
      _selectValue("genDuration"),
    ]),
    prompt: JSON.stringify([p.template || "", p.vars || {},
                            p.rendered || p.render_error || ""]),
    quota: JSON.stringify([
      _selectValue("sessProject"), _selectValue("sessEpisode"),
      _selectValue("sessSession"),
    ]),
  };
}

const SIG_LABELS = { assets: "素材", params: "参数", prompt: "提示词", quota: "扣费额度" };

function _sigChanges(before, now) {
  if (!before || !now) return [];
  return Object.keys(SIG_LABELS)
    .filter(k => before[k] !== now[k])
    .map(k => SIG_LABELS[k]);
}

function _previewStatus(now) {
  if (!preview.sig) return null;
  if (preview.state === "pending") return { text: "预演中…", cls: "" };
  if (preview.state === "failed") {
    return { text: "✗ 预演未通过 · 详见任务记录", cls: "bad" };
  }
  const changed = _sigChanges(preview.sig, now);
  if (changed.length) {
    return { text: `预演已过期 · ${changed.join("、")}有改动`, cls: "bad" };
  }
  return { text: "✓ 预演通过 · 当前选择未改动", cls: "ok" };
}

function _barStatusText() {
  const gp = (info && info.generation_params) || {};
  if (gp.error) return { text: "生成参数暂时读不到：" + gp.error, cls: "bad" };
  const gate = ((info && info.gates) || {}).generate || {};
  if (!gate.ready) return { text: gate.reason || "还不能提交", cls: "bad" };
  const hint = $("genParamsHint");
  if (hint && hint.textContent.trim()) {
    return { text: hint.textContent.trim(), cls: "bad" };
  }
  const pv = _previewStatus(submissionSignature());
  if (pv) return pv;
  const n = ((info && info.submit_selected) || []).length;
  return { text: `素材 ${n} 项`, cls: n ? "ok" : "bad" };
}

function renderSubmitBar() {
  const bar = $("submitBar");
  if (!bar) return;
  const fixed = $("genFixed"), label = $("settingsLabel");
  if (label && fixed) label.title = fixed.textContent || "";
  const gate = ((info && info.gates) || {}).generate || {};
  const ready = !!gate.ready;
  for (const id of ["btnDryRun", "btnSubmit"]) {
    const btn = $(id);
    if (btn) btn.disabled = !ready;
  }
  const st = $("barStatus");
  if (st) {
    const s = _barStatusText();
    st.textContent = s.text;
    st.className = "barStatus " + (s.cls || "");
    st.title = s.text;
  }
}

function syncSubmitBarHeight() {
  const bar = $("submitBar");
  const h = bar && bar.offsetHeight ? bar.offsetHeight : 0;
  document.documentElement.style.setProperty("--submit-bar-h", h + "px");
}

function setupSubmitBar() {
  const bar = $("submitBar");
  if (!bar) return;
  if (window.ResizeObserver) {
    submitBarObserver = new ResizeObserver(syncSubmitBarHeight);
    submitBarObserver.observe(bar);
  }
  window.addEventListener("resize", syncSubmitBarHeight);
  syncSubmitBarHeight();
}

/** 任务队列收尾：把 pending 的预演标记成通过/失败，下一次渲染再比对签名。 */
function onTasksSettled(s) {
  const recent = (s && s.last) || [];
  if (recent.some(h => h.kind === "keyframe" && h.case === CASE)
      && typeof loadKeyframeRounds === "function") {
    loadKeyframeRounds();
  }
  if (!preview.sig || preview.state !== "pending") return;
  const last = recent.slice().reverse()
    .find(h => h.kind === "dry_run" && h.case === CASE);
  if (!last) return;
  preview.state = last.state === "done" ? "ok" : "failed";
}

async function runSubmit(dryRun = false) {
  const gate = ((info && info.gates) || {}).generate || {};
  if (!gate.ready) return;
  if (typeof flushPromptSaves === "function" && !(await flushPromptSaves())) return;
  const sig = submissionSignature();
  const body = {
    mode: _selectValue("genMode"),
    aspect_ratio: _selectValue("genAspect"),
    resolution: _selectValue("genResolution"),
    scale: _selectValue("genScale"),
  };
  const duration = _selectValue("genDuration") || "auto";
  if (duration !== "auto") body.duration = +duration;
  if (dryRun) preview = { sig, state: "pending" };
  else preview = { sig: null, state: "none" };
  renderSubmitBar();
  const d = await run(dryRun ? "dry_run" : "submit", body);
  if (!d && dryRun) {
    preview = { sig: null, state: "none" };
    renderSubmitBar();
  }
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
setupSubmitBar();
loadInfo().then(() => showStep(STEPS[0][0]));
setInterval(pollTasks, 2000);
pollTasks();
