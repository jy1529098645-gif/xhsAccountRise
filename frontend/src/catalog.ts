// Human-friendly catalog of LLMs, agent roles, platform expectations.
// All user-facing labels live here — pages should never display raw model
// strings or spec syntax.

export interface LlmOption {
  id: string;          // spec id used by the backend, e.g. "claude:opus"
  label: string;       // user-visible
  family: "anthropic" | "deepseek" | "openai";
  hint: string;        // one-line capability description
  cost: "high" | "mid" | "low";
}

export const LLM_CATALOG: LlmOption[] = [
  { id: "claude:opus",   label: "Claude Opus 4.7",       family: "anthropic", hint: "最强综合 · 强逻辑 / 长上下文 / 中文好",         cost: "high" },
  { id: "claude:sonnet", label: "Claude Sonnet 4.6",     family: "anthropic", hint: "平衡型 · 比 Opus 快 5×、便宜 5×，质量足够",  cost: "mid"  },
  { id: "claude:haiku",  label: "Claude Haiku 4.5",      family: "anthropic", hint: "最快最便宜，做粗稿/快速迭代很合适",            cost: "low"  },
  { id: "deepseek",      label: "DeepSeek V3 (chat)",    family: "deepseek",  hint: "中文下沉感最强 · 价格亲民",                    cost: "low"  },
  { id: "deepseek:reasoner", label: "DeepSeek R1 (reasoner)", family: "deepseek", hint: "强推理但偏慢，适合 Strategist / Critic",  cost: "mid"  },
  // OpenAI lineup ：bare "openai" 默认走 env 的 OPENAI_MODEL（仓库默认 gpt-5）。
  // 想显式钉死哪个就用下面的 :gpt-5 / :gpt-5-mini / :gpt-5-nano / :gpt-4o。
  { id: "openai",          label: "OpenAI GPT-5 (默认)",   family: "openai", hint: "最新旗舰 · 综合能力最强 · 通用首选",                cost: "high" },
  { id: "openai:gpt-5",    label: "OpenAI GPT-5",          family: "openai", hint: "显式钉 GPT-5（与上面同款，env 不会改）",              cost: "high" },
  { id: "openai:gpt-5-mini", label: "OpenAI GPT-5 mini",   family: "openai", hint: "GPT-5 系列 · 快 ~3× / 便宜 ~10×，质量近似",          cost: "low"  },
  { id: "openai:gpt-5-nano", label: "OpenAI GPT-5 nano",   family: "openai", hint: "GPT-5 系列 · 最快最便宜，适合粗稿 / 大规模并发",      cost: "low"  },
  { id: "openai:gpt-4o",   label: "OpenAI GPT-4o (老版本)", family: "openai", hint: "兼容老链路 · 多样性视角 · 想法跳脱",                cost: "mid"  },
];

export type AgentRoleId =
  | "strategist" | "drafter" | "critic" | "refiner" | "synthesizer" | "planner";

export interface AgentRoleSpec {
  id: AgentRoleId;
  label: string;            // 中文友好名
  multi: boolean;           // true = pool (multi-select), false = single LLM
  description: string;      // one-line
  rationale: string;        // why this role matters (1-2 sentences)
  whatItProduces: string;   // single phrase shown as the "output"
  defaultIds: string[];     // default selected LLM spec ids
  canSkip: boolean;
}

export const AGENT_ROLES: AgentRoleSpec[] = [
  {
    id: "strategist", label: "策略师",
    multi: false,
    description: "先给整体方向：用什么 hook、开头怎么钩、结构骨架、避坑点",
    rationale: "一锤定音，避免下面各家 AI 各写各的散架",
    whatItProduces: "策略 (hook 类型 / 开头钩子 / 结构 / 避坑)",
    defaultIds: ["openai"],
    canSkip: true,
  },
  {
    id: "drafter", label: "起草团",
    multi: true,
    description: "多家 AI 并发起草，每家产一份候选",
    rationale: "跨模型多样性。GPT 想法跳 / DeepSeek 下沉感 / Claude 严谨",
    whatItProduces: "N 份候选稿件",
    defaultIds: ["openai"],
    canSkip: false,
  },
  {
    id: "critic", label: "审稿团",
    multi: true,
    description: "跨家给每份候选打 5 维分（hook / 语言 / 转发 / 品牌 / 结构）",
    rationale: "刻意挑跟起草不同的家来审，避免 AI 自夸偏差",
    whatItProduces: "每份候选的评分 + 风险点 + 改进建议",
    defaultIds: ["deepseek"],
    canSkip: true,
  },
  {
    id: "refiner", label: "改稿师",
    multi: false,
    description: "拿评分最高的候选 + 审稿团反馈 → 重写",
    rationale: "针对性修缺陷，但保持原 hook 类型不偏题",
    whatItProduces: "改稿后的候选",
    defaultIds: ["openai"],
    canSkip: true,
  },
  {
    id: "synthesizer", label: "融合师",
    multi: false,
    description: "看完所有候选 + 评分 + 改稿 → 综合各家优点写最终稿",
    rationale: "★ 核心步骤 ★ 取 A 的标题、B 的金句、C 的结构融合成一篇",
    whatItProduces: "最终融合稿 (含 rationale：从哪家取的什么)",
    defaultIds: ["openai"],
    canSkip: true,
  },
  {
    id: "planner", label: "计划师",
    multi: false,
    description: "结合最终稿 + 历史发布时段热力图 + 评论原话",
    rationale: "不止给内容，还给「什么时候发、接下来发什么、评论怎么运营」",
    whatItProduces: "执行计划 (发布时段 / 后续选题 / 互动话术)",
    defaultIds: ["deepseek"],
    canSkip: true,
  },
];

// "省钱预设" — same agents but all switched to cheaper models.
// v0.51: default is now GPT-4o + DeepSeek (Claude removed from defaults due
// to cost). Claude presets remain available for users who want them.
export const COST_PRESETS: Record<string, Record<AgentRoleId, string[]>> = {
  "默认 (4o + DeepSeek ★ 性价比最高)": {
    strategist: ["openai"],
    drafter: ["openai"],
    critic: ["deepseek"],
    refiner: ["openai"],
    synthesizer: ["openai"],
    planner: ["deepseek"],
  },
  "极致省钱 (全 DeepSeek)": {
    strategist: ["deepseek"],
    drafter: ["deepseek"],
    critic: ["deepseek"],
    refiner: ["deepseek"],
    synthesizer: ["deepseek"],
    planner: ["deepseek"],
  },
  "多样性 (4o + DeepSeek 起草)": {
    strategist: ["openai"],
    drafter: ["openai", "deepseek"],
    critic: ["deepseek"],
    refiner: ["openai"],
    synthesizer: ["openai"],
    planner: ["deepseek"],
  },
  "Claude 全开 (Opus 顶配 · 贵)": {
    strategist: ["claude:opus"],
    drafter: ["claude:opus", "deepseek", "openai"],
    critic: ["claude:sonnet", "deepseek"],
    refiner: ["claude:opus"],
    synthesizer: ["claude:opus"],
    planner: ["claude:opus"],
  },
  "Claude 省钱 (Sonnet 全开)": {
    strategist: ["claude:sonnet"],
    drafter: ["claude:sonnet", "deepseek"],
    critic: ["claude:haiku", "deepseek"],
    refiner: ["claude:sonnet"],
    synthesizer: ["claude:sonnet"],
    planner: ["claude:sonnet"],
  },
};

// ---- Platform expectations -----------------------------------------------

export interface PlatformGuide {
  id: string;
  label: string;
  emoji: string;
  bestSource: string;        // best origin of compatible .db files
  expectedTables: string;    // "notes + comments" etc.
  keyFields: string[];       // critical column names
  contentExamples: string;   // what each row should contain
  notes?: string;            // caveats / known sources
}

export const PLATFORM_GUIDES: PlatformGuide[] = [
  {
    id: "xiaohongshu",
    label: "小红书",
    emoji: "📕",
    bestSource: "xhs-spider / NanmiCoder/MediaCrawler / 自建 curl_cffi 爬虫",
    expectedTables: "notes (主表) + comments",
    keyFields: ["note_id", "title", "body", "liked_count", "collected_count", "comment_count", "tags_json", "publish_time_ms"],
    contentExamples: "每行 1 篇笔记。title 是小红书标题，body 是正文（含 emoji / 分点），互动数取小红书显示值",
    notes: "推荐：xhs 反爬重，建议用 curl_cffi+chrome131 或 MediaCrawler 抓 SSR HTML。",
  },
  {
    id: "douyin",
    label: "抖音",
    emoji: "🎵",
    bestSource: "MediaCrawler/DouYinSpider / TikTokApi (跨境) / Apify",
    expectedTables: "notes (用 video 表映射) + comments",
    keyFields: ["note_id (= aweme_id)", "title (= video desc)", "body (= 副标/合集说明)", "liked_count (= digg_count)", "comment_count", "share_count"],
    contentExamples: "一条短视频 → 一行。title 用 desc（视频文案），body 留空或填合集介绍。",
    notes: "抖音没有「正文」概念，body 字段可留空。多 AI 写出稿默认会按短视频脚本风。",
  },
  {
    id: "kuaishou",
    label: "快手",
    emoji: "📹",
    bestSource: "MediaCrawler/KuaiShouSpider",
    expectedTables: "notes (用 photo 表映射) + comments",
    keyFields: ["note_id (= photo_id)", "title (= caption)", "liked_count", "comment_count"],
    contentExamples: "每行一条快手作品。title 用 caption，body 留空。",
    notes: "下沉感最强的平台，AI 出稿会偏老铁文化、接地气。",
  },
  {
    id: "bilibili",
    label: "B站",
    emoji: "📺",
    bestSource: "bilibili-api / MediaCrawler / 自建 BVID 爬虫",
    expectedTables: "notes (用 video/dynamic 表映射) + comments",
    keyFields: ["note_id (= bvid 或 dynamic_id)", "title", "body (= 视频简介或动态正文)", "liked_count", "collected_count (= favorite)", "comment_count"],
    contentExamples: "视频或动态均可。视频用标题+简介，动态用 dynamic_text 当 body。",
    notes: "二次元 + 长内容 + 系统性。可上传弹幕到 comments 表当评论用。",
  },
  {
    id: "youtube",
    label: "YouTube",
    emoji: "🎬",
    bestSource: "youtube-data-api / yt-dlp metadata + comments",
    expectedTables: "notes (= video) + comments",
    keyFields: ["note_id (= video_id)", "title", "body (= description)", "liked_count (= like_count)", "comment_count (= comment_count)"],
    contentExamples: "title 是视频名，body 是 description（可带时间戳/链接）。",
    notes: "AI 出稿会带 intro/outline/CTA 订阅风。国际化建议混英文。",
  },
  {
    id: "reddit",
    label: "Reddit",
    emoji: "🤖",
    bestSource: "praw (Python Reddit API Wrapper) / Pushshift archive",
    expectedTables: "notes (= submission) + comments",
    keyFields: ["note_id (= submission_id)", "title", "body (= selftext)", "liked_count (= upvotes)", "comment_count (= num_comments)"],
    contentExamples: "title 是 post title，body 是 selftext（长文 ok），comments 是评论。",
    notes: "长文论证体，禁营销腔。AI 出稿会自动写得克制 + 提供论据。",
  },
  {
    id: "x",
    label: "X / Twitter",
    emoji: "𝕏",
    bestSource: "twscrape / tweepy / snscrape (旧)",
    expectedTables: "notes (= tweet) + comments (= reply tree)",
    keyFields: ["note_id (= tweet_id)", "title (合并第一段)", "body (= 完整 tweet text)", "liked_count (= favorite_count)", "share_count (= retweet_count)"],
    contentExamples: "单条推文。如果是 thread，把整条 thread 拼成 body。",
    notes: "极短 hook。AI 出稿会拆成可发 thread 的段落。",
  },
  {
    id: "other",
    label: "其他平台 / 自定义",
    emoji: "📦",
    bestSource: "ETL 自己的数据 → SQLite，按下方核心 schema 映射",
    expectedTables: "notes + comments (最少必须)",
    keyFields: ["note_id (必填)", "title (必填)", "body", "liked_count", "comment_count"],
    contentExamples: "把你的内容映射成「每行一篇」就行，平台风格选『其他』。",
    notes: "如果想用本工具但 schema 完全不一样，可以写个 SQL view 模拟必备字段。",
  },
];

export const PLATFORM_LABEL_MAP: Record<string, string> =
  Object.fromEntries(PLATFORM_GUIDES.map(p => [p.id, p.label]));

export const GITHUB_REPO = "https://github.com/jy1529098645-gif/xhsAccountRise";

// v0.52: angles available for both Composer (per-draft) and Strategy
// (per-slot) multi-select. Backend Brief.angles + AccountInput.expected_angles
// validate against this same list.
export const CONTENT_ANGLES: string[] = [
  "教程", "痛点", "故事", "工具评测", "对比", "感悟", "数字", "种草", "建议",
  "段子",  // v0.56: 反讽 / 自嘲 / 夸张戏谑 / 玩梗 / 沙雕。DNA 笑点信号强的库会自动分配。
  "科普",  // v0.57: 是什么/为什么知识点，distinct from 教程
  "避雷",  // v0.57: 主动警告，distinct from 痛点共鸣
  "测评",  // v0.57: 多产品横评，distinct from 工具评测单品深度
];
