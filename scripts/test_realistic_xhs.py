"""Reproduce the user's bug: upload a small realistic xhs-like DB and ensure
the report cites *real content* not '0 notes' tropes."""
import io, json, os, sqlite3, sys, tempfile
import urllib.request as ur

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

fd, path = tempfile.mkstemp(suffix=".db")
os.close(fd)
con = sqlite3.connect(path)
# Build a realistic small xhs-format DB with actual content
con.executescript("""
CREATE TABLE notes (
    note_id TEXT PRIMARY KEY,
    title TEXT,
    body TEXT,
    type TEXT,
    author_id TEXT,
    author_nickname TEXT,
    publish_time_ms INTEGER,
    liked_count INTEGER,
    collected_count INTEGER,
    comment_count INTEGER,
    share_count INTEGER,
    image_count INTEGER,
    tags_json TEXT
);
CREATE TABLE comments (
    comment_id TEXT PRIMARY KEY,
    note_id TEXT,
    content TEXT,
    like_count INTEGER,
    publish_time_ms INTEGER
);
""")
TOPICS = [
    ("AI写论文神器，研究生救命！", "用 ChatGPT + Claude 双开，效率翻倍",
     ["论文", "AI写作", "研究生", "ChatGPT"]),
    ("降AI率绝招｜从68%降到5%", "亲测有效，连续 3 篇都过查重",
     ["降AI率", "查重", "毕业论文"]),
    ("文献综述这样写，导师秒同意", "三步法：找 → 读 → 串",
     ["文献综述", "毕业论文", "学术写作"]),
    ("DeepSeek 写论文 prompt 大全", "20 个高质量指令收藏",
     ["DeepSeek", "prompt", "AI写作"]),
    ("一晚上写完开题报告，亲测可行", "把模板 + AI 工具用到极致",
     ["开题报告", "毕业论文", "效率"]),
]
import random; random.seed(42)
for i in range(40):
    t = TOPICS[i % len(TOPICS)]
    con.execute(
        "INSERT INTO notes VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (f"n{i}",
         f"{t[0]} {i % 5 + 1}",
         t[1] + "\n\n" + ("详细内容描述。" * 30),
         "normal", f"u{i % 10}", f"作者{i % 10}",
         1700000000000 + i * 86400000,
         random.randint(50, 5000),  # likes
         random.randint(20, 2000),  # collects
         random.randint(2, 200),    # comments
         random.randint(0, 100),    # shares
         random.randint(1, 9),      # images
         json.dumps(t[2])),
    )
for i in range(80):
    con.execute(
        "INSERT INTO comments VALUES (?,?,?,?,?)",
        (f"c{i}", f"n{i % 40}",
         random.choice(["求 prompt 链接", "好用！收藏了", "怎么用啊",
                       "降重了我去试试", "求详细教程", "已私信", "蹲一下"]),
         random.randint(0, 50), 1700000000000),
    )
con.commit(); con.close()
print(f"built realistic xhs db: {os.path.getsize(path)} bytes")

# Upload
def call(m, p, body=None, headers=None, timeout=600):
    h = headers or {}
    if body is not None and not isinstance(body, bytes):
        body = json.dumps(body).encode(); h.setdefault("Content-Type", "application/json")
    req = ur.Request(f"http://127.0.0.1:8765{p}", data=body, method=m, headers=h)
    with ur.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


boundary = "----RealTest"
parts = []
for k, v in [("display_name", "realistic-xhs"), ("platform", "auto"),
             ("activate", "1"), ("analyze", "1"), ("auto_adapt", "1")]:
    parts.append(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode())
with open(path, "rb") as fh:
    blob = fh.read()
parts.append(
    f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; "
    f"filename=\"realistic-xhs.db\"\r\nContent-Type: application/octet-stream\r\n\r\n".encode()
)
parts.append(blob); parts.append(f"\r\n--{boundary}--\r\n".encode())
body = b"".join(parts)

print()
print("POST /api/libraries/import ...")
imp = call("POST", "/api/libraries/import", body=body,
           headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
print(f"  lib_id: {imp['lib_id']}")
print(f"  notes_count: {imp.get('notes_count')}")
print(f"  adapter.adapted: {imp.get('adapter', {}).get('adapted')}")
print(f"  analyzed: {imp.get('analyzed')}")
print(f"  dna_version: {imp.get('dna_version')}")
print(f"  section_errors: {list((imp.get('section_errors') or {}).keys())}")
print(f"  build_dna_error: {imp.get('build_dna_error')}")
print(f"  fts_error: {imp.get('fts_error')}")
print(f"  persist_error: {imp.get('persist_error')}")
print()

print("POST /api/insight/run (Haiku) ...")
try:
    rep = call("POST", "/api/insight/run", body={
        "library_id": imp["lib_id"],
        "claude_spec": "claude:haiku",
        "openai_spec": "openai",  # uses gpt-5 → fallback gpt-4o
        "moderator_spec": "claude:haiku",
    }, timeout=600)
    print(f"  report_id: {rep['report_id']}, elapsed: {rep['elapsed_s']}s")
    c = rep.get("consensus") or {}
    print(f"  title: {c.get('title')}")
    print(f"  executive: {(c.get('executive_summary') or '')[:280]}")
    print(f"  consensus_findings ({len(c.get('consensus_findings') or [])}):")
    for f in (c.get("consensus_findings") or [])[:5]:
        print(f"     · {f.get('title')}")
        print(f"       evidence: {(f.get('evidence') or '')[:120]}")
except Exception as e:
    print(f"  insight failed: {e}")

os.unlink(path)
