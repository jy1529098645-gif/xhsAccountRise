"""Smoke test for v0.4: full pipeline including Planner."""
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
    # Use Haiku across the board for fast/cheap smoke; v0.4 wiring is what
    # we're testing, not output quality.
    "strategist_spec": "claude:haiku",
    "drafter_spec": "claude:haiku,deepseek",
    "critic_spec": "claude:haiku",
    "refiner_spec": "claude:haiku",
    "synthesizer_spec": "claude:haiku",
    "planner_spec": "claude:haiku",
}
print("POST /api/compose...")
data = json.dumps(req).encode("utf-8")
r = urllib.request.Request(
    "http://127.0.0.1:8765/api/compose",
    data=data, headers={"Content-Type": "application/json"},
)
with urllib.request.urlopen(r, timeout=600) as resp:
    b = json.loads(resp.read().decode("utf-8"))

print(f"draft_id: {b['draft_id']}")
print(f"platform: {b['brief']['platform']}")
print(f"strategy hook: {(b.get('strategy') or {}).get('recommended_hook')}")
print()
print("drafts:")
for c in b["drafts"]:
    if c["error"]:
        print(f"  X {c['llm']}: {c['error'][:120]}")
    else:
        print(f"  + {c['llm']}: {c['payload']['title'][:50]}")
print()
if b.get("final"):
    print(f"★ final: {b['final']['payload']['title'][:60]}")
print()

# Plan checks
plan = b.get("plan") or {}
print("=== PLAN ===")
print(f"series_thesis: {plan.get('series_thesis', '')[:120]}")
print(f"publish_schedule: {len(plan.get('publish_schedule') or [])} slots")
for s in (plan.get("publish_schedule") or [])[:3]:
    print(f"  - {s.get('slot')}: median {s.get('median_likes')} · {s.get('why', '')[:60]}")
print(f"follow_up_angles: {len(plan.get('follow_up_angles') or [])} ideas")
for a in (plan.get("follow_up_angles") or [])[:5]:
    print(f"  - [{a.get('angle')}/{a.get('hook_type')}] {a.get('title')}")
print(f"engagement_tactics: {len(plan.get('engagement_tactics') or [])} items")
for t in (plan.get("engagement_tactics") or [])[:3]:
    print(f"  - {t[:80]}")
print()
print(f"elapsed: {b['totals']['elapsed_s']}s, cost ${b['totals']['cost_usd']:.4f}")
