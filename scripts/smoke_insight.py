"""Smoke test for /api/insight/run (Claude × OpenAI debate)."""
import io, json, sys, urllib.request, urllib.error
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")


def call(method, path, body=None, timeout=600):
    url = f"http://127.0.0.1:8765{path}"
    if body is None:
        req = urllib.request.Request(url, method=method)
    else:
        req = urllib.request.Request(
            url, method=method,
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


# Confirm projects + libraries.
projs = call("GET", "/api/projects")
print("projects:", projs["active"], "→", [(p["project_id"], p["name"]) for p in projs["projects"]])
libs = call("GET", "/api/libraries")
print("libraries (current project):", [(l["lib_id"], l["platform"], l["notes_count"]) for l in libs])

lib_id = libs[0]["lib_id"]
print()
print(f"=== POST /api/insight/run (lib={lib_id}, Haiku for both) ===")
r = call("POST", "/api/insight/run", {
    "library_id": lib_id,
    "claude_spec": "claude:haiku",
    "openai_spec": "openai",  # GPT-4o
    "moderator_spec": "claude:haiku",
}, timeout=600)

print(f"report_id: {r['report_id']}, elapsed: {r['elapsed_s']}s, status: {r['status']}")
c = r.get("consensus") or {}
print(f"title: {c.get('title')}")
print(f"executive_summary: {(c.get('executive_summary') or '')[:160]}")
print()
print(f"consensus_findings ({len(c.get('consensus_findings') or [])}):")
for f in (c.get("consensus_findings") or [])[:5]:
    print(f"  · {f.get('title')}")
    print(f"    evidence: {(f.get('evidence') or '')[:100]}")
print(f"consensus_opportunities ({len(c.get('consensus_opportunities') or [])}):")
for o in (c.get("consensus_opportunities") or [])[:5]:
    print(f"  · {o.get('opportunity')} :: {(o.get('suggested_angle') or '')[:80]}")
print(f"single_side_views ({len(c.get('single_side_views') or [])}):")
for v in (c.get("single_side_views") or [])[:5]:
    print(f"  · [{v.get('side')}] {(v.get('point') or '')[:90]}")
print(f"charts_to_show: {c.get('charts_to_show')}")
