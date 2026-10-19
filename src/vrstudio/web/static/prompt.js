/** prompt.js —— 提示词面板（模板只读 + 变量编辑，决策 18/25）。
 *
 * 数据来自 info.prompt（只在 loadInfo 与保存应答里带）。几何键与计算键
 * （asset_list/task_rule/timeline_rule）都是只读占位符，不生成输入框。
 */
"use strict";

function renderPrompt() {
  const p = info && info.prompt;
  const box = $("promptBox"), list = $("varList"), meta = $("promptMeta");
  if (!box || !list || !meta) return;
  if (!p || p.error) {
    box.textContent = (p && p.error) || "（提示词信息还没有加载）";
    list.innerHTML = "";
    meta.textContent = "";
    return;
  }
  box.textContent = p.template_text || "（还没有选定模板）";
  const computed = p.computed_keys || [];
  const readOnly = [...(p.geometry_keys || []), ...computed];
  meta.textContent = p.template
    ? `模板 ${p.template}。正文里 ${(p.geometry_keys || []).length} 个几何占位` +
      (computed.length ? `、${computed.length} 个素材/模式自动键（${computed.join("、")}）` : "") +
      "在提交时自动填入，不用手动改。"
    : "";
  const declared = (p.placeholders || []).filter(k => !readOnly.includes(k));
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
  // 不走 post()：它会重画整个提示词面板，连续改多个变量时后一次重画会把
  // 前一个输入框的焦点/未失焦值冲掉。已有变量的值变化不改变模板正文，
  // 只更新 info；新增/删除变量才重建列表。
  const existed = body.set && info && info.prompt && info.prompt.vars
    && Object.prototype.hasOwnProperty.call(info.prompt.vars, body.set);
  const r = await fetch(`/api/case/${encodeURIComponent(CASE)}/prompt`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const d = await r.json();
  if (!r.ok) { alert(d.error || r.statusText); return; }
  info = d;
  if (body.unset || !existed) renderPrompt();
  if (body.set && !existed) {
    $("newVarKey").value = "";
    $("newVarVal").value = "";
  }
}

function addVar() {
  const k = $("newVarKey").value.trim(), v = $("newVarVal").value;
  if (!k) { alert("先填变量名"); return; }
  savePrompt({ set: k, value: v });
}
