"""Smoke test: POST /api/compose and report results."""
import io
import json
import sys
import urllib.request

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

req = {
    "topic": "降AI率技巧",
    "angle": "教程",
    "target_length": 500,
    "cta_strength": "soft",
    "niche": "降AI率",
    "strategist_spec": "claude:haiku",
    "drafter_spec": "claude:haiku,openai,deepseek",
    "critic_spec": "claude:haiku",
    "refiner_spec": "claude:haiku",
}
data = json.dumps(req).encode("utf-8")
r = urllib.request.Request(
    "http://127.0.0.1:8765/api/compose",
    data=data,
    headers={"Content-Type": "application/json"},
)
with urllib.request.urlopen(r, timeout=300) as resp:
    b = json.loads(resp.read().decode("utf-8"))

print("draft_id:", b["draft_id"])
print("strategy hook:", (b.get("strategy") or {}).get("recommended_hook"))
print()
print("drafts:")
for c in b["drafts"]:
    if c["error"]:
        print(f"  X {c['llm']}: {c['error'][:120]}")
    else:
        avg = c.get("critique_avg") or 0
        title = c["payload"]["title"][:60]
        score = c["payload"]["self_score"]
        print(f"  + {c['llm']}: {title} (self {score:.1f}, avg {avg:.1f})")
if b.get("refined"):
    print(f"  -> refined: {b['refined']['payload']['title'][:60]}")
if b.get("final"):
    print(f"  * final: {b['final']['payload']['title'][:60]}")
print()
print(f"elapsed: {b['totals']['elapsed_s']}s, cost ${b['totals']['cost_usd']:.4f}")
