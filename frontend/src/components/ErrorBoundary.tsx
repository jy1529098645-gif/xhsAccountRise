import { Component, ErrorInfo, ReactNode } from "react";

interface Props { children: ReactNode; fallbackLabel?: string }
interface State { err: Error | null; info: ErrorInfo | null }

export default class ErrorBoundary extends Component<Props, State> {
  state: State = { err: null, info: null };

  static getDerivedStateFromError(err: Error): State {
    return { err, info: null };
  }
  componentDidCatch(err: Error, info: ErrorInfo) {
    // eslint-disable-next-line no-console
    console.error("[ErrorBoundary] caught", err, info?.componentStack || "");
    this.setState({ err, info });
  }

  reset = () => {
    this.setState({ err: null, info: null });
  };

  render() {
    if (!this.state.err) {
      // v0.61.17 ：render 本身也包一层 try — 极端情况（subtree 还在 throw
      // 阶段但 React 没 commit error state）至少给用户看到边界，不是白屏。
      try {
        return this.props.children;
      } catch (e: any) {
        // eslint-disable-next-line no-console
        console.error("[ErrorBoundary] sync render threw", e);
        return this._errorUI(e instanceof Error ? e : new Error(String(e)));
      }
    }
    return this._errorUI(this.state.err);
  }

  private _errorUI(err: Error) {
    const msg = (err && (err.message || String(err))) || "未知错误";
    const stack = err && err.stack ? "\n\n" + err.stack : "";
    return (
      <div className="card" style={{borderLeft: "4px solid var(--danger)"}}>
        <h2 style={{margin: 0}}>⚠️ {this.props.fallbackLabel || "这个页面渲染崩了"}</h2>
        <p className="muted" style={{margin: "6px 0 12px"}}>
          其它页面不受影响 ：左侧栏的链接都能用。常见原因 ：API 返回不完整 /
          浏览器缓存的旧 JS 模块 / 后端版本不匹配。建议先 Ctrl + Shift + R 硬刷新。
        </p>
        <details style={{marginBottom: 10}} open>
          <summary style={{cursor: "pointer", fontSize: 12.5, color: "var(--muted)"}}>
            ▾ 报错详情
          </summary>
          <pre style={{background: "#fafafa", padding: 10, fontSize: 11.5,
                       overflow: "auto", maxHeight: 240, marginTop: 8,
                       whiteSpace: "pre-wrap"}}>
            {msg}{stack}
          </pre>
        </details>
        <div className="row" style={{gap: 6}}>
          <button onClick={this.reset}>↻ 重试渲染</button>
          <button className="ghost" onClick={() => window.location.reload()}>🔄 刷新页面</button>
        </div>
      </div>
    );
  }
}
