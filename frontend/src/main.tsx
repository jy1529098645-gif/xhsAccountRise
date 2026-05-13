import React from "react";
import ReactDOM from "react-dom/client";
import { HashRouter } from "react-router-dom";
import App from "./App";
import "./styles.css";

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
