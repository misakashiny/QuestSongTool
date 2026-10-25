# -*- coding: utf-8 -*-
"""Dynamic verification A: DB + info.dat parsing + cover + rating persistence.
Imports the REAL module (no modification) and exercises DatabaseManager / SongManager.
"""
import importlib.util, json, os, shutil, sys, io

SRC = r"D:\QA\Quest3bs\源代码_原始版.py"
BASE = str(Path(__file__).resolve().parent / "fixtures")

spec = importlib.util.spec_from_file_location("target_app", SRC)
m = importlib.util.module_from_spec(spec)
sys.modules["target_app"] = m
spec.loader.exec_module(m)          # module level logger = setup_logging() -> ./logs under cwd
print("module imported OK; logger =", m.logger.name)

shutil.rmtree(BASE, ignore_errors=True)
os.makedirs(BASE, exist_ok=True)

db = m.DatabaseManager()
print("db_path:", os.path.abspath(db.db_path))

# ---------------------------------------------------------------- 1. rating on a missing row
print("\n[1] update_song_rating() on a folder that has NO row in DB")
ok = db.update_song_rating("NonexistentSong", 5)
print("    returns:", ok, "| rows in table:", db.get_all_song_ratings())

# ---------------------------------------------------------------- 2. info.dat variants
def mkfolder(name, raw_bytes):
    p = os.path.join(BASE, name)
    os.makedirs(p, exist_ok=True)
    with open(os.path.join(p, "info.dat"), "wb") as f:
        f.write(raw_bytes)
    return p

v2 = {"_version": "2.0.0", "_songName": "V2 Song", "_songSubName": "sub",
      "_songAuthorName": "Author2", "_beatsPerMinute": 174.0,
      "_coverImageFilename": "cover.png", "_songFilename": "song.ogg",
      "_difficultyBeatmapSets": [{"_beatmapCharacteristicName": "Standard",
                                  "_difficultyBeatmaps": [{"_difficulty": "Expert", "_difficultyRank": 5}]}]}
v4 = {"version": "4.0.0", "song": {"title": "V4 Song", "subTitle": "s4", "author": "Author4"},
      "audio": {"bpm": 128, "songFilename": "song.ogg"},
      "coverImageFilename": "cover.png",
      "difficultyBeatmaps": [{"difficulty": "Hard", "beatmapFilename": "Hard.dat"}]}

p_v2 = mkfolder("SongV2", json.dumps(v2).encode("utf-8"))
p_v4 = mkfolder("SongV4", json.dumps(v4).encode("utf-8"))
p_bom = mkfolder("SongBOM", b"\xef\xbb\xbf" + json.dumps(v2).encode("utf-8"))
p_gbk = mkfolder("SongGBK", json.dumps(v2, ensure_ascii=False).encode("gbk"))

sm = m.SongManager(db)
for label, p in (("v2", p_v2), ("v4", p_v4), ("UTF-8 BOM", p_bom), ("GBK", p_gbk)):
    try:
        info = sm.get_song_info(p)
        print(f"\n[2] {label:10s} -> title={info['title']!r} author={info['author']!r} "
              f"bpm={info['bpm']!r} ver={info['version']!r} diff={info['difficulties']!r} cover={info['cover_path']!r}")
    except Exception as e:
        print(f"\n[2] {label:10s} -> RAISED {type(e).__name__}: {e}")

# ---------------------------------------------------------------- 3. cover filename ignored
print("\n[3] _find_cover_image when info.dat says cover.png but only a bg image exists")
p_bg = mkfolder("SongBg", json.dumps(v2).encode("utf-8"))
open(os.path.join(p_bg, "background.png"), "wb").write(b"\x89PNG\r\n\x1a\n" + b"0" * 10)
print("    ->", os.path.basename(str(sm._find_cover_image(p_bg))))

# ---------------------------------------------------------------- 4. re-parse despite cache
print("\n[4] second get_song_info() on an already cached folder (same mtime):")
import time
t0 = time.perf_counter(); sm.get_song_info(p_v2); t1 = time.perf_counter()
print("    cache row exists:", db.get_song_info('SongV2') is not None)
# prove the file is re-read by removing it and calling again
os.rename(os.path.join(p_v2, "info.dat"), os.path.join(p_v2, "info.dat.bak"))
info2 = sm.get_song_info(p_v2)
print("    with info.dat removed, result title =", repr(info2['title']),
      "(cache row still in DB -> proves file is always re-parsed, cache never used)")

# ---------------------------------------------------------------- 5. row version / rating survival
print("\n[5] rating persistence round-trip for a folder WITHOUT info.dat")
p_noinfo = os.path.join(BASE, "SongNoInfo"); os.makedirs(p_noinfo, exist_ok=True)
sm.get_song_info(p_noinfo)
print("    row created?", db.get_song_info("SongNoInfo") is not None,
      "| update_song_rating ->", db.update_song_rating("SongNoInfo", 4),
      "| stored:", db.get_song_info("SongNoInfo"))

# ---------------------------------------------------------------- 6. concurrent write behaviour
print("\n[6] two connections writing at once (no WAL / no busy_timeout):")
import threading
res = []
def w(i):
    try:
        c = m.DatabaseManager()
        c.save_song_info(f"S{i}", {"title": "t"}, "/p", "/c", 1.0)
        res.append(("ok", i))
    except Exception as e:
        res.append((type(e).__name__, str(e)))
ts = [threading.Thread(target=w, args=(i,)) for i in range(12)]
[t.start() for t in ts]; [t.join() for t in ts]
print("    failures:", [r for r in res if r[0] != "ok"] or "none")
print("    journal_mode:", __import__("sqlite3").connect(db.db_path).execute("PRAGMA journal_mode").fetchone()[0])
