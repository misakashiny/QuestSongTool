# -*- coding: utf-8 -*-
"""Dynamic verification B: copy/delete data-loss paths, using the REAL methods via a duck-typed stub."""
import importlib.util, os, shutil, sys, queue, traceback

SRC = r"D:\QA\Quest3bs\源代码_原始版.py"
BASE = str(Path(__file__).resolve().parent / "fixtures" / "copy")
shutil.rmtree(BASE, ignore_errors=True)
os.makedirs(BASE, exist_ok=True)

spec = importlib.util.spec_from_file_location("target_app", SRC)
m = importlib.util.module_from_spec(spec)
sys.modules["target_app"] = m
spec.loader.exec_module(m)


class FakeRoot:
    """Stand-in for the Tk root: records what the worker thread would schedule."""
    def __init__(self): self.scheduled = []
    def after(self, delay, fn=None, *a): self.scheduled.append((delay, fn))


def make_app():
    app = m.SongComparatorApp.__new__(m.SongComparatorApp)   # bypass __init__ (no Tk)
    app.progress_queue = queue.Queue()
    app.root = FakeRoot()
    app.local_path_var = type("V", (), {"get": staticmethod(lambda: "")})()
    return app


def make_song(root, name, size=4):
    p = os.path.join(root, name)
    os.makedirs(p, exist_ok=True)
    with open(os.path.join(p, "info.dat"), "w", encoding="utf-8") as f:
        f.write('{"_songName":"%s"}' % name)
    with open(os.path.join(p, "song.ogg"), "wb") as f:
        f.write(b"x" * size)
    return p


print("=== CASE A: user points BOTH path boxes at the SAME folder (src == dst) ===")
same = os.path.join(BASE, "BothPaths"); os.makedirs(same, exist_ok=True)
make_song(same, "Song1")
app = make_app()
print("  before:", os.listdir(same))
try:
    app.copy_missing_background(same, same, ["Song1"], "Quest")   # REAL method
except Exception:
    traceback.print_exc()
print("  after :", os.listdir(same), " <-- song folder still there?", os.path.isdir(os.path.join(same, "Song1")))
while not app.progress_queue.empty():
    print("  queue:", app.progress_queue.get())

print("\n=== CASE B: destination folder already exists (different content) ===")
src = os.path.join(BASE, "Src"); dst = os.path.join(BASE, "Dst")
make_song(src, "Song2", 4)
p = make_song(dst, "Song2", 9999)                      # an unrelated 9 KB map with the same folder name
print("  dst song.ogg size before:", os.path.getsize(os.path.join(p, "song.ogg")))
app = make_app()
app.copy_missing_background(src, dst, ["Song2"], "Quest")
print("  dst song.ogg size after :", os.path.getsize(os.path.join(p, "song.ogg")),
      "  <-- silently overwritten, no backup, no confirmation")
while not app.progress_queue.empty():
    print("  queue:", app.progress_queue.get())

print("\n=== CASE C: execute_delete() with an EMPTY path variable ===")
cwd_sandbox = os.path.join(BASE, "cwd"); os.makedirs(cwd_sandbox, exist_ok=True)
make_song(cwd_sandbox, "RelativeSong")
print("  os.path.join('', 'RelativeSong') ==", repr(os.path.join("", "RelativeSong")))
prev = os.getcwd()
os.chdir(cwd_sandbox)                                  # emulate cwd being the songs folder
app = make_app()
app.info_labels = {}; app.rating_stars = []
app.update_difficulty_display = lambda *a: None
app.update_rating_display = lambda *a: None
app.update_status = lambda *a, **k: None
app.show_info = lambda *a: None
app.comment_label = type("L", (), {"configure": lambda *a, **k: None})()
app.cover_label = type("L", (), {"configure": lambda *a, **k: None})()
app.local_songs = ["RelativeSong"]; app.quest_songs = []; app.missing_songs = []
app.local_list = type("L", (), {"set_songs": lambda *a, **k: None})()
app.quest_list = type("L", (), {"set_songs": lambda *a, **k: None})()
app.missing_list = type("L", (), {"set_songs": lambda *a, **k: None})()
app.song_ratings = {}; app.song_names = {}
app.current_selected_list = None; app.current_selected_song = None
app.execute_delete("RelativeSong", os.path.join("", "RelativeSong"), "local")   # REAL method
print("  folder still exists?", os.path.isdir(os.path.join(cwd_sandbox, "RelativeSong")))
os.chdir(prev)

print("\n=== CASE D: does execute_delete verify the path is under the configured root? ===")
import inspect
src_lines = inspect.getsource(m.SongComparatorApp.execute_delete).splitlines()
print("  execute_delete body (first 6 lines):")
for l in src_lines[:6]:
    print("   ", l)
