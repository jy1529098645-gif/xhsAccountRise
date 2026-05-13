"""Smoke test for /api/strategy/autofill (Claude×OpenAI starter brief)."""
import io, json, sys, urllib.request
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

req = {
    "personal_hint": "",
    "constraints_hint": "",
    "claude_spec": "claude:haiku",
    "openai_spec": "openai",
    "moderator_spec": "claude:haiku",
}
print("POST /api/strategy/autofill ...")
data = json.dumps(req).encode("utf-8")
r = urllib.request.Request(
    "http://127.0.0.1:8765/api/strategy/autofill", data=data,
    headers={"Content-Type": "application/json"},
)
with urllib.request.urlopen(r, timeout=300) as resp:
    body = json.loads(resp.read().decode("utf-8"))

inp = body["input"]
print(f"elapsed: {body['elapsed_s']}s")
print()
print("=== PROPOSED STARTER BRIEF ===")
print(f"  定位:  {inp['positioning']}")
print(f"  受众:  {inp['target_audience']}")
print(f"  周期:  {inp['cycle_weeks']} 周")
print(f"  频率:  {inp['posts_per_week']} 篇/周")
print(f"  平台:  {inp['platform']}")
print(f"  个人优势提示: {inp.get('personal_strengths') or '(空)'}")
print()
print("=== FIELD RATIONALE ===")
for k, v in (body.get("field_rationale") or {}).items():
    print(f"  · {k} [{v.get('source')}]: {(v.get('rationale') or '')[:140]}")
    if v.get("alternatives"):
        print(f"      alts: {v['alternatives']}")
print()
print("=== CONSENSUS NOTES ===")
for n in (body.get("consensus_notes") or [])[:5]:
    print(f"  · {n[:160]}")
print()
print("=== SINGLE-SIDE VIEWS ===")
for v in (body.get("single_side_views") or [])[:5]:
    print(f"  · [{v.get('side')}] {v.get('field')}: {(v.get('point') or '')[:120]}")
