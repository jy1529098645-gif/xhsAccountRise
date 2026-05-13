"""Pretty-print a saved schema_map.json so we can verify AI mapping quality."""
import io, json, sys
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

lib_id = sys.argv[1] if len(sys.argv) > 1 else "douyin-test"
p = Path(f"H:/xhsAccountRise/data/libraries/{lib_id}/schema_map.json")
if not p.exists():
    print("no schema_map.json at", p); sys.exit(0)

m = json.loads(p.read_text(encoding="utf-8"))
for table in ("notes", "comments"):
    spec = m.get(table)
    if not spec:
        print(f"-- {table}: no mapping")
        continue
    print(f"-- {table} from {spec.get('source_table')!r}")
    for k, v in (spec.get("columns") or {}).items():
        src = (v or {}).get("source")
        ex = (v or {}).get("expr")
        print(f"   {k:20s}  source={src!r:18s}  expr={ex!r}")
    if spec.get("extra_filters"):
        print(f"   FILTER: {spec['extra_filters']}")
if m.get("reasoning"):
    print(f"-- reasoning: {m['reasoning']}")
