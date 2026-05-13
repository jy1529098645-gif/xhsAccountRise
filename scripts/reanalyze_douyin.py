"""Re-trigger DNA analyze on the existing douyin-test lib (after canonical
schema was extended) to verify all section errors clear."""
import io, json, sys, urllib.request as ur
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")


def call(m, p, body=None, timeout=300):
    h = {}
    if body is not None: body = json.dumps(body).encode(); h["Content-Type"] = "application/json"
    req = ur.Request(f"http://127.0.0.1:8765{p}", data=body, method=m, headers=h)
    with ur.urlopen(req, timeout=timeout) as r: return json.loads(r.read().decode())


# Activate douyin-test
call("POST", "/api/libraries/douyin-test/activate")
print("activated douyin-test")

# Re-run DNA via /api/libraries/{lib_id}/analyze
r = call("POST", "/api/libraries/douyin-test/analyze", timeout=120)
print(json.dumps(r, ensure_ascii=False, indent=2))
section_errors = (r.get("summary") or {}).get("section_errors") or []
print()
print("section_errors:", section_errors)
print("dna_version:", r.get("dna_version"))
