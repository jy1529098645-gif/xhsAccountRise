"""Smoke test: /api/strategy/propose + /api/strategy/{id}/expand."""
import io
import json
import sys
import urllib.request

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")


def post(path: str, body: dict, timeout: int = 600):
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        f"http://127.0.0.1:8765{path}", data=data,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


print("=== Phase 1: propose (Haiku) ===")
propose_resp = post("/api/strategy/propose", {
    "positioning": "留学生写论文工具种草",
    "target_audience": "赶 ddl 的留学生 + 毕业论文党",
    "cycle_weeks": 4,
    "posts_per_week": 3,
    "personal_strengths": "已经用 ChatGPT 写过 5 篇论文，有真实降重案例",
    "constraints": "前 2 周不带商品",
    "positioner_spec": "claude:haiku",
})
print(f"pack_id: {propose_resp['pack_id']}")
print(f"elapsed: {propose_resp['elapsed_s']}s")
print(f"directions ({len(propose_resp['directions'])}):")
for i, d in enumerate(propose_resp["directions"]):
    print(f"  [{i}] {d['name']} (score {d['score']:.1f})")
    print(f"      → {d['positioning_statement'][:80]}")
    print(f"      受众: {d['target_audience'][:60]}")
    print(f"      hooks: {d['hook_angles'][:3]}")
print()

pack_id = propose_resp["pack_id"]
chosen = 0
print(f"=== Phase 2: expand (pick #{chosen}, all Haiku) ===")
expand_resp = post(f"/api/strategy/{pack_id}/expand", {
    "chosen_direction_idx": chosen,
    "topicgen_spec": "claude:haiku,deepseek",
    "scheduler_spec": "claude:haiku",
    "resourcer_spec": "claude:haiku",
}, timeout=600)
print(f"elapsed: {expand_resp['elapsed_s']}s")
print(f"topic candidates (across LLMs): {expand_resp['topic_candidate_count']}")
if expand_resp.get("topicgen_errors"):
    for e in expand_resp["topicgen_errors"]:
        print(f"  ! topicgen err: {e[:100]}")
if expand_resp.get("scheduler_error"):
    print(f"  ! scheduler err: {expand_resp['scheduler_error'][:140]}")
pack = expand_resp["pack"]
print()
print(f"series_thesis: {pack['series_thesis'][:140]}")
print(f"weekly_themes ({len(pack['weekly_themes'])}):")
for w in pack["weekly_themes"]:
    print(f"  W{w['week']} [{w['intent']}]: {w['theme'][:80]}")
print()
print(f"schedule ({len(pack['schedule'])} slots):")
for s in pack["schedule"][:6]:
    print(f"  W{s['week']} {s['publish_slot'] or 'TBD'} | {s['title'][:50]}")
    print(f"     {s['angle']}/{s['hook_type']} · 材料: {', '.join(s['materials_needed'][:3])}")
print(f"  ... +{max(0, len(pack['schedule']) - 6)} more")
print()
print(f"materials_checklist ({len(pack['materials_checklist'])}):")
for m in pack["materials_checklist"][:5]:
    print(f"  · {m[:100]}")
print(f"risks_and_mitigations ({len(pack['risks_and_mitigations'])}):")
for r in pack["risks_and_mitigations"][:3]:
    print(f"  · {r[:100]}")
print(f"success_metrics ({len(pack['success_metrics'])}):")
for m in pack["success_metrics"][:3]:
    print(f"  · {m[:100]}")
