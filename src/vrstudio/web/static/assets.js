/** assets.js —— gen 的素材步骤：卡片、角色、排序、删除与本地多选添加。
 *
 * 角色词表与后端 vrstudio.case.ROLE_LABELS 一致；当前模式允许的角色来自
 * /api/case 的 generation_params.modes[].roles（模型能力 + 本地矩阵的只读回显）。
 */
"use strict";

const ASSET_ROLE_LABELS = {
  edit_master: "编辑母版（时间轴、构图与动作基准）",
  motion_ref: "动作参考（只提供动作节奏与路径）",
  space_ref: "空间参考（只提供机位、构图与起终点位置）",
  scene_ref: "场景参考（只提供构图、背景与光照）",
  subject_ref: "主体参考（只定义角色外观）",
};

let assetPick = { dir: null, selected: new Map() };   // path -> role

function _modeMetaFor(infoObj, value) {
  const gp = (infoObj && infoObj.generation_params) || {};
  return ((gp.modes) || []).find(m => m.value === value) || null;
}

function _currentMode() {
  const gp = (info && info.generation_params) || {};
  return ((gp.current || {}).mode) || "";
}

function _allowedRoles(kind) {
  const meta = _modeMetaFor(info, _currentMode());
  const roles = meta && meta.roles && meta.roles[kind];
  return roles && roles.length ? roles : Object.keys(ASSET_ROLE_LABELS);
}

function _kindLabel(kind) {
  return kind === "video" ? "视频" : "图片";
}

function _fmtDuration(sec) {
  return sec == null ? "" : `${Number(sec).toFixed(2)}s`;
}

function _roleSelect(asset) {
  const sel = document.createElement("select");
  const allowed = _allowedRoles(asset.kind);
  const roles = allowed.includes(asset.role) ? allowed : [asset.role, ...allowed];
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

function _assetCard(asset, index, assets) {
  const card = document.createElement("div");
  card.className = "assetCard" + (asset.exists ? "" : " bad");
  card.draggable = true;
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
  meta.innerHTML = `<b>${asset.label} · ${asset.id}</b> · ${_kindLabel(asset.kind)} · ${size}<br>` +
    `<span class="dim">${asset.rel || asset.path}</span>`;

  const moves = document.createElement("span");
  const up = document.createElement("button");
  up.textContent = "↑"; up.title = "上移"; up.disabled = index === 0;
  up.onclick = () => _moveAsset(assets, index, -1);
  const down = document.createElement("button");
  down.textContent = "↓"; down.title = "下移"; down.disabled = index === assets.length - 1;
  down.onclick = () => _moveAsset(assets, index, 1);
  moves.append(up, down);

  const del = document.createElement("button");
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
    const ids = assets.map(a => a.id);
    const fromIndex = ids.indexOf(from);
    const toIndex = ids.indexOf(asset.id);
    if (fromIndex < 0 || toIndex < 0) return;
    ids.splice(toIndex, 0, ids.splice(fromIndex, 1)[0]);
    post("assets", { op: "reorder", ids });
  });

  card.append(img, meta, _roleSelect(asset), moves, del);
  return card;
}

function _moveAsset(assets, index, delta) {
  const ids = assets.map(a => a.id);
  const to = index + delta;
  if (to < 0 || to >= ids.length) return;
  [ids[index], ids[to]] = [ids[to], ids[index]];
  post("assets", { op: "reorder", ids });
}

function renderAssets() {
  if (FLOW !== "gen") return;
  const el = $("assetList");
  if (!el) return;
  const assets = (info && info.assets) || [];
  el.innerHTML = "";
  if (!assets.length) {
    el.innerHTML = '<span class="meta">还没有素材。至少添加 1 个视频；图片作为附加参考。</span>';
  } else {
    assets.forEach((asset, index) => el.appendChild(_assetCard(asset, index, assets)));
  }
  const hint = $("assetHint");
  if (hint) {
    const videos = assets.filter(a => a.kind === "video").length;
    const images = assets.filter(a => a.kind === "image").length;
    const mode = _currentMode();
    hint.textContent = assets.length
      ? `当前模式 ${mode || "未设置"} · ${videos} 个视频 · ${images} 张图片`
      : "先添加素材，再回到「生成」步骤";
    hint.className = "meta " + (videos ? "ok" : "bad");
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
    const kind = /\.(png|jpe?g|webp)$/i.test(path) ? "image" : "video";
    const sel = document.createElement("select");
    const blank = new Option("请选择角色", "");
    sel.appendChild(blank);
    for (const r of _allowedRoles(kind)) {
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
