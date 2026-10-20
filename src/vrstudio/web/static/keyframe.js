/** keyframe.js —— 关键帧工作台：抽帧、两步图片重绘、候选挑选。
 *
 * 图片任务走 vrgen image submit，由 web 队列串行执行；候选结果从
 * /keyframe/rounds 读取，挑选时调 vrgen result --index 再写入素材列表。
 */
"use strict";

function _kfStep() {
  return (($("kfStep") || {}).value) || "first";
}

function _kfDefaultRoles(step) {
  return step === "first"
    ? ["first_frame_raw", "subject_ref"]
    : ["first_frame", "last_frame_raw", "subject_ref"];
}

function renderKeyframe() {
  const box = $("kfAssets");
  if (!box) return;
  const assets = ((info && info.assets) || []).filter(a => a.kind === "image");
  const step = _kfStep();
  const wanted = _kfDefaultRoles(step);
  box.innerHTML = "";
  if (!assets.length) {
    box.innerHTML = '<span class="meta">（还没有图片素材；先把三视图或抽取帧加入素材）</span>';
    return;
  }
  for (const asset of assets) {
    const label = document.createElement("label");
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.value = asset.id;
    cb.checked = wanted.includes(asset.role);
    label.append(cb, document.createTextNode(
      `${asset.label} · ${asset.role}`));
    box.appendChild(label);
  }
  if (!box.dataset.loaded) {
    box.dataset.loaded = "1";
    loadKeyframeRounds();
  }
}

function _kfSelected() {
  return [...document.querySelectorAll("#kfAssets input:checked")].map(cb => cb.value);
}

async function runKeyframe() {
  const prompt = (($("kfPrompt") || {}).value || "").trim();
  if (!prompt) { alert("先填写这一帧的提示词"); return; }
  const assets = _kfSelected();
  if (!assets.length) { alert("至少勾选 1 张参考图"); return; }
  const body = { step: _kfStep(), prompt, assets };
  const model = (($("kfModel") || {}).value || "").trim();
  if (model) body.model_id = +model;
  const resolution = ($("kfResolution") || {}).value;
  if (resolution) body.resolution = resolution;
  const r = await fetch(`/api/case/${encodeURIComponent(CASE)}/keyframe/run`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const d = await r.json();
  if (!r.ok) { alert(d.error || r.statusText); return; }
  $("kfHint").className = "meta ok";
  $("kfHint").textContent = "图片任务已进入队列；完成后点「刷新候选」挑选。";
}

async function loadKeyframeRounds() {
  const el = $("kfRounds");
  if (!el) return;
  const r = await fetch(`/api/case/${encodeURIComponent(CASE)}/keyframe/rounds`);
  const d = await r.json();
  if (!r.ok) { el.textContent = d.error || r.statusText; return; }
  const rounds = d.rounds || [];
  el.innerHTML = "";
  if (!rounds.length) {
    el.innerHTML = '<span class="meta">还没有图片轮次</span>';
    return;
  }
  for (const round of rounds) {
    const card = document.createElement("div");
    card.className = "kfCard";
    const title = document.createElement("b");
    title.textContent = `${round.round} · ${round.stage}`;
    card.appendChild(title);
    const grid = document.createElement("div");
    grid.className = "kfGrid";
    for (const result of round.results || []) {
      const item = document.createElement("div");
      const img = document.createElement("img");
      img.src = result.url + "?v=" + Date.now();
      img.title = "候选 " + result.index;
      const first = document.createElement("button");
      first.textContent = "设为首帧";
      first.onclick = () => pickKeyframe(round.round, result.index, "first_frame");
      const last = document.createElement("button");
      last.textContent = "设为尾帧";
      last.onclick = () => pickKeyframe(round.round, result.index, "last_frame");
      item.append(img, first, last);
      grid.appendChild(item);
    }
    if (!(round.results || []).length) {
      const empty = document.createElement("span");
      empty.className = "meta";
      empty.textContent = "还没有候选图（任务可能仍在运行）";
      grid.appendChild(empty);
    }
    card.appendChild(grid);
    el.appendChild(card);
  }
}

async function pickKeyframe(round, index, role) {
  const r = await fetch(`/api/case/${encodeURIComponent(CASE)}/keyframe/pick`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ round, index, role }),
  });
  const d = await r.json();
  if (!r.ok) { alert(d.error || r.statusText); return; }
  info = d;
  renderAll();
  loadKeyframeRounds();
}

async function extractFrames() {
  const r = await fetch(`/api/case/${encodeURIComponent(CASE)}/extract-frames`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({}),
  });
  const d = await r.json();
  if (!r.ok) { alert(d.error || r.statusText); return; }
  info = d;
  renderAll();
}
