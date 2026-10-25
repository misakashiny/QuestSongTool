"""Real Tk key dispatch, internal CTk children and inspector-only cleanup."""
from __future__ import annotations

import json
import sys
import time
import tkinter as tk
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import customtkinter as ctk
from quest_tool.tk_inspector import TkInspector
from quest_tool.messages import set_language, tr


def main():
    root = ctk.CTk()
    root.geometry("760x520")
    errors = []
    root.report_callback_exception = lambda *args: errors.append(str(args))
    calls = []
    entry = ctk.CTkEntry(root)
    entry.grid(row=0, column=0, padx=13, pady=11, sticky="ew")
    button = ctk.CTkButton(root, text="Example", corner_radius=9, command=lambda: calls.append("click"))
    button.grid(row=1, column=0, padx=17, pady=7)
    tabs = ctk.CTkTabview(root)
    tabs.grid(row=2, column=0)
    tabs.add("One"); tabs.add("Two")
    original_tab = []
    original_escape = []
    native_entry = next(w for w in entry.winfo_children() if w.winfo_class() == "Entry")
    native_entry.bind("<Tab>", lambda _event: (original_tab.append(True), "break")[-1])
    root.bind_all("<Escape>", lambda _event: original_escape.append(True), add="+")
    inspector = None
    child = None

    def pump(seconds=.06):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            root.update()
            time.sleep(.005)

    try:
        pump(.2)
        inspector = TkInspector(root)
        inspector.toggle()
        native_entry.focus_force(); pump()
        # Lock via the same mouse event path, so pointer position cannot dismiss it.
        native_entry.event_generate("<Button-1>", x=6, y=6)
        pump()
        assert inspector.selected and inspector.overlay._visible
        assert inspector._last_widget is entry, "Internal Entry was not promoted to CTkEntry"
        native_entry.event_generate("<Tab>"); pump()
        assert inspector.overlay.detail and not original_tab, "Widget Tab binding consumed inspector key"
        assert root.focus_get() is native_entry, "Inspector Tab moved focus"
        native_entry.event_generate("<F2>"); pump()
        assert not inspector.overlay.detail
        native_entry.event_generate("<Control-Alt-KeyPress-d>"); pump()
        assert inspector._details.winfo_viewable()
        details = json.loads(inspector._details_text.get("1.0", "end"))
        assert details["manager"] == "grid" and details["layout"]["padx"] == 13
        assert details["python_class"].endswith("CTkEntry") and details["parent"] == str(root)
        assert "fg_color" in details["options"] and "border_width" in details["options"]
        assert details["abs_root_x"] == entry.winfo_rootx() - root.winfo_rootx()
        clipboard = []
        root.clipboard_clear = lambda: None
        root.clipboard_append = clipboard.append
        inspector._copy_details()
        assert json.loads(clipboard[-1]) == details, "Copy lost full properties"
        for language in ("zh", "en"):
            set_language(language)
            assert tr("inspector.title") != "inspector.title"
        set_language("zh")
        inspector._details.withdraw()
        # A Toplevel created after activation must also receive early key dispatch.
        child = ctk.CTkToplevel(root)
        child.geometry("400x200")
        late = ctk.CTkEntry(child); late.pack(padx=20, pady=20)
        pump(.2)
        late_native = next(w for w in late.winfo_children() if w.winfo_class() == "Entry")
        assert late_native.bindtags()[0] == inspector._event_tag
        late_native.focus_force()
        late_native.event_generate("<Button-1>", x=6, y=6); pump()
        late_native.event_generate("<Tab>"); pump()
        assert inspector.overlay.detail
        assert inspector._info_for(late_native).python_class.endswith("CTkEntry")
        segmented = next(w for w in tk.Misc.winfo_children(tabs)
                         if isinstance(w, ctk.CTkSegmentedButton))
        assert segmented.bindtags()[0] == inspector._event_tag, "Hidden CTk internals were skipped"
        inspector.selected = None
        info = inspector._info_for(button)
        assert info.options["corner_radius"] == 9 and info.layout["padx"] == 17
        inspector._on_click(SimpleNamespace(widget=button))
        inspector._on_motion(SimpleNamespace(widget=root))
        assert inspector.overlay._visible, "Moving over root lost a locked selection"
        shape = next(w for w in button.winfo_children() if w.winfo_class() == "Canvas")
        shape.event_generate("<Button-1>", x=6, y=6); pump()
        assert not calls, "Inspecting a button invoked its business command"
        inspector.toggle()
        native_entry.focus_force()
        native_entry.event_generate("<Tab>"); pump()
        assert original_tab == [True], "Normal Tab was not restored when inactive"
        inspector.detach()
        assert inspector._event_tag not in native_entry.bindtags()
        native_entry.event_generate("<Tab>"); pump()
        assert len(original_tab) == 2, "Detach removed an existing widget binding"
        native_entry.event_generate("<Escape>"); pump()
        assert original_escape, "Detach removed another all-binding"
        shape.event_generate("<Button-1>", x=6, y=6); pump()
        assert calls == ["click"], "Detach broke the application's mouse binding"
        assert not errors, errors
        inspector = None
        print("[PASS] real Tab/F2/Ctrl+Alt+D, class-binding precedence, late Toplevel and CTk internals")
        print("[PASS] geometry/style/font/layout JSON, copy boundary, locked hover, inspection click isolation")
        print("[PASS] inactive keys and detach preserve existing widget/all bindings; no callback errors")
    finally:
        if inspector is not None:
            inspector.detach()
        if child is not None:
            child.destroy()
        root.destroy()


if __name__ == "__main__":
    main()
