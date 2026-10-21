/** assets.js —— 素材与本次提交集：候选卡片、勾选、角色、排序与删除。
 *
 * 候选 = 流程母版（patch 的 clip / fullredraw 的 pad，只读）+ [[assets]] 文件素材。
 * mask 与首尾原始帧不是候选，不出现在这里。提交集只认勾选（决策 33）：没有
 * 默认集，勾选才提交；勾选结果即时写 [submit] selected。
 */
"use strict";

const ASSET_ROLE_LABELS = {
  edit_master: "编辑母版（时间轴、构图与动作基准）",
  motion_ref: "动作参考（只提供动作节奏与路径）",
  space_ref: "空间参考（只提供机位、构图与起终点位置）",
  scene_ref: "场景参考（只提供构图、背景与光照）",
  subject_ref: "主体参考（只定义角色外观）",
  first_frame: "首帧定稿图（视频第一帧）",
  last_frame: "尾帧定稿图（视频最后一帧）",
  mid_frame: "中间过渡帧（预留）",
};

// 用户能给文件素材选的角色；mask 与 *_raw 由流程产物/工作台提供。
const USER_ROLES = Object.keys(ASSET_ROLE_LABELS);

let assetPick = { dir: null, selected: new Map() };   // path -> role

function _currentMode() {
  const gp = (info && info.generation_params) || {};
  return ((gp.current || {}).mode) || "";
}

function _selectedIds() {
  return new Set((info && info.submit_selected) || []);
}

function _canEditAssets() {
  return true;      // 三条流程都可增删改排（决策 33）
}

function _fileAssets() {
  return ((info && info.assets) || []).filter(a => a.source !== "flow");
}

function _flowAssets() {
  return ((info && info.assets) || []).filter(a => a.source === "flow");
}

async function saveSubmitSelection() {
  const ids = [...document.querySelectorAll("#assetList input[type=checkbox]:checked")]
    .map(cb => cb.dataset.id);
  const r = await fetch(`/api/case/${encodeURIComponent(CASE)}/submit`, {
    method: "PUT", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ selected: ids }),
  });
  const d = await r.json();
  if (!r.ok) { alert(d.error || r.statusText); return; }
  info = d;
  renderAll();
}

function _setAllSelected(on) {
  const boxes = [...document.querySelectorAll("#assetList input[type=checkbox]")];
  boxes.forEach(cb => { cb.checked = on; });
  saveSubmitSelection();
}

function _invertSelection() {
  const boxes = [...document.querySelectorAll("#assetList input[type=checkbox]")];
  boxes.forEach(cb => { cb.checked = !cb.checked; });
  saveSubmitSelection();
}

function _kindLabel(kind) {
  return kind === "video" ? "视频" : "图片";
}

function _fmtDuration(sec) {
  return sec == null ? "" : `${Number(sec).toFixed(2)}s`;
}

function _roleSelect(asset) {
  const sel = document.createElement("select");
  const roles = USER_ROLES.includes(asset.role) ? USER_ROLES : [asset.role, ...USER_ROLES];
  for (const role of roles) {
    const o = document.createElement("option");
    o.value = role;
    o.textContent = ASSET_ROLE_LABELS[role] || role;
    sel.appendChild(o);
  }
  sel.value = asset.role;
  sel.onchange = () => post("assets", { op: "role", id: asset.id, role: sel.value });
  return sel;
}

function _checkBox(asset) {
  const check = document.createElement("input");
  check.type = "checkbox";
  check.dataset.id = asset.id;
  check.checked = _selectedIds().has(asset.id);
  check.title = "勾选后参与本次提交";
  check.onchange = saveSubmitSelection;
  return check;
}

function _assetCard(asset, index, fileAssets) {
  const flow = asset.source === "flow";
  const card = document.createElement("div");
  card.className = "assetCard" + (asset.exists ? "" : " bad") + (flow ? " flowAsset" : "");
  card.draggable = !flow;
  card.dataset.id = asset.id;

  const img = document.createElement("img");
  img.src = asset.url + "?v=" + (asset.mtime || 0);
  img.title = "打开原文件：" + (asset.rel || asset.path);
  img.onclick = () => window.open(asset.url, "_blank");

  const meta = document.createElement("div");
  meta.className = "ameta";
  const size = asset.exists
    ? `${asset.width || "?"}×${asset.height || "?"}` +
      (asset.kind === "video" ? ` · ${_fmtDuration(asset.duration)} · ${asset.fps || "?"}fps` : "")
    : "文件不存在";
  const tag = flow ? "流程产物 · " : "";
  meta.innerHTML = `<b>${asset.label} · ${asset.id}</b> · ${tag}${_kindLabel(asset.kind)} · ${size}<br>` +
    `<span class="dim">${asset.rel || asset.path}</span>`;

  const roleEl = asset.role_fixed
    ? Object.assign(document.createElement("span"), {
        className: "meta",
        textContent: ASSET_ROLE_LABELS[asset.role] || asset.role,
      })
    : _roleSelect(asset);

  let moves = null;
  let del = null;
  if (!flow) {
    moves = document.createElement("span");
    const up = document.createElement("button");
    up.textContent = "↑"; up.title = "上移"; up.disabled = index === 0;
    up.onclick = () => _moveAsset(fileAssets, index, -1);
    const down = document.createElement("button");
    down.textContent = "↓"; down.title = "下移";
    down.disabled = index === fileAssets.length - 1;
    down.onclick = () => _moveAsset(fileAssets, index, 1);
    moves.append(up, down);

    del = document.createElement("button");
    del.textContent = "删除";
    del.onclick = () => {
      if (!confirm(`删除素材 ${asset.label}？文件也会从 case 的 assets/ 里删除。`)) return;
      post("assets", { op: "remove", id: asset.id });
    };

    card.addEventListener("dragstart", (e) => {
      card.classList.add("dragging");
      e.dataTransfer.setData("text/plain", asset.id);
    });
    card.addEventListener("dragend", () => card.classList.remove("dragging"));
    card.addEventListener("dragover", (e) => e.preventDefault());
    card.addEventListener("drop", (e) => {
      e.preventDefault();
      const from = e.dataTransfer.getData("text/plain");
      if (!from || from === asset.id) return;
      const ids = fileAssets.map(a => a.id);
      const fromIndex = ids.indexOf(from);
      const toIndex = ids.indexOf(asset.id);
      if (fromIndex < 0 || toIndex < 0) return;
      ids.splice(toIndex, 0, ids.splice(fromIndex, 1)[0]);
      post("assets", { op: "reorder", ids });
    });
  }

  card.append(_checkBox(asset), img, meta, roleEl);
  if (moves) card.append(moves, del);
  return card;
}

function _moveAsset(fileAssets, index, delta) {
  const ids = fileAssets.map(a => a.id);
  const to = index + delta;
  if (to < 0 || to >= ids.length) return;
  [ids[index], ids[to]] = [ids[to], ids[index]];
  post("assets", { op: "reorder", ids });
}

function _renderDerived() {
  const el = $("derivedList");
  if (!el) return;
  const rows = (info && info.derived) || [];
  el.innerHTML = "";
  if (!rows.length) {
    el.innerHTML = '<span class="meta">（还没有流程产物）</span>';
    return;
  }
  for (const row of rows) {
    const div = document.createElement("div");
    div.className = "derivedRow" + (row.exists ? "" : " bad");
    const name = document.createElement("b");
    name.textContent = row.label;
    const meta = document.createElement("span");
    meta.textContent = `${row.name || ""} · ${row.submit || "只读"}`;
    div.append(name, meta);
    el.appendChild(div);
  }
}

function _renderSubmitHint() {
  const el = $("submitHint");
  if (!el) return;
  const mode = _currentMode();
  const count = _selectedIds().size;
  const head = `本次提交 ${count} 项（勾选即提交，没有默认集）。`;
  if (mode === "firstLastFrame") {
    el.textContent = head +
      " firstLastFrame：只需要勾选恰好一张首帧定稿图与一张尾帧定稿图，" +
      "流程视频与 mask 都不参与。";
  } else if (mode === "allMediaRef") {
    el.textContent = head + " allMediaRef：勾选的素材会全部进入本次清单；" +
      "区域标注 mask 由流程自动附加，不占 @ImageN。";
  } else {
    el.textContent = head + " 视频按勾选顺序编 @VideoN，图片编 @ImageN；" +
      "mask 由流程自动附加在图片末尾，不参与编号。";
  }
}

function renderAssets() {
  _renderDerived();
  _renderSubmitHint();
  const el = $("assetList");
  if (!el) return;
  const flowAssets = _flowAssets();
  const fileAssets = _fileAssets();
  el.innerHTML = "";
  if (!flowAssets.length && !fileAssets.length) {
    el.innerHTML = '<span class="meta">还没有素材。gen 可以只用图片；' +
      'patch/fullredraw 的 clip 与 pad 会在对应步骤完成后出现在这里。</span>';
  } else {
    // 流程母版排在前面，勾选顺序即编号顺序（@Video1 是母版）。
    flowAssets.forEach(asset => el.appendChild(_assetCard(asset, 0, fileAssets)));
    fileAssets.forEach((asset, index) => el.appendChild(_assetCard(asset, index, fileAssets)));
  }
  const addRow = $("assetAddRow");
  if (addRow) addRow.style.display = _canEditAssets() ? "" : "none";
  const hint = $("assetHint");
  if (hint) {
    const all = flowAssets.concat(fileAssets);
    const videos = all.filter(a => a.kind === "video").length;
    const images = all.filter(a => a.kind === "image").length;
    const mode = _currentMode();
    hint.textContent = all.length
      ? `当前模式 ${mode || "未设置"} · ${videos} 个视频 · ${images} 张图片 · ` +
        `已勾选 ${_selectedIds().size} 项`
      : "先添加素材，再回到「生成」步骤";
    hint.className = "meta " + ((videos || images) ? "ok" : "bad");
  }
}

// ── 本地文件多选 ─────────────────────────────────────────────────────────

function openAssetBrowse() {
  const box = $("assetBrowseBox");
  if (!box) return;
  assetPick = { dir: null, selected: new Map() };
  $("abSelected").innerHTML = "";
  box.style.display = "block";
  browseAssets("E:\\VRAIGC");
}

function closeAssetBrowse() {
  const box = $("assetBrowseBox");
  if (box) box.style.display = "none";
}

async function browseAssets(path) {
  const r = await fetch("/api/browse?kinds=assets&path=" + encodeURIComponent(path));
  const d = await r.json();
  if (!r.ok) { $("abPath").textContent = d.error; return; }
  assetPick.dir = d.path;
  $("abPath").textContent = d.path;
  const el = $("abList");
  el.innerHTML = "";
  const addDir = (label, target) => {
    const it = document.createElement("div");
    it.className = "abitem";
    it.textContent = "📁 " + label;
    it.style.cursor = "pointer";
    it.onclick = () => browseAssets(target);
    el.appendChild(it);
  };
  if (d.parent) addDir("..", d.parent);
  for (const dir of d.dirs) addDir(dir, d.path + "\\" + dir);
  for (const f of d.files) {
    const row = document.createElement("div");
    row.className = "abitem";
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.checked = assetPick.selected.has(f.path);
    cb.onchange = () => {
      if (cb.checked) assetPick.selected.set(f.path, "");
      else assetPick.selected.delete(f.path);
      renderAssetPicked();
    };
    const label = document.createElement("span");
    label.textContent = f.name;
    const kind = document.createElement("span");
    kind.className = "kind";
    kind.textContent = f.kind === "video" ? "视频" : "图片";
    row.append(cb, label, kind);
    el.appendChild(row);
  }
  if (!d.files.length && !d.dirs.length) {
    el.innerHTML = '<span class="meta">这个目录里没有视频或图片</span>';
  }
}

function renderAssetPicked() {
  const el = $("abSelected");
  el.innerHTML = "";
  const entries = [...assetPick.selected.entries()];
  if (!entries.length) return;
  const title = document.createElement("div");
  title.className = "meta";
  title.textContent = "为选中的文件指定角色（必选）：";
  el.appendChild(title);
  for (const [path, role] of entries) {
    const row = document.createElement("div");
    row.className = "absel";
    const name = document.createElement("span");
    name.textContent = path.split(/[\\/]/).pop();
    name.title = path;
    name.style.minWidth = "180px";
    const sel = document.createElement("select");
    sel.appendChild(new Option("请选择角色", ""));
    for (const r of USER_ROLES) {
      sel.appendChild(new Option(ASSET_ROLE_LABELS[r] || r, r));
    }
    sel.value = role;
    sel.onchange = () => assetPick.selected.set(path, sel.value);
    row.append(name, sel);
    el.appendChild(row);
  }
}

async function submitAssetAdd() {
  const entries = [...assetPick.selected.entries()];
  if (!entries.length) { alert("先勾选要添加的文件"); return; }
  if (entries.some(([, role]) => !role)) { alert("每个素材都要选择一个角色"); return; }
  const d = await post("assets", {
    op: "add",
    paths: entries.map(([path]) => path),
    roles: entries.map(([, role]) => role),
  });
  if (!d) return;
  closeAssetBrowse();
}
