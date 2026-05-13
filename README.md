# xhs Account Rise Studio

闭环工具：从 xhs 语料提取爆款 DNA → 多 Agent 跨模型并发出稿 → 候选评审 → 改稿 → 选 final → 追踪互动。

为 AcademiCats（下沉学生 AI 学术工作台）的小红书起号设计；支持上传**任意 xhs.db 库**切换不同赛道的策略。

🌐 **前端在线 demo**：https://jy1529098645-gif.github.io/xhsAccountRise/ （静态只读；要上传/生成请按下面 Quick Start 跑本地后端）

完整规划：[STUDIO_PLAN.md](./STUDIO_PLAN.md)

## 多 Agent 内容生成架构

```
Brief ─▶ Strategist (Claude Opus) ─┐  hook 类型 / 开头钩子 / 结构 / 避坑
                                   │
        Researcher (FTS5 RAG) ─────┤  top 爆款 + 用户原话 + hook 模板
                                   │
            ▼
     Drafter Pool (并发，跨家)
     ├─ Claude Opus 4.7  ─▶ 候选 A    每家产 {title,body,tags,cover,hook,
     ├─ DeepSeek V3      ─▶ 候选 B     predicted_likes,self_score,critique}
     └─ OpenAI GPT-4o    ─▶ 候选 C
            ▼
     Critic Pool (跨家，与 drafter 不同)
     ├─ Claude Sonnet  ─┐  对每份候选打 5 维分：
     └─ DeepSeek       ─┘    hook / language_fit / shareability /
                              brand_safety / structural_clarity
            ▼
        Refiner (Claude Opus) ─▶ 拿评分 top 候选 → 按 critic 建议改稿
            ▼
   ★ Synthesizer (Claude Opus, LLM-driven) ★
        - 看完 N 份 drafts + 所有 critique
        - 取每家最强的元素（标题用 A 的 hook、骨架用 B 的、金句用 C 的）
        - 主动修掉所有 critic 标出的 risk_flags
        - 输出 `rationale`：title_from / body_from / addresses_risks
            ▼
    持久化：drafts / candidates / critiques / agent_traces
```

每个 Agent 的 LLM 在 Composer 里可单独配置；时间线 + 成本估算 + 错误状态全程可见。

实测一次「降AI率技巧」full-strength compose：3 drafter + 2 critic + refiner + synthesizer，99s / $0.19，Synthesizer 主动修掉 5 条 critic 标的风险（学术诚信立意、过度承诺数字、品牌背书、CTA 过强、缺免责说明）。

## Quick start (本地后端)

```powershell
# 1. Python deps
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt

# 2. API keys
Copy-Item .env.example .env
# 填 .env：ANTHROPIC_API_KEY / DEEPSEEK_API_KEY / OPENAI_API_KEY

# 3. 初始化 DB + 跑分析
$env:PYTHONPATH = (Get-Location).Path
.venv\Scripts\python -m studio migrate
.venv\Scripts\python -m studio rag build
.venv\Scripts\python -m studio analyze
.venv\Scripts\python -m studio promote-hooks

# 4. 启 FastAPI 后端 (Windows 务必设 PYTHONUTF8=1 以正确处理中文)
$env:PYTHONUTF8 = "1"
.venv\Scripts\python -m studio serve --port 8765

# 5. 启前端（另开终端）
cd frontend
npm install
npm run dev
# → http://localhost:5173
```

## CLI 全集

```
studio migrate                                 # 应用未跑过的 migration
studio status                                  # 行数 / 库 / providers
studio rag build                               # 重建 FTS5
studio rag search "AI 写论文"                   # 调试检索
studio analyze                                 # 出 DNA artifact + HTML
studio render <json>                           # 重渲染历史 artifact
studio promote-hooks                           # DNA → studio_hook_templates
studio generate --topic ... --llms claude,deepseek    # 单 LLM 模式
studio compose --topic ... --drafters claude:opus,deepseek,openai \
                            --critics claude:sonnet,deepseek         # 多 Agent
studio library list / activate <id> / add <path> / delete <id>
studio serve --port 8765                       # 启 FastAPI
studio export-public                           # 静态化导出到 frontend/public/data/
```

## 多平台 + 多 Library

每个 library 是一个独立 SQLite .db，放在 `data/libraries/<lib_id>/xhs.db`，可标记平台：
**小红书** (default) / **抖音** / **快手** / **B站** / **YouTube** / **Reddit** / **X (Twitter)** / **其他**。

Brief 默认继承激活库的平台；可在 Composer 中显式覆盖。平台 voice hint 注入 Strategist 和 Drafter 的 prompt，所以同一个 brief 在抖音库下出短视频脚本风、在 Reddit 库下出长文论证体。

```powershell
# CLI
studio library add path/to/xhs.db --name "考研写作-2026"
studio library activate kaoyan-xiezuo-2026
studio analyze        # 该库的爆款 DNA
studio compose ...    # 该库语料下的稿件
```

Web UI 上更简单：Libraries 页面拖拽 .db → 一键激活 → 一键重分析 → 切到 Composer 直接出稿。

## 项目布局

```
H:\xhsAccountRise\
├── studio\                  # Python 后端
│   ├── agents\              # 多 Agent (Strategist/Researcher/Drafter/Critic/Refiner/Synthesizer/Pipeline)
│   ├── analysis\            # DNA extraction + hook 分类 + render report
│   ├── api\                 # FastAPI server + 静态导出
│   ├── generators\          # Claude / DeepSeek / OpenAI 适配器 + registry
│   ├── rag\                 # FTS5 trigram 检索
│   ├── migrations\          # 001/002/003 schema
│   ├── library.py           # 多语料管理
│   └── ...
├── frontend\                # Vite + React + TS
│   ├── src\
│   │   ├── pages\           # Dashboard / Analysis / Composer / Drafts / DraftDetail / Libraries / Settings
│   │   └── api.ts           # 静态/连接双模式 client
│   └── public\data\         # 静态快照（Pages 演示模式读这里）
├── data\
│   ├── libraries\<lib_id>\xhs.db
│   └── active_library.txt
├── exports\
│   ├── analysis\v<date>.{json,html}
│   └── drafts\compose_<id>.html
└── .github\workflows\pages.yml      # 自动部署到 GitHub Pages
```

## 设计原则

1. **多 Agent 不是 N 个 drafter 投票**——是真实创作流程的拟态：先定策略，再各家起草，跨 LLM 评审，按建议改稿，再选 final。
2. **跨 LLM 评审**——drafter 和 critic 用不同模型，降低自夸偏差。
3. **artifact 是一等公民**——每次分析 / 每次生成都写 JSON，可对比、可回溯。
4. **prompt 改动须人工守门**——LLM 复盘可建议 diff，但默认进队列，需 approve 才升级版本（W4）。
5. **多 Library 隔离**——不锁死单赛道，每个 .db 独立分析、独立 RAG、独立 prompt 历史。

## 状态

- ✅ 静态 DNA 分析 + HTML 报告
- ✅ FTS5 RAG（trigram）
- ✅ 多 Agent pipeline（Claude + DeepSeek + OpenAI）
- ✅ Library 上传/切换/分析
- ✅ FastAPI + React 前端（GH Pages 静态 + 本地后端双模式）
- ⏳ W4：发布追踪、自动复盘、prompt 版本树

## 开发命令对照

| 任务 | 命令 |
|---|---|
| 装 Python deps | `pip install -r requirements.txt` |
| 装前端 deps | `cd frontend && npm install` |
| 跑后端 | `python -m studio serve` |
| 跑前端 dev | `cd frontend && npm run dev` |
| 前端 build | `cd frontend && npm run build` |
| 静态导出 | `python -m studio export-public` |
| 切库 | `python -m studio library activate <id>` |
| 一键全量分析 | `python -m studio analyze && python -m studio promote-hooks` |
