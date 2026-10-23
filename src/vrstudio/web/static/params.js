/** params.js —— 生成参数（模式/比例/输出与输入分辨率）与项目会话选择。
 *
 * 数据源是 info.generation_params（vrgen params show）与 /api/sessions。
 * 模式与比例先保存；分辨率/缩放要等素材步骤完成后才有可计算的值。
 */
"use strict";

function _modeMeta(gp, value) {
  return ((gp && gp.modes) || []).find(m => m.value === value) || null;
}

function _currentGenParams() {
  return ((info && info.generation_params) || {}).current || {};
}

function _fillAspect(mode, wanted) {
  const sel = $("genAspect");
  if (!sel) return "auto";
  sel.innerHTML = "";
  if (mode && mode.value === "firstLastFrame") {
    sel.appendChild(new Option("16:9（首期固定）", "16:9"));
    sel.value = "16:9";
    sel.disabled = true;
    return "16:9";
  }
  sel.disabled = false;
  const ratios = (mode && mode.ratios) || [];
  if (!ratios.length) {
    sel.appendChild(new Option("跟随输入", "auto"));
    sel.value = "auto";
    return "auto";
  }
  for (const r of ratios) sel.appendChild(new Option(r, r));
  const pick = ratios.includes(wanted) ? wanted
    : (ratios.includes("16:9") ? "16:9" : ratios[0]);
  sel.value = pick;
  return pick;
}

function _fillDuration(mode, autoSeconds) {
  const sel = $("genDuration");
  if (!sel) return;
  const previous = sel.value;
  sel.innerHTML = "";
  const d = (mode && mode.duration) || {};
  const autoLabel = autoSeconds ? `自动（约 ${autoSeconds} 秒）` : "自动";
  if (d.type === "range" && d.min != null && d.max != null) {
    const auto = new Option(autoLabel, "auto");
    auto.title = autoSeconds
      ? `不指定时长；按 clip 帧范围与帧率推导，约 ${autoSeconds} 秒`
      : "不指定时长，由母版或素材推导";
    sel.appendChild(auto);
    for (let i = Number(d.min); i <= Number(d.max); i++) {
      sel.appendChild(new Option(`${i} 秒`, String(i)));
    }
    sel.disabled = false;
  } else {
    const auto = new Option(autoLabel, "auto");
    auto.title = "由母版/素材推导";
    sel.appendChild(auto);
    sel.disabled = true;
  }
  // 重画（例如预演返回后）不改用户刚选的时长；选项里没有才退回自动。
  const values = [...sel.options].map(o => o.value);
  sel.value = values.includes(previous) ? previous : "auto";
}

function renderGenParams() {
  const resSel = $("genResolution"), scaleSel = $("genScale");
  const modeSel = $("genMode"), aspectSel = $("genAspect"), durSel = $("genDuration");
  const hint = $("genParamsHint"), fixed = $("genFixed");
  if (!resSel || !scaleSel || !modeSel || !aspectSel || !durSel) return;
  const gp = (info && info.generation_params) || {};
  if (gp.error) {
    for (const sel of [resSel, scaleSel, modeSel, aspectSel, durSel]) {
      sel.innerHTML = "";
      sel.disabled = true;
    }
    hint.style.display = "none";
    hint.textContent = "生成参数暂时读不到：" + gp.error;
    if (fixed) fixed.textContent = "";
    return;
  }
  hint.style.display = "none";
  hint.textContent = "";
  for (const sel of [resSel, scaleSel, modeSel, aspectSel, durSel]) sel.disabled = false;
  const cur = gp.current || {};
  const modes = gp.modes || [];
  modeSel.innerHTML = "";
  const modeList = modes.length ? modes
    : [{ value: cur.mode || "videoEdit", label: cur.mode || "videoEdit" }];
  for (const m of modeList) {
    modeSel.appendChild(new Option(m.label || m.value, m.value));
  }
  const mode = _modeMeta(gp, cur.mode) || modeList[0];
  modeSel.value = mode.value;
  _fillAspect(mode, cur.aspect_ratio);
  _fillDuration(mode, gp.auto_duration_s);

  const inputLabel = (o) => ({ auto: "自动", full: "原尺寸", half: "半尺寸" })[o.value] || o.value;
  const option = (o, text, extra = "") =>
    `<option value="${o.value}" title="${o.width}×${o.height}${o.reason ? "；" + o.reason : ""}"` +
    `${o.ok === false ? " disabled" : ""}>${text}${o.ok === false ? "　超限" : ""}${extra}</option>`;
  resSel.innerHTML = (gp.output || []).map(o => option(o, o.value)).join("");
  scaleSel.innerHTML = (gp.input || []).map(o => option(o, inputLabel(o))).join("");
  // 输出与输入分开判断：firstLastFrame 没有输入视频，input 必然为空，
  // 但输出分辨率仍然可选，不能因为 input 为空就把两个下拉框一起禁用。
  const noOutput = !(gp.output || []).length;
  const noInput = !(gp.input || []).length;
  resSel.disabled = noOutput;
  scaleSel.disabled = noInput;
  if (!noOutput && cur.resolution) resSel.value = cur.resolution;
  if (!noInput) {
    const wanted = [...scaleSel.options].find(o => o.value === cur.scale);
    if (wanted && !wanted.disabled) scaleSel.value = cur.scale;
    else scaleSel.value = ((gp.input || []).find(o => o.ok !== false) || {}).value || "auto";
  }
  if (noOutput) {
    hint.textContent = "先添加素材，再计算输出尺寸。";
  }
  const f = gp.fixed || {};
  if (durSel.disabled && durSel.options.length && f.duration_s) {
    durSel.options[0].textContent = `自动（${f.duration_s} 秒）`;
    durSel.options[0].title = `由母版/素材推导，本次实际为 ${f.duration_s} 秒`;
  }
  const chosen = durSel.value;
  const durationText = (chosen && chosen !== "auto")
    ? `${chosen} 秒`
    : (gp.auto_duration_s ? `自动（约 ${gp.auto_duration_s} 秒）`
      : (f.duration_s ? `自动（${f.duration_s} 秒）` : "由输入/素材推导"));
  fixed.textContent =
    `模型 ${f.model_id}（Seedance 2.5）· 时长 ${durationText}` +
    ` · 数量 ${f.num}` +
    (mode.value === "firstLastFrame" ? " · 首期全链路固定 16:9" : "");
}

function onModeChange() {
  const gp = (info && info.generation_params) || {};
  const mode = _modeMeta(gp, ($("genMode") || {}).value);
  _fillAspect(mode, null);
  _fillDuration(mode, gp.auto_duration_s);
  saveGenParams();
}

function saveGenParams() {
  const mode = ($("genMode") || {}).value || "";
  const aspect = ($("genAspect") || {}).value || "";
  const resolution = ($("genResolution") || {}).value || "";
  const scale = ($("genScale") || {}).value || "";
  if (!mode || !aspect) return;
  const body = { mode, aspect_ratio: aspect };
  if (resolution) body.resolution = resolution;
  if (scale) body.scale = scale;
  post("params", body);
}

// ── 生成额度：项目/剧集/生成会话（决策 22）────────────────────────────
// 数据每次页面加载都从 /api/sessions 重新取；选择结果写 generation.toml。

function _option(sel, value, label, title) {
  const o = document.createElement("option");
  o.value = String(value);
  o.textContent = label;
  if (title) o.title = title;
  sel.appendChild(o);
  return o;
}

function _project(id) {
  return ((sessionTree && sessionTree.projects) || []).find(p => p.id === id);
}

function _episode(project, no) {
  return ((project && project.episodes) || []).find(e => e.no === no);
}

function fillEpisodes(project, wantedEp) {
  const sel = $("sessEpisode");
  if (!sel) return 0;
  sel.innerHTML = "";
  const episodes = (project && project.episodes) || [];
  if (!episodes.length) {
    _option(sel, 0, "（这个项目还没有剧集）");
    return 0;
  }
  for (const e of episodes) {
    _option(sel, e.no, `第 ${e.no} 集 · ${e.name || "未命名"}`);
  }
  const hit = episodes.find(e => e.no === wantedEp);
  const chosen = hit ? hit.no : episodes[0].no;
  sel.value = String(chosen);
  return chosen;
}

function fillSessions(project, episodeNo, wantedSession) {
  const sel = $("sessSession");
  if (!sel) return;
  sel.innerHTML = "";
  _option(sel, 0, "个人额度（不使用项目会话）");
  const episode = _episode(project, episodeNo);
  const sessions = (episode && episode.sessions) || [];
  for (const s of sessions) {
    _option(sel, s.id, s.name || "未命名", `会话 ID ${s.id}`);
  }
  if (wantedSession && !sessions.some(s => s.id === wantedSession)) {
    _option(sel, wantedSession, `⚠ 已失效：${wantedSession}`, "这个会话已不在平台返回的列表里");
  }
  sel.value = String(wantedSession || 0);
}

function renderSessions() {
  const projectSel = $("sessProject");
  if (!projectSel) return;
  const cur = _currentGenParams();
  let pid = cur.project_id || 0;
  let ep = cur.episode || 0;
  const sid = cur.session_id || 0;
  const projects = (sessionTree && sessionTree.projects) || [];

  // 旧文件只有 session_id：在树里反查它属于哪个项目与剧集。
  if ((!pid || !ep) && sid) {
    for (const p of projects) {
      for (const e of (p.episodes || [])) {
        if ((e.sessions || []).some(s => s.id === sid)) { pid = p.id; ep = e.no; }
      }
    }
  }

  projectSel.innerHTML = "";
  if (!projects.length) {
    _option(projectSel, 0, "（没有可用的项目）");
  } else {
    for (const p of projects) _option(projectSel, p.id, p.name || `项目 ${p.id}`);
    if (!projects.some(p => p.id === pid)) pid = projects[0].id;
    projectSel.value = String(pid);
  }
  const project = _project(pid);
  ep = fillEpisodes(project, ep);
  fillSessions(project, ep, sid);
  if (typeof renderSubmitBar === "function") renderSubmitBar();
}

function _setHint(el, text, cls = "meta") {
  if (!el) return;
  el.className = cls;
  el.textContent = text;
  el.title = text;
}

async function loadSessions() {
  const hint = $("sessionHint");
  if (!hint) return;
  _setHint(hint, "正在获取项目与会话…");
  try {
    const r = await fetch("/api/sessions");
    const d = await r.json();
    if (!r.ok || d.error) throw new Error(d.error || r.statusText);
    sessionTree = d;
    renderSessions();
    _setHint(hint, `已获取 ${(d.projects || []).length} 个项目`);
    if (typeof renderKeyframe === "function") renderKeyframe();   // 首尾帧步骤的计费会话行
  } catch (e) {
    renderSessions();
    _setHint(hint, "会话列表获取失败：" + (e.message || e), "meta bad");
  }
}

function onProjectChange() {
  const project = _project(+$("sessProject").value || 0);
  const ep = fillEpisodes(project, 0);
  fillSessions(project, ep, 0);
  _setHint($("sessionHint"), "选好生成会话后自动保存");
}

function onEpisodeChange() {
  const project = _project(+$("sessProject").value || 0);
  fillSessions(project, +$("sessEpisode").value || 0, 0);
  _setHint($("sessionHint"), "选好生成会话后自动保存");
}

async function onSessionChange() {
  const pid = +$("sessProject").value || 0;
  const ep = +$("sessEpisode").value || 0;
  const sid = +$("sessSession").value || 0;
  const resolution = ($("genResolution") || {}).value || "";
  const scale = ($("genScale") || {}).value || "";
  const hint = $("sessionHint");
  const body = { session_id: sid, project_id: sid ? pid : 0, episode: sid ? ep : 0 };
  if (resolution) body.resolution = resolution;
  if (scale) body.scale = scale;
  const d = await post("params", body);
  if (!d) return;
  const picked = $("sessSession").selectedOptions[0];
  const sname = picked ? picked.textContent : String(sid);
  _setHint(hint, sid
    ? `已保存项目会话「${sname}」；下次提交扣项目额度`
    : "已切换为个人额度", "meta " + (sid ? "ok" : ""));
}
