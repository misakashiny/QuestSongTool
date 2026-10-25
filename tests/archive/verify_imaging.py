# -*- coding: utf-8 -*-
"""Dynamic verification E: Pillow file-handle retention + CTkImage reference retention."""
import gc, os, shutil, inspect, sys
base = r"D:\QA\Quest3bs\tests\imgs"
shutil.rmtree(base, ignore_errors=True); os.makedirs(base, exist_ok=True)

from PIL import Image
p = os.path.join(base, "cover.jpg")
Image.new("RGB", (600, 600), (200, 30, 30)).save(p, quality=90)
p_png_a = os.path.join(base, "cover_alpha.png")
Image.new("RGBA", (600, 600), (30, 200, 30, 120)).save(p_png_a)

print("=== [1] does Image.open()+thumbnail() keep the file handle open on Windows? ===")
img = Image.open(p)
img.thumbnail((300, 250), Image.Resampling.LANCZOS)
print("  img.fp after thumbnail():", getattr(img, "fp", "n/a"))
try:
    os.remove(p)
    print("  os.remove(cover.jpg) -> OK  (handle was released by load())")
    Image.new("RGB", (600, 600), (200, 30, 30)).save(p, quality=90)
except PermissionError as e:
    print("  os.remove(cover.jpg) -> PermissionError:", e)

print("\n=== [2] and WITHOUT touching the pixels (no load) ? ===")
img2 = Image.open(p)
print("  img2.fp after open() only:", getattr(img2, "fp", "n/a"))
try:
    os.remove(p); print("  os.remove -> OK")
except PermissionError as e:
    print("  os.remove -> PermissionError:", e)

print("\n=== [3] RGBA (with alpha) source pasted into an RGB canvas ===")
try:
    src = Image.open(p_png_a); src.thumbnail((300, 250), Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", (300, 250), (50, 50, 50))
    canvas.paste(src, (0, 0))
    print("  paste(RGBA -> RGB): OK (alpha silently dropped -> transparent PNG renders as black/odd)")
except Exception as e:
    print("  paste(RGBA -> RGB) RAISED:", type(e).__name__, e)

print("\n=== [4] does CTkLabel keep its own reference to a CTkImage? ===")
import customtkinter as ctk
r = ctk.CTk(); r.withdraw()
lbl = ctk.CTkLabel(r, text="x")
ci = ctk.CTkImage(light_image=Image.new("RGB", (10, 10)), dark_image=Image.new("RGB", (10, 10)), size=(10, 10))
lbl.configure(image=ci, text="")
print("  label._image is ci:", getattr(lbl, "_image", None) is ci)
del ci; gc.collect()
print("  after del + gc, label._image still set:", getattr(lbl, "_image", None) is not None,
      "| size:", getattr(getattr(lbl, "_image", None), "cget", lambda *a: "?")() if hasattr(lbl, "_image") else "?")
r.destroy()
