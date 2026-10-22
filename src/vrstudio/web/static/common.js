/** common.js —— 向导页的公共状态、请求封装与渲染调度。
 *
 * 各步骤渲染函数在各自脚本里定义（assets/params/prompt……），这里只按名字调用
 * 已存在的函数，避免拆分脚本时互相依赖加载顺序。
 */
"use strict";

const CASE = document.body.dataset.name;
const FLOW = document.body.dataset.flow;

let info = null;            // 最近一次 /api/case 的应答
let curStep = null;         // 当前显示的步骤 id
let wasRunning = false;     // 任务从忙到闲时刷新一次 info
let vpFrame = null;         // 当前逐帧查看的帧号（null = 第 0 帧）
let sessionTree = null;     // /api/sessions 的项目/剧集/会话树

// 任务类型 → 页面上的说法（op id 是内部标识，不出现在页面上）
const KIND_LABEL = {
  extract: "抽取", pad: "扩充两侧画面", register: "登记外部视频",
  submit: "提交生成", dry_run: "预演检查", keyframe: "关键帧图片生成",
  adopt: "采用成片", merge: "空间合并", assemble: "时间拼接",
  recompose: "裁回原尺寸",
};

const $ = (id) => document.getElementById(id);

function gateText(id, elId) {
  const g = (info && info.gates && info.gates[id]) || {};
  const el = $(elId);
  if (!el) return;
  el.textContent = g.ok ? ("✓ " + (g.evidence || "这一步已完成"))
                        : (g.reason || "这一步还没完成");
  el.className = "meta " + (g.ok ? "ok" : "bad");
}

/** 依次调用已定义在各脚本里的渲染函数；未加载的面板自动跳过。 */
function renderAll() {
  for (const name of ["renderSteps", "renderAssets", "renderPick", "renderExtract",
                      "renderResultStrip", "renderGenParams", "renderSubmitBar",
                      "renderFinish", "renderStepFiles", "renderPrompt",
                      "renderCompare", "renderKeyframe"]) {
    if (typeof window[name] === "function") window[name]();
  }
}

async function post(path, body, opts = {}) {
  const r = await fetch(`/api/case/${encodeURIComponent(CASE)}/${path}`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (r.status === 404) { location.href = "/"; return null; }
  const d = await r.json();
  if (!r.ok) { alert(d.error || r.statusText); return null; }
  // 连续拖框时旧请求可能后到；stale() 为真就不拿它覆盖更新的界面状态。
  if (opts.stale && opts.stale()) return d;
  const prev = info;
  info = d;                                   // 这些端点都回新 info
  // draft/nudge 等高频端点不回 prompt / 生成参数；保留上一份，避免面板被清空。
  if (!info.generation_params && prev && prev.generation_params)
    info.generation_params = prev.generation_params;
  if (!info.prompt && prev && prev.prompt) info.prompt = prev.prompt;
  renderAll();
  return d;
}

async function run(op, params = {}) {
  const r = await fetch(`/api/case/${encodeURIComponent(CASE)}/run`, {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ op, ...params }),
  });
  const d = await r.json();
  if (!r.ok) { alert(d.error || r.statusText); return null; }
  wasRunning = true;
  pollTasks();
  return d;
}
function runOp(op, params) { run(op, params); }
