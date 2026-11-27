#!/usr/bin/env python3
"""Check index/<ds>/MANIFEST.json: files exist and sizes match. Usage: verify_index.py [index_dir]"""
import json
import sys
from pathlib import Path

d = Path(sys.argv[1] if len(sys.argv) > 1 else "index")
bad = 0
for m in sorted(d.glob("*/MANIFEST.json")):
    info = json.loads(m.read_text(encoding="utf-8"))
    for f in info.get("files", []):
        p = m.parent / f["path"]
        if not p.exists() or p.stat().st_size != f.get("bytes", p.stat().st_size):
            print(f"BAD  {m.parent.name}/{f['path']}")
            bad += 1
    print(f"{'ok ' if not bad else 'ERR'}  {m.parent.name}: {len(info.get('files', []))} files, built {info.get('built', '?')}")
if not list(d.glob("*/MANIFEST.json")):
    print(f"No MANIFEST.json under {d}/*/ : nothing indexed yet.")
    bad = 1
sys.exit(1 if bad else 0)
