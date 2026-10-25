# -*- coding: utf-8 -*-
"""Dynamic verification D: Delete-key binding scope, encoding, trace API, break semantics, O(n*m)."""
import importlib.util, json, os, sys, time, tkinter

SRC = r"D:\QA\Quest3bs\源代码_原始版.py"
spec = importlib.util.spec_from_file_location("target_app", SRC)
m = importlib.util.module_from_spec(spec); sys.modules["target_app"] = m; spec.loader.exec_module(m)

print("=== [1] tkinter trace API facts ===")
print("  Variable.trace is Variable.trace_variable ->", tkinter.Variable.trace is tkinter.Variable.trace_variable)
r = tkinter.Tk(); r.withdraw()
v = tkinter.StringVar()
try:
    v.trace_add("w", lambda *a, **k: None)
    print("  trace_add('w', ...) -> OK")
except Exception as e:
    print("  trace_add('w', ...) ->", type(e).__name__, e)
try:
    v.trace_add("write", lambda *a, **k: None)
    print("  trace_add('write', ...) -> OK")
except Exception as e:
    print("  trace_add('write', ...) ->", type(e).__name__, e)

print("\n=== [2] toplevel <Delete> binding also fires when the user types Delete in the search Entry ===")
hits = []
e = tkinter.Entry(r)
e.pack()
r.bind("<Delete>", lambda ev: hits.append(("root-handler", str(ev.widget))))
r.deiconify(); r.update()
e.focus_force(); r.update()
e.insert(0, "abcdef")
e.icursor(3)
e.event_generate("<Delete>")
r.update()
print("  entry text after Delete:", repr(e.get()), "| root handler fired:", hits)
e.event_generate("<Delete>", when="now")
r.update()
print("  (when='now') text:", repr(e.get()), "| root handler fired:", hits)
r.destroy()

print("\n=== [3] non-UTF-8 info.dat with Chinese text (GBK) ===")
base = r"D:\QA\Quest3bs\tests\fixtures2"
os.makedirs(base, exist_ok=True)
p = os.path.join(base, "GbkSong"); os.makedirs(p, exist_ok=True)
data = {"_version": "2.0.0", "_songName": "夜曲", "_songAuthorName": "周杰伦", "_beatsPerMinute": 90}
with open(os.path.join(p, "info.dat"), "wb") as f:
    f.write(json.dumps(data, ensure_ascii=False).encode("gbk"))
db = m.DatabaseManager(); sm = m.SongManager(db)
try:
    info = sm.get_song_info(p)
    print("  returned:", info["title"])
except Exception as ex:
    print("  get_song_info RAISED (not caught by `except (OSError, json.JSONDecodeError)`) ->", type(ex).__name__, ex)

print("\n=== [4] `if song in missing_songs` where missing_songs is a LIST (lines 2411-2414) ===")
for n in (500, 2000):
    src = [f"Song{i:05d}" for i in range(n)]
    miss = [f"Song{i:05d}" for i in range(n) if i % 2 == 0]
    miss_set = set(miss)
    t0 = time.perf_counter()
    idx = [i for i, s in enumerate(src) if s in miss]
    t1 = time.perf_counter()
    idx2 = [i for i, s in enumerate(src) if s in miss_set]
    t2 = time.perf_counter()
    print(f"  n={n:5d}: list-membership {t1-t0:.4f}s | set-membership {t2-t1:.6f}s  (x{(t1-t0)/max(t2-t1,1e-9):.0f})")

print("\n=== [5] os.walk/break shape used at lines 1830-1847 (break fires after the FIRST matched item) ===")
tree = os.path.join(base, "deviceroot")
os.makedirs(os.path.join(tree, "Android", "data", "com.beatgames.beatsaber", "files", "CustomLevels"), exist_ok=True)
os.makedirs(os.path.join(tree, "内部共享存储空间", "Android", "data", "com.beatgames.beatsaber", "files", "CustomLevels"), exist_ok=True)
found = []
for item in sorted(os.listdir(tree)):          # "Android" comes first (matches 'android')
    if any(name in item.lower() for name in ["quest", "oculus", "meta", "android", "内部共享"]):
        quest_root = os.path.join(tree, item)
        search_paths = [quest_root,
                        os.path.join(quest_root, "内部共享存储空间"),
                        os.path.join(quest_root, "Android", "data", "com.beatgames.beatsaber", "files", "CustomLevels")]
        for sp in search_paths:
            if os.path.exists(sp) and os.path.isdir(sp):
                if os.path.basename(sp) == "CustomLevels":
                    found.append(sp)
                else:
                    for root_, dirs, _ in os.walk(sp, topdown=True):
                        if "CustomLevels" in dirs:
                            found.append(os.path.join(root_, "CustomLevels")); break
        break                                   # <-- line 1847: unconditional
print("  dirs on device:", sorted(os.listdir(tree)))
print("  detected paths:", [os.path.relpath(f, tree) for f in found])
print("  -> the second top-level device folder is never inspected")
