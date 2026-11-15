#!/usr/bin/env python3
"""Keep the live season folder, the staged one, and the newest few others; delete older built folders."""
import json, os, shutil, sys
root = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), '..')
keep_n = 6
seasons = os.path.join(root, 'seasons')
keep = set()
for f in ('live.json', 'staging.json'):
    p = os.path.join(root, f)
    if os.path.exists(p):
        try: keep.add(json.load(open(p)).get('folder'))
        except Exception: pass
dirs = sorted((d for d in os.listdir(seasons) if os.path.isdir(os.path.join(seasons, d))),
              key=lambda d: os.path.getmtime(os.path.join(seasons, d, 'index.json')) if os.path.exists(os.path.join(seasons, d, 'index.json')) else 0, reverse=True)
for i, d in enumerate(dirs):
    if d in keep or i < keep_n: continue
    shutil.rmtree(os.path.join(seasons, d)); print('removed', d)
