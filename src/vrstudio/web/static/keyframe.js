/** keyframe.js —— 关键帧工作台：两步图片重绘与候选挑选。
 *
 * 输入底图来自当前 extract 导出的 first_frame_raw.png / last_frame_raw.png
 * （决策 33：抽帧已并入 extract，不再单独点按钮）；其余参考图是 [[assets]]
 * 里的图片。图片任务走 vrgen image submit，由 web 队列串行执行；候选结果从
 * /keyframe/rounds 读取，挑选时调 vrgen result --index 再写入素材列表。
 */
"use strict";

const KF_PROMPTS = {
  first: "对图1做局部编辑，只改指定区域，其余像素尽量保持。\n" +
    "【替换目标】把图1中偏右、矮案后的白色占位人形替换成图2定义的人物。" +
    "这个白色人形是下沉摆放的占位模型，它表示“有一位老人坐在这张案后”，不是站立——" +
    "请画成真实的坐姿：老年男性端坐在矮案后，上身挺直，头与肩露出案沿上方，" +
    "双手向前自然放在案面上，宽袖垂在案侧，下半身被案面与袍摆完全遮挡。\n" +
    "【人物外观】严格按图2：老年男性古代高官，清瘦，花白长须，黑色高冠与金红绶带，" +
    "黑底暗红云纹宽袖深衣，腰间革带玉佩，黑色翘头履。只取图2的脸、胡须、冠帽、服装与配色，" +
    "不取它的背景与站姿。\n" +
    "【位置锁定】人物的左右位置、远近与大小和白色人形一致，头顶不超过白色人形头顶太多，" +
    "脚部被案遮挡。\n" +
    "【保持不变】机位、构图、透视、宫殿建筑、红毯、矮案与案上器物、背景就座人物、" +
    "右侧黑袍人物、光照与色温全部保持图1原样。\n" +
    "【界面清理】去掉画面顶部的编辑器工具栏和右下角的界面文字，去掉后按周围画面自然补全；" +
    "除此之外不新增、不删除任何物体，画面里只能有这位高官一个主角，不能残留白色人形。\n" +
    "【画质】写实古装剧质感，人物光效与场景一致，暗色大厅里脸与长须能辨认。",
  last: "对图1做局部编辑，只改指定区域，其余像素尽量保持。\n" +
    "【替换目标】把图1中央红毯上的白色占位人形（一个站立的占位模型，只表示位置与大小）" +
    "替换成人物，画成站立待命姿势：老年男性双脚并拢站在红毯上，双手自然垂在身前，" +
    "面朝画面右侧的朝堂，身体放松，只有衣料静止。\n" +
    "【人物外观与一致性】人物必须与图2、图3是同一个人：脸型、花白长须、黑色高冠、" +
    "金红绶带、黑底暗红云纹深衣、革带玉佩完全一致，图3是他在同一场景另一张画面里的样子，" +
    "以图2和图3共同锁定身份，不得画成另一个人。\n" +
    "【位置锁定】人物的左右位置、远近、大小与脚底落点和白色人形完全一致，" +
    "站姿高度与白色人形接近。\n" +
    "【保持不变】机位、构图、透视、宫殿建筑、红毯、成排矮案与案上器物、背景就座人物、" +
    "右侧黑袍人物、光照与色温全部保持图1原样。\n" +
    "【界面清理】去掉画面顶部的编辑器工具栏和右下角的界面文字，去掉后按周围画面自然补全；" +
    "除此之外不新增、不删除任何物体，画面里只能有这位高官一个主角，不能残留白色人形。\n" +
    "【画质】写实古装剧质感，与图3同一光照与色调。",
};

function _kfStep() {
  return (($("kfStep") || {}).value) || "first";
}

function _kfDefaultIds(step) {
  // 首帧：原始首帧 + 所有主体参考图；尾帧：首帧定稿图 + 原始尾帧 + 主体参考图。
  const raw = step === "first" ? "first_raw" : "last_raw";
  const ids = [raw];
  if (step === "last") ids.push("first_frame");
  const roles = step === "first" ? ["subject_ref"] : ["subject_ref"];
  for (const asset of ((info && info.assets) || [])) {
    if (asset.source !== "flow" && asset.kind === "image" && roles.includes(asset.role)) {
      ids.push(asset.id);
    }
  }
  return ids;
}

function renderKeyframe() {
  const box = $("kfAssets");
  if (!box) return;
  if (FLOW !== "patch") return;
  const rawFrames = ((info && info.raw_frames) || []).filter(f => f.exists);
  const assets = ((info && info.assets) || [])
    .filter(a => a.kind === "image" && a.source !== "flow");
  const step = _kfStep();
  const wanted = _kfDefaultIds(step);
  const promptBox = $("kfPrompt");
  if (promptBox && promptBox.dataset.step !== step) {
    promptBox.value = KF_PROMPTS[step] || "";
    promptBox.dataset.step = step;
  }
  box.innerHTML = "";
  if (!rawFrames.length) {
    box.innerHTML = '<span class="meta">（还没有首尾原始帧；先完成 extract，' +
      '它会和 clip 一起产出）</span>';
    return;
  }
  const addRow = (id, label) => {
    const el = document.createElement("label");
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.value = id;
    cb.checked = wanted.includes(id);
    el.append(cb, document.createTextNode(label));
    box.appendChild(el);
  };
  for (const frame of rawFrames) addRow(frame.id, ` ${frame.label}（extract 产出）`);
  for (const asset of assets) addRow(asset.id, ` ${asset.label} · ${asset.role}`);
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
      img.onclick = () => window.open(result.url, "_blank");
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
