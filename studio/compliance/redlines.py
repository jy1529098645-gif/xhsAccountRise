"""Red-line dictionary — sourced from策略报告 6.4 雷区清单 + safe替代词表.

Each rule:
    patterns:        list of regex strings (case-insensitive, Chinese-friendly).
                     Match wins → hit.
    category:        '学术诚信' / '违规承诺' / '违禁词' / '检测规避' / '隐性导流'
    severity:        'block' (instant封号/限流) | 'warn' (谨慎)
    safe_alternative: replacement phrase that the autorewrite will substitute.
                     Pick the first one as the default.
    rationale:       why this is banned (shown to user when a hit fires).

Adding new rules: append to REDLINES. Do NOT remove old rules without a deprecation
window — agents/pipeline.py persists hits keyed by `term`, so removing rules
breaks historical reporting.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


Severity = str  # 'block' | 'warn'


@dataclass(frozen=True)
class RedlineRule:
    rule_id: str
    patterns: tuple[str, ...]
    category: str
    severity: Severity
    safe_alternative: str
    rationale: str

    def compile(self) -> list[re.Pattern]:
        return [re.compile(p, re.IGNORECASE) for p in self.patterns]


# ⚠️  Order matters: earlier rules apply first when overlapping matches occur.
#     Block-severity rules should be listed before warn-severity for the same
#     family of terms (so the harsher hit wins).
#
# Why the negative lookbehinds are this ugly:
#   代写 / 包过 / 降到 0 / 枪手 are all suffixes of legitimate compound words in
#   Chinese (现代写 / 红包过 / 把成本降到 0 / 玩枪手感). The original v0.53 patterns
#   without context guards generated ~50% false positives on realistic copy.
#   v0.53.1 adds:
#     - fixed-width negative lookbehind for time-period prefixes (现/古/当/年/时/朝/近/后/世/几)
#       on 代写/代笔/代做 (which all suffer the same 现代+verb false positive)
#     - negative lookbehind for common 包-compounds (红/福/礼/面/荷/钱/皮/腰/背/书/牙/吊/挎/邮)
#       on 包过 — covers 红包/面包/钱包/etc. + 过期/过滤
#     - negative lookahead (?!记|本) on 代笔 so 代笔记录 doesn't fire
#     - drops bare 枪\s*手 (catches sports/games) — keeps 找枪手 / 请枪手
#     - tightens 降到 0 to require %  or 率 suffix (essay/AI context guarantee)
#     - tightens 100%过 to require 稿|关|审|线 suffix (drops 100% 过敏 etc)
REDLINES: tuple[RedlineRule, ...] = (
    # --- 学术诚信 / 代写灰产 (策略报告 6.4 一类红线) -------------------------
    RedlineRule(
        rule_id="ghostwriting",
        patterns=(
            # 代写 — guard against 现代写/古代写/当代写/年代写/时代写/朝代写/近代写/后代写/世代写/几代写
            r"(?<![现古当年时朝近后世几])代\s*写",
            # 代笔 — same period guard; reject 代笔记 (代笔+记录) and 代笔本 (sketchpad)
            r"(?<![现古当年时朝近后世几])代\s*笔(?!记|本)",
            # 代做 — same period guard
            r"(?<![现古当年时朝近后世几])代\s*做",
            # 找枪手 / 请枪手 — bare 枪手 is too sport-game heavy
            r"找\s*枪\s*手",
            r"请\s*枪\s*手",
        ),
        category="学术诚信",
        severity="block",
        safe_alternative="AI 辅助写作框架",
        rationale="小红书对「代写」是严重违规词，触发立刻限流/封号。改写为 AI 辅助/工具属性。",
    ),
    RedlineRule(
        rule_id="guaranteed_pass",
        patterns=(
            # 包过 — guard against 红包/福包/礼包/面包/荷包/钱包/皮包/腰包/背包/书包/牙包/吊包/挎包/邮包
            r"(?<![红福礼面荷钱皮腰背书牙吊挎邮])包\s*过",
            r"保\s*过",
            r"稳\s*过",
            r"100\s*%\s*通过",
            # 100%过-suffix — must end in 稿|关|审|线 (drop bare 过敏/过期)
            r"100\s*%\s*过(?:稿|关|审|线)",
        ),
        category="违规承诺",
        severity="block",
        safe_alternative="高通过率 / 已帮 N 同学成功",
        rationale="平台规则禁止结果保证类承诺。用「高通过率」+ 历史成功案例代替。",
    ),
    RedlineRule(
        rule_id="ai_rate_to_zero",
        patterns=(
            # AI率到0 / AI率0% — explicit AI mention in pattern
            r"[AaＡａ]\s*[IiＩｉ]\s*率?\s*(?:降|到)\s*到?\s*0",
            r"降\s*[AaＡａ]\s*[IiＩｉ]\s*率?\s*到?\s*0",
            r"AI\s*率\s*0\s*%?",
            r"AIGC?\s*率?\s*0\s*%?",
            # 降到 0 — generic, requires % or 率 suffix so 把成本降到0 doesn't fire
            r"降\s*到\s*0\s*(?:%|率)",
        ),
        category="检测规避",
        severity="block",
        safe_alternative="降到个位数 / 学术表达自然度提升",
        rationale="承诺降到 0 是检测规避表达，触发平台学术诚信审查。用「个位数」更安全。",
    ),
    RedlineRule(
        rule_id="bypass_detector",
        patterns=(r"绕\s*过\s*(?:查重|Turnitin|AIGC|AI\s*检测)",
                  r"破\s*解\s*(?:Turnitin|查重|AIGC)",
                  r"Turnitin\s*不\s*抓",
                  r"AIGC?\s*不\s*抓",
                  r"查\s*不\s*出来",
                  r"规\s*避\s*(?:检测|查重)"),
        category="检测规避",
        severity="block",
        safe_alternative="通过 Turnitin / 重复率友好 / 引用规范检查",
        rationale="任何「绕过/破解检测」表达都是学术不端的明示证据，平台与学校双面雷。",
    ),
    RedlineRule(
        rule_id="buy_paper",
        patterns=(r"买\s*(?:论文|essay|assignment)", r"卖\s*论文",
                  r"论文\s*出售", r"定\s*制\s*论文"),
        category="违禁词",
        severity="block",
        safe_alternative="购买使用权 / 订阅服务 / 论文工具",
        rationale="买卖论文是「学术不端 + 商业违规」双重雷区。",
    ),
    RedlineRule(
        rule_id="full_output",
        patterns=(r"全\s*文\s*直\s*出", r"不\s*用\s*自\s*己\s*写",
                  r"一\s*键\s*生\s*成\s*整\s*篇\s*论文"),
        category="学术诚信",
        severity="block",
        safe_alternative="用户定方向 / AI 辅助整理与修改",
        rationale="承诺「不用自己写」直接踩学术不端定义线。",
    ),

    # --- 隐性导流 / 站外引流 ----------------------------------------------
    RedlineRule(
        rule_id="external_contact",
        # 微信号/QQ 群 — 完整数字 ID 的形式。
        patterns=(
            r"VX[：:\s]*[A-Za-z0-9_\-]{4,}",
            r"微信\s*号?[：:\s]*[A-Za-z0-9_\-]{4,}",
            r"QQ\s*群[：:\s]*\d{5,}",
            r"加\s*我\s*[VWvw][Xx]",
        ),
        category="隐性导流",
        severity="warn",
        safe_alternative="主页简介自取 / 私聊看主页",
        rationale="正文出现外联会被限流。要么藏在主页简介，要么用谐音「A-c-a-d-e-m-i」拆开。",
    ),

    # --- 软风险 / 过度承诺 -------------------------------------------------
    RedlineRule(
        rule_id="exaggerated_speed",
        patterns=(r"\d+\s*分钟\s*(?:写完|搞定|完成)\s*(?:整篇|全篇)?\s*(?:论文|essay)",
                  r"一\s*小时\s*写\s*完\s*论文"),
        category="违规承诺",
        severity="warn",
        safe_alternative="X 分钟跑通前期流程 / 框架阶段",
        rationale="夸张时间承诺易被认定为虚假宣传。改成「框架阶段」更合规。",
    ),
    RedlineRule(
        rule_id="absolute_claims",
        patterns=(r"绝\s*对\s*(?:不会|不被|安全)",
                  r"100\s*%\s*(?:安全|放心|不会)",
                  r"零\s*风\s*险"),
        category="违规承诺",
        severity="warn",
        safe_alternative="显著降低风险 / 实测安全",
        rationale="平台对绝对化用语是默认风控规则。",
    ),
)


def all_compiled() -> list[tuple[RedlineRule, list[re.Pattern]]]:
    """Pre-compile patterns once; called from check.py at import."""
    return [(r, r.compile()) for r in REDLINES]


def as_dicts() -> list[dict]:
    """For /api/compliance/rules — let the frontend show the redline catalogue."""
    return [
        {
            "rule_id": r.rule_id,
            "patterns": list(r.patterns),
            "category": r.category,
            "severity": r.severity,
            "safe_alternative": r.safe_alternative,
            "rationale": r.rationale,
        }
        for r in REDLINES
    ]
