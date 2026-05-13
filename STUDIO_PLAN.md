# AcademiCats × xhs Account Rise Studio · 主计划

> Handoff doc — paste this + the repo into a fresh Claude session to resume.
> Last updated: 2026-05-13. Repo: `H:\xhsAccountRise`.

## 0. 使命

围绕 AcademiCats（AI 学术工作台，下沉学生市场）做 **小红书起号闭环工具**。

输入：已爬的 10,453 条 xhs notes / 1,497 条评论 / 1,042 作者（在 `data/xhs.db`，自 `H:\xhs\data\xhs.db` 复制而来）。

输出：从语料中提取爆款 DNA → 用混合 LLM（Claude + DeepSeek + 其他参考）出稿 → 追踪发布后互动 → LLM 自动复盘 → prompt / 模板自学习。

**关键约束**：xhs 没有 publish API，所以本工具到「导出可发布稿件」为止；发布回流靠用户手动粘 URL，之后复用现有 `crawler.httpx_detail` 路径定时刷计数。

## 1. 已完成（W1）

- ✅ DB 复制到 `data/xhs.db`（50MB）
- ✅ `studio/` 子包 + migrations 系统（`studio_*` 表全套）
- ✅ 爆款 DNA 提取（无 LLM）：
  - 标题 hook 分类（13 类规则引擎）
  - 正文长度 / 图片数 / 视频 vs 图文 × 互动
  - 发布时间 7×24 热力图
  - tag 频次 + 共现
  - 关键词蓝海排行（avg_likes / log2(n+2)）
  - 评论需求挖掘（求/怎么/有没有 等模板）
  - Top performers (likes / collects / comments / collect rate)
- ✅ HTML 报告渲染（静态、self-contained、可推 GitHub Pages）
- ✅ CLI: `python -m studio {migrate,analyze,render,status}`

**当前 DNA 报告**：`exports/analysis/v2026-05-14.{json,html}`
- 9,729 titled notes 分析
- 工具型 24.3% / 数字型 16.7% / 故事型 10% / 教程型 6.9% (top hooks)
- 蓝海 Top: 研究生 (score 535) / 科研工具 (360) / 毕业论文 (338) / ChatGPT 写论文 (292)

## 2. 接下来（W2 — 内容生成 pipeline）

### W2.1 LLM 适配器
- `studio/generators/base.py` — `Generator` 抽象类 + `GeneratedCandidate` dataclass
- `studio/generators/claude.py` — Anthropic SDK，model=`claude-opus-4-7`
- `studio/generators/deepseek.py` — OpenAI SDK，base_url=`https://api.deepseek.com`, model=`deepseek-chat`

### W2.2 RAG（TF-IDF / FTS5 baseline）
- 用 SQLite FTS5 建虚表覆盖 notes(title+body) + comments(content)
- 检索：给定 brief 主题 → top-K 相似高赞 notes + 相关评论 + 同类 hook 模板
- 向量召回（embedding 模型）放 W3，TF-IDF 足够 v0.1

### W2.3 Brief schema + 编排
- `studio/brief.py` — `Brief` dataclass: topic / angle / target_length / cta_strength / niche / reference_note_ids
- `studio/generators/orchestrator.py` — async 并发调多 LLM，结果入 `studio_drafts` + `studio_draft_candidates`
- 每个 candidate：title / body / tags / cover_prompt / hook_type / predicted_likes / self_score / self_critique

### W2.4 CLI
- `python -m studio generate --topic "降AI率技巧" --angle 教程 --length 800 --llms claude,deepseek`
- 输出：draft_id + 候选对比 HTML（`exports/drafts/{draft_id}.html`）

### W2.5 候选对比报告
- 并排显示各 LLM 输出
- 按 self_score 排序
- 一键标记 chosen / 人工评分 1-5

## 3. W3 — Web UI

技术栈：Vite + React + TanStack Query。后端 FastAPI（封装 studio CLI 能力）。本地开发直连 `http://localhost:8000`；构建产物 push 到 `gh-pages` 分支看历史 artifacts。

5 个页面：Dashboard / Analysis Lab / Composer / My Posts / Prompt Lab（详见对话第一回合的"4. 前端 5 页"）。

## 4. W4 — 追踪 + 复盘 + 自学习

- APScheduler 定时刷新 `studio_my_posts` 互动数据（复用 `crawler.httpx_detail`）
- 满 48h 后自动跑复盘 LLM → 写 `studio_retrospectives`
- prompt diff 建议进"待审核队列"，人工 approve 才升级 `studio_prompt_versions`
- hook_template 权重根据采用率 × 实际互动自动调整

## 5. 目录结构

```
H:\xhsAccountRise\
├── data\xhs.db                  # ← copied from H:\xhs
├── studio\
│   ├── __main__.py              # CLI entry
│   ├── config.py                # paths, env keys
│   ├── db.py                    # DAO, migrations runner
│   ├── migrations\001_init.sql  # studio_* tables
│   ├── analysis\
│   │   ├── hooks.py             # title hook classifier (13 categories)
│   │   ├── extract_dna.py       # full statistical pipeline
│   │   └── render_report.py     # JSON → HTML
│   ├── rag\                     # W2: FTS5 retrieval
│   ├── generators\              # W2: claude / deepseek adapters
│   ├── tracking\                # W4: my posts metric refresh
│   └── feedback\                # W4: retro + prompt versioning
├── exports\
│   ├── analysis\v{date}.{json,html}
│   └── drafts\{draft_id}.html
├── scripts\                     # one-off devtools (db_stats, inspect_schema)
├── frontend\                    # W3
└── STUDIO_PLAN.md
```

## 6. 常用命令

```powershell
# 一键全量分析（重跑随时安全，artifact 按日 versioned）
$env:PYTHONPATH='H:\xhsAccountRise'
& 'H:\xhs\.venv\Scripts\python.exe' -m studio analyze

# 数据快照
& 'H:\xhs\.venv\Scripts\python.exe' -m studio status

# 重新渲染历史 artifact
& 'H:\xhs\.venv\Scripts\python.exe' -m studio render exports\analysis\v2026-05-14.json

# 应用未运行的 migration
& 'H:\xhs\.venv\Scripts\python.exe' -m studio migrate
```

## 7. 设计原则

1. **统计先行，LLM 后到**：W1 全部 stats，零 API 成本。LLM 只在 W2+ 生成 / 复盘介入。
2. **artifact 是一等公民**：每次分析产 `v{date}.json` + `.html`，可对比、可回溯、可静态部署。
3. **prompt diff 必须人工守门**：避免 LLM 自循环跑偏。复盘只产建议，不自动 apply。
4. **复用爬虫 stack**：发布追踪走 `crawler.httpx_detail`（自己发的 note 有完整权限）。
5. **多 LLM 对比常态化**：不押注单家。同一 brief 用 Claude + DeepSeek + GPT-5/Gemini/Kimi 并发出稿，人工挑。

## 8. 已知 / 待办

- [ ] hook 规则 27% 落 "其他"——多数是纯主题词（"论文修改"），不是 hook 失败。W2 用 LLM 兜底分类二次确认。
- [ ] DB 中 `tags_json` 形态混杂（dict / str / 空），已在 `analyse_tags` 做归一化。
- [ ] 评论需求挖掘当前是正则匹配；W2 用 LLM 聚类一遍出更高质量的 product backlog。
- [ ] 蓝海得分公式简单（`avg/log2(n+2)`），目前能用，W3 看板可加多维 filter。
- [ ] 视频 note 仅 32 条，统计意义不大；当前数据 99.7% 是图文。
