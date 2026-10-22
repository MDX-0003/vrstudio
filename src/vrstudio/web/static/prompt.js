/** prompt.js —— 提示词面板（模板只读 + 变量编辑，决策 18/25）。
 *
 * 数据来自 info.prompt（只在 loadInfo 与保存应答里带）。几何键与计算键
 * （asset_list/task_rule/timeline_rule）都是只读占位符，不生成输入框。
 * 变量显示名来自模板里的 {键|显示名}，由 prompt.labels 下发，前端不写死映射。
 */
"use strict";

let promptView = "rendered";   // rendered = 完整提示词；template = 含占位符的骨架
let promptSaveTimer = null;
const pendingPromptSaves = new Map();   // 键 -> 尚未落盘的值

function _promptText(text) {
  // 在【章节】前留空行，长提示词按段可读；pre 仍保留原始换行。
  return String(text || "").replace(/\n(?=【)/g, "\n\n");
}

function _label(p, key) {
  return ((p && p.labels) || {})[key] || key;
}

function renderPromptBox() {
  const p = info && info.prompt;
  const box = $("promptBox"), toggle = $("promptToggle"), state = $("promptViewState");
  const sum = $("promptPreviewSummary");
  if (!box) return;
  if (!p || p.error) {
    box.textContent = (p && p.error) || "（提示词信息还没有加载）";
    if (toggle) toggle.style.display = "none";
    if (state) state.textContent = "";
    if (sum) sum.textContent = "";
    return;
  }
  if (toggle) {
    toggle.style.display = p.template_text ? "" : "none";
    toggle.textContent = promptView === "template" ? "返回完整预览" : "查看模板骨架";
  }
  if (state) {
    state.textContent = promptView === "template"
      ? "模板骨架（| 后是页面显示名，不进入成片提示词）"
      : "完整预览";
  }
  if (sum) {
    const text = promptView === "template" ? p.template_text : p.rendered;
    sum.textContent = text ? `约 ${String(text).length} 字` : "";
  }
  if (promptView === "template") {
    box.textContent = _promptText(p.template_text) || "（还没有选定模板）";
    return;
  }
  if (p.rendered) {
    box.textContent = _promptText(p.rendered);
    return;
  }
  if (p.render_error) {
    box.textContent = "还不能展开完整提示词：\n" + p.render_error;
    return;
  }
  box.textContent = "（还没有可展开的提示词）";
}

function togglePromptView() {
  promptView = promptView === "rendered" ? "template" : "rendered";
  renderPromptBox();
}

/** 输入框随内容增高；CSS 的 max-height 负责封顶。 */
function autoGrow(el) {
  if (!el) return;
  el.style.height = "auto";
  el.style.height = (el.scrollHeight + 2) + "px";
}

function _varSummary(value) {
  const text = String(value || "");
  const head = text.replace(/\s+/g, " ").trim().slice(0, 40);
  return `${text.length} 字${head ? " · " + head : ""}`;
}

function _refreshVarSummary(card, value) {
  const sum = card.querySelector(".vsum");
  if (sum) sum.textContent = _varSummary(value);
}

function _varCard(p, key, declared) {
  const card = document.createElement("details");
  card.className = "varCard";
  card.dataset.key = key;

  const summary = document.createElement("summary");
  const name = document.createElement("span");
  name.className = "vname";
  name.textContent = _label(p, key);
  const keyEl = document.createElement("span");
  keyEl.className = "vkey";
  keyEl.textContent = key;
  const sum = document.createElement("span");
  sum.className = "vsum";
  summary.append(name, keyEl, sum);
  card.appendChild(summary);

  const ta = document.createElement("textarea");
  ta.rows = 3;
  ta.value = (p.vars || {})[key] ?? "";
  ta.placeholder = "（还没填）";
  ta.oninput = () => {
    autoGrow(ta);
    _refreshVarSummary(card, ta.value);
    schedulePromptSave(key, ta.value);
  };
  card.appendChild(ta);
  _refreshVarSummary(card, ta.value);
  card.addEventListener("toggle", () => { if (card.open) autoGrow(ta); });

  const ops = document.createElement("div");
  ops.className = "varOps";
  if (declared.includes(key)) {
    const lock = document.createElement("span");
    lock.textContent = "模板变量";
    lock.title = "模板正文引用了这个键，只能改值，不能删除";
    ops.appendChild(lock);
  } else {
    const del = document.createElement("button");
    del.textContent = "删除";
    del.onclick = () => unsetPromptVar(key);
    ops.appendChild(del);
  }
  card.appendChild(ops);
  return card;
}

function renderPrompt() {
  const p = info && info.prompt;
  const list = $("varList"), meta = $("promptMeta"), ro = $("readonlyVars");
  renderPromptBox();
  if (!list || !meta) return;
  const summaryEl = $("promptSummary");
  if (!p || p.error) {
    list.innerHTML = "";
    if (ro) ro.innerHTML = "";
    list.dataset.sig = "";
    meta.textContent = "";
    if (summaryEl) summaryEl.textContent = p && p.error ? "读取失败" : "";
    return;
  }
  const computed = p.computed_keys || [];
  const readOnly = [...(p.geometry_keys || []), ...computed];
  const declared = (p.placeholders || []).filter(k => !readOnly.includes(k));
  const keys = [...new Set([...declared, ...Object.keys(p.vars || {})])]
    .filter(k => !readOnly.includes(k));
  if (summaryEl) {
    summaryEl.textContent = `${p.template || "未选择模板"} · ${declared.length} 个可编辑变量` +
      (p.render_error ? " · 有变量待补齐" : "");
  }
  meta.textContent = p.template
    ? `模板 ${p.template}。完整预览已代入当前变量、素材清单、模式规则与几何尺寸；` +
      (computed.length ? `${computed.join("、")} 由页面自动填入。` : "")
    : "";

  const sig = JSON.stringify([keys, readOnly, p.template]);
  if (list.dataset.sig === sig && list.children.length) {
    // 签名没变：只同步值，不重建输入框，避免别的请求把焦点冲掉。
    for (const card of list.querySelectorAll(".varCard")) {
      const ta = card.querySelector("textarea");
      if (!ta) continue;
      if (document.activeElement !== ta) {
        ta.value = (p.vars || {})[card.dataset.key] ?? "";
      }
      _refreshVarSummary(card, ta.value);
    }
    return;
  }

  list.dataset.sig = sig;
  list.innerHTML = "";
  for (const k of keys) list.appendChild(_varCard(p, k, declared));
  for (const ta of list.querySelectorAll("textarea")) autoGrow(ta);

  if (ro) {
    ro.innerHTML = "";
    for (const k of readOnly) {
      const item = document.createElement("span");
      const name = document.createElement("b");
      name.textContent = _label(p, k);
      item.append(name);
      if (_label(p, k) !== k) item.append(` · ${k}`);
      item.append(" · 自动填入");
      ro.appendChild(item);
    }
  }
}

/** 防抖保存：连续输入只在停顿 400ms 后发一次。 */
function schedulePromptSave(key, value) {
  pendingPromptSaves.set(key, value);
  clearTimeout(promptSaveTimer);
  promptSaveTimer = setTimeout(() => { flushPromptSaves(); }, 400);
}

async function _postPrompt(body) {
  const r = await fetch(`/api/case/${encodeURIComponent(CASE)}/prompt`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const d = await r.json();
  if (!r.ok) { alert(d.error || r.statusText); return null; }
  return d;
}

/** 提交与预演前调用：把防抖中的变量全部落盘。失败返回 false。 */
async function flushPromptSaves() {
  clearTimeout(promptSaveTimer);
  promptSaveTimer = null;
  const entries = [...pendingPromptSaves.entries()];
  pendingPromptSaves.clear();
  let ok = true;
  for (const [key, value] of entries) {
    const d = await _postPrompt({ set: key, value });
    if (!d) { ok = false; continue; }
    info = d;
    renderPromptBox();
    if (typeof renderSubmitBar === "function") renderSubmitBar();
  }
  return ok;
}

async function unsetPromptVar(key) {
  await flushPromptSaves();
  const d = await _postPrompt({ unset: key });
  if (!d) return;
  info = d;
  renderPromptBox();
  renderPrompt();
  if (typeof renderSubmitBar === "function") renderSubmitBar();
}

async function addVar() {
  const k = $("newVarKey").value.trim(), v = $("newVarVal").value;
  if (!k) { alert("先填变量名"); return; }
  await flushPromptSaves();
  const d = await _postPrompt({ set: k, value: v });
  if (!d) return;
  info = d;
  $("newVarKey").value = "";
  $("newVarVal").value = "";
  renderPromptBox();
  renderPrompt();
  if (typeof renderSubmitBar === "function") renderSubmitBar();
}
