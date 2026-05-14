import React from "react";
import ReactDOM from "react-dom/client";
import { HashRouter } from "react-router-dom";
import App from "./App";
import "./styles.css";

// v0.61.12 ：last-resort 全局 error 兜底。React 的 ErrorBoundary 抓不到
// 异步 effect / Promise 抛错，结果有时整页空白。这里挂 window.onerror +
// onunhandledrejection 直接画一条红横幅在顶上，告诉用户发生了啥 + 引导
// 他们到 console F12 看堆栈。比「白屏 + 沉默」好太多。
function ensureGlobalErrorBanner() {
  if (typeof window === "undefined") return;
  function show(msg: string) {
    try {
      let bar = document.getElementById("__global_err_banner__");
      if (!bar) {
        bar = document.createElement("div");
        bar.id = "__global_err_banner__";
        bar.style.cssText =
          "position:fixed;top:0;left:0;right:0;z-index:99999;" +
          "background:#fef2f2;color:#991b1b;border-bottom:1px solid #fecaca;" +
          "padding:8px 14px;font-size:12.5px;font-family:ui-sans-serif,system-ui;" +
          "white-space:pre-wrap;max-height:50vh;overflow:auto;";
        const close = document.createElement("button");
        close.textContent = "×";
        close.style.cssText =
          "float:right;background:transparent;border:0;color:#991b1b;" +
          "font-size:16px;cursor:pointer;line-height:1;padding:0 4px;";
        close.onclick = () => bar?.remove();
        bar.appendChild(close);
        const body = document.createElement("div");
        body.id = "__global_err_body__";
        bar.appendChild(body);
        document.body.appendChild(bar);
      }
      const body = document.getElementById("__global_err_body__");
      if (body) body.textContent = msg;
    } catch { /* DOM not ready / sandboxed */ }
  }
  window.addEventListener("error", (e) => {
    show(`⚠️ JS error: ${e.message}\n${(e.error && (e.error as any).stack) || ""}\n(F12 → Console 看完整堆栈)`);
  });
  window.addEventListener("unhandledrejection", (e) => {
    const r = (e as any).reason;
    const msg = r?.message || String(r);
    show(`⚠️ Unhandled promise rejection: ${msg}\n(F12 → Console 看完整堆栈)`);
  });
}
ensureGlobalErrorBanner();

// HashRouter (URLs like /#/dashboard) — avoids the BrowserRouter problem
// where deep links 404 on static hosts (GitHub Pages, S3, Netlify rewrites,
// etc.). Every browser handles this identically; no server rewrite needed.
ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <HashRouter>
      <App />
    </HashRouter>
  </React.StrictMode>
);
