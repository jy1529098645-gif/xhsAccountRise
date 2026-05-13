import { Component, ErrorInfo, ReactNode } from "react";

interface Props { children: ReactNode }
interface State { err: Error | null; info: ErrorInfo | null }

export default class ErrorBoundary extends Component<Props, State> {
  state: State = { err: null, info: null };

  static getDerivedStateFromError(err: Error): State {
    return { err, info: null };
  }
  componentDidCatch(err: Error, info: ErrorInfo) {
    console.error("[ErrorBoundary] caught", err, info);
    this.setState({ err, info });
  }

  reset = () => {
    this.setState({ err: null, info: null });
  };

  render() {
    if (!this.state.err) return this.props.children;
    return (
      <div className="card" style={{borderLeft: "4px solid var(--danger)"}}>
        <h2 style={{margin: 0}}>⚠️ 这个页面渲染崩了</h2>
        <p className="muted" style={{margin: "6px 0 12px"}}>
          其它页面不受影响 ：左侧栏的链接都能用。如果反复崩，刷新一下页面。
        </p>
        <details style={{marginBottom: 10}}>
          <summary style={{cursor: "pointer", fontSize: 12.5, color: "var(--muted)"}}>
            ▾ 报错详情（开发用）
          </summary>
          <pre style={{background: "#fafafa", padding: 10, fontSize: 11.5,
                       overflow: "auto", maxHeight: 240, marginTop: 8,
                       whiteSpace: "pre-wrap"}}>
            {String(this.state.err.message || this.state.err)}
            {this.state.err.stack ? "\n\n" + this.state.err.stack : ""}
          </pre>
        </details>
        <button onClick={this.reset}>↻ 重试渲染</button>
      </div>
    );
  }
}
