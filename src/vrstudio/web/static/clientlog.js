/** clientlog.js —— 浏览器端错误上报；只写服务端的 client-*.jsonl。
 *
 * 全局脚本错误、未处理的 Promise 拒绝，以及页面显式调用的业务错误都走
 * window.reportClientError。同一个错误在本次页面生命周期内只报一次，
 * 避免错误循环把日志撑爆；上报失败不影响页面。
 */
"use strict";

window.reportClientError = (function () {
  const seen = new Set();

  function send(level, message, extra) {
    extra = extra || {};
    const source = String(extra.source || "");
    if (source.includes("/api/client-log")) return;   // 上报接口自身不再回传
    const key = [level, message, source, extra.line, extra.col].join("|");
    if (seen.has(key)) return;
    if (seen.size < 100) seen.add(key);

    const body = {
      level: level || "error",
      message: String(message || "unknown client error").slice(0, 4000),
      detail: extra.detail == null ? null : String(extra.detail).slice(0, 4000),
      source: source.slice(0, 1000),
      line: extra.line == null ? null : extra.line,
      col: extra.col == null ? null : extra.col,
      stack: extra.stack == null ? null : String(extra.stack).slice(0, 4000),
      context: extra.context == null ? null : String(extra.context).slice(0, 200),
      code: extra.code == null ? null : extra.code,
      url: location.href,
      ua: navigator.userAgent,
      case: (document.body && document.body.dataset.name) || null,
    };
    try {
      const blob = new Blob([JSON.stringify(body)], { type: "application/json" });
      if (navigator.sendBeacon && navigator.sendBeacon("/api/client-log", blob)) return;
    } catch (_) { /* sendBeacon 不可用时走 fetch */ }
    try {
      fetch("/api/client-log", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
        keepalive: true,
      }).catch(() => {});
    } catch (_) { /* 日志失败不能影响页面 */ }
  }

  return send;
})();

window.addEventListener("error", (e) => {
  window.reportClientError("error", e.message || "script error", {
    source: e.filename,
    line: e.lineno,
    col: e.colno,
    stack: e.error && e.error.stack,
  });
});

window.addEventListener("unhandledrejection", (e) => {
  const reason = e.reason;
  window.reportClientError(
    "error",
    (reason && reason.message) || String(reason || "unhandled rejection"),
    { stack: reason && reason.stack, context: "unhandledrejection" },
  );
});
