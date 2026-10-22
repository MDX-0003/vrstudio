/** tasks.js —— 单队列任务轮询与展示（决策 17 串行层）。 */
"use strict";

async function pollTasks() {
  try {
    const r = await fetch("/api/task");
    const s = await r.json();
    const lines = [];
    const label = (k) => KIND_LABEL[k] || k;
    if (s.current) {
      lines.push(`▶ 正在执行（${s.current.submitted} 提交）[${s.current.case}] ${label(s.current.kind)}…`);
      if (s.current.log) lines.push(`完整记录：${s.current.log}`);
      lines.push(...s.current.tail);
    } else lines.push("（当前没有任务在跑）");
    if (s.queued.length) {
      lines.push("── 排队等候（早提交的先跑）──");
      for (const q of s.queued) lines.push(`${q.submitted}  [${q.case}] ${label(q.kind)}`);
    }
    if (s.last.length) {
      lines.push("── 最近完成（新→旧，最多 10 条，行首是提交时间）──");
      for (const h of [...s.last].reverse()) {
        lines.push(`${h.submitted}  ${h.state === "done" ? "✓ 完成" : "✗ 失败"}：[${h.case}] ${label(h.kind)}` +
                   (h.log ? `（完整记录：${h.log}）` : ""));
        if (h.state !== "done") lines.push(...(h.tail || []));
      }
    }
    const box = $("taskBox");
    if (box) box.textContent = lines.join("\n");
    const busy = !!(s.current || s.queued.length);
    if (wasRunning && !busy) {
      if (typeof onTasksSettled === "function") onTasksSettled(s);
      loadInfo();                             // 任务收尾 → 刷新门控与列表
    }
    wasRunning = busy;
  } catch (_) { /* 服务重启间隙忽略 */ }
}
