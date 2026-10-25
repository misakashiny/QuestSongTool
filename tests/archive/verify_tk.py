# -*- coding: utf-8 -*-
"""Dynamic verification C: Tk-level behaviour (perf scaling, highlight index bug, falsy-cache bug)."""
import importlib.util, os, shutil, sys, time, gc

SRC = r"D:\QA\Quest3bs\源代码_原始版.py"
spec = importlib.util.spec_from_file_location("target_app", SRC)
m = importlib.util.module_from_spec(spec)
sys.modules["target_app"] = m
spec.loader.exec_module(m)

import customtkinter as ctk


class FakeApp(ctk.CTk):
    """Minimal stand-in for SongComparatorApp: provides clipboard_* (what SelectableListBox calls)."""
    pass


root = FakeApp()
root.withdraw()

print("=== [1] widget count / build time for _display_songs() ===")
def count_widgets(w):
    n = 1
    for c in w.winfo_children():
        n += count_widgets(c)
    return n

for n in (200, 1000, 2000):
    lb = m.SelectableListBox(root, title="t", width=300, height=450)
    songs = [f"Song{i:05d} (Artist) [Mapper]" for i in range(n)]
    t0 = time.perf_counter()
    lb.set_songs(songs)
    root.update_idletasks()
    t1 = time.perf_counter()
    print(f"  n={n:5d}: {count_widgets(lb.content_frame):6d} Tk widgets, {t1-t0:6.2f} s to build")
    lb.destroy()
    gc.collect()

print("\n=== [2] search keystroke cost (trace -> _filter_songs -> full rebuild) ===")
lb = m.SelectableListBox(root, title="t", width=300, height=450)
lb.set_songs([f"Song{i:05d}" for i in range(1000)])
t0 = time.perf_counter()
for ch in "Song0":                    # 5 keystrokes == what a user types
    lb.search_var.set(lb.search_var.get() + ch)
t1 = time.perf_counter()
print(f"  5 keystrokes over 1000 songs: {t1-t0:.2f} s ({((t1-t0)/5)*1000:.0f} ms per keystroke, synchronous on the UI thread)")
lb.destroy(); gc.collect()

print("\n=== [3] highlight index mapping after sorting (compare-then-highlight) ===")
lb = m.SelectableListBox(root, title="t", width=300, height=450)
source_list = ["Zebra", "Apple", "Mango"]          # == app.local_songs order
lb.set_songs(source_list, {}, {})
missing = ["Apple"]                                # app._compare_songs: local_set - quest_set
missing_indices = [i for i, s in enumerate(source_list) if s in missing]   # lines 2411-2414
print("  source_list        :", source_list)
print("  computed indices   :", missing_indices, "-> expects to highlight 'Apple'")
lb.set_sort_mode("name", reverse=False)             # user clicks the A-Z sort button
print("  displayed order    :", lb.filtered_songs)
lb.highlight_items(missing_indices)
highlighted = [w.song for w in lb.item_widgets if getattr(w, "is_highlighted", False)]
print("  ACTUALLY highlighted:", highlighted, " <-- wrong song" if highlighted != ["Apple"] else "")
lb.clear_highlights(); lb.destroy(); gc.collect()

print("\n=== [4] set_songs(..., {}, {}) -> falsy cache is ignored (stale ratings kept) ===")
lb = m.SelectableListBox(root, title="t", width=300, height=450)
lb.set_songs(["A", "B"], {"A": 5, "B": 1}, {"A": "titleA"})
print("  ratings before        :", lb.song_ratings)
lb.set_songs(["A", "B"], {}, {})                    # what refresh_cache/empty DB produces
print("  ratings after set({},{}):", lb.song_ratings, " <-- expected {}")
lb.destroy(); gc.collect()

print("\n=== [5] image reference / file handle check for update_cover_image ===")
import inspect
print("\n".join("  " + l for l in inspect.getsource(m.SongComparatorApp.update_cover_image).splitlines()[:14]))

root.destroy()
