"""Full-strength multi-agent smoke test:
- 3 drafters (Claude Opus + DeepSeek + GPT-4o)
- 2 critics (Claude Sonnet + DeepSeek)
- Refiner: Claude Opus
- Synthesizer (LLM-driven, fuses all): Claude Opus
"""
import io
import json
import sys
import urllib.request

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

req = {
    "topic": "降AI率技巧",
    "angle": "教程",
    "target_length": 600,
    "cta_strength": "soft",
    "niche": "降AI率",
    "strategist_spec": "claude:opus",
    "drafter_spec": "claude:opus,deepseek,openai",
    "critic_spec": "claude:sonnet,deepseek",
    "refiner_spec": "claude:opus",
    "synthesizer_spec": "claude:opus",
}
print("POST /api/compose (full strength)...")
data = json.dumps(req).encode("utf-8")
r = urllib.request.Request(
    "http://127.0.0.1:8765/api/compose",
    data=data, headers={"Content-Type": "application/json"},
)
with urllib.request.urlopen(r, timeout=600) as resp:
    b = json.loads(resp.read().decode("utf-8"))

print(f"draft_id: {b['draft_id']}")
print(f"platform: {b['brief'].get('platform')}")
strat = b.get("strategy") or {}
print(f"strategy hook: {strat.get('recommended_hook')}")
print(f"opening hook: {strat.get('opening_hook')}")
print()
print("Drafter outputs:")
for c in b["drafts"]:
    if c["error"]:
        print(f"  X {c['llm']}: {c['error'][:140]}")
    else:
        avg = c.get("critique_avg") or 0
        print(f"  + {c['llm']}: {c['payload']['title'][:60]} (self {c['payload']['self_score']:.1f}, critic avg {avg:.1f})")

if b.get("refined"):
    print(f"\nRefiner: {b['refined']['payload']['title'][:60]}")
if b.get("final"):
    print(f"\n★ Synthesizer final: {b['final']['payload']['title'][:60]}")
    print(f"   {b['final']['llm']}")
    crit = b['final']['payload'].get('self_critique', '')
    if '[synth rationale]' in crit:
        rat = crit.split('[synth rationale]')[1].strip()
        try:
            r = json.loads(rat)
            print(f"   title_from: {r.get('title_from')}")
            print(f"   body_from:  {r.get('body_from')}")
            risks = r.get('addresses_risks', [])
            if risks:
                print(f"   addresses risks: {risks}")
        except Exception:
            pass

print()
print(f"elapsed: {b['totals']['elapsed_s']}s, cost ${b['totals']['cost_usd']:.4f}")
print("\nfull body (final):")
if b.get("final"):
    print(b["final"]["payload"]["body"][:800])
