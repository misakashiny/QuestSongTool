"""Real filter dialog regression; no network, database, audio or device access."""
from __future__ import annotations

import copy
import json
import sys
import time
import tkinter as tk
from unittest.mock import patch

import customtkinter as ctk

from _paths import ROOT, sandbox

sys.path.insert(0, str(ROOT))

from quest_tool.messages import tr, set_language
from quest_tool.search_catalog import TAGS, ENVIRONMENTS
from quest_tool.search_filters import FLAGS, RANGES, CHARACTERISTICS, filter_params
from quest_tool.theme import T
from quest_tool.ui_search_filters import SearchFilters
from window_capture import capture_client


def pump(root, seconds=.18):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        root.update()
        time.sleep(.01)


def descendants(widget):
    result = []
    for child in widget.winfo_children():
        result.append(str(child))
        result.extend(descendants(child))
    return result


def main():
    work = sandbox("search_filters")
    root = ctk.CTk()
    root.withdraw()
    errors = []
    root.report_callback_exception = lambda *args: errors.append(str(args))
    calls = []
    fixture = dict(automapper="ai", leaderboard="BeatLeader",
                   tags={key: index % 2 == 0 for index, key in enumerate(TAGS)},
                   environments=list(ENVIRONMENTS), characteristics=list(CHARACTERISTICS))
    fixture.update({key: index % 2 == 0 for index, key in enumerate(FLAGS)})
    fixture.update({prefix + name: "1" if prefix == "min" else "2"
                    for name in RANGES for prefix in ("min", "max")})
    fixture.update({"from": "2025-01-01", "to": "2026-01-01"})
    original = copy.deepcopy(fixture)
    measurements = []
    try:
        for language in ("zh", "en"):
            set_language(language)
            ctk.set_appearance_mode("light")
            dialog = SearchFilters(root, fixture, calls.append)
            pump(root)
            segment = next(child for child in tk.Misc.winfo_children(dialog.tabs) if isinstance(child, ctk.CTkSegmentedButton))
            assert segment.winfo_height() >= 32, "Tab strip ignored form height"
            assert (len(dialog.tri), len(dialog.modes), len(dialog.tags), len(dialog.env), len(dialog.bounds)) == (8, 8, 45, 47, 16)
            for key in ("general", "tags", "environments", "metrics", "general"):
                dialog.tabs.set(tr("filter." + key))
                pump(root, .12)
            assert dialog.modes["Standard"].get() and fixture == original
            for key in ("general", "metrics", "tags", "general"):
                dialog.tabs.set(tr("filter." + key))
            pump(root)
            assert dialog.panels["general"].winfo_ismapped(), "Superseded tab callback hid current draft"
            dialog.apply()
            assert calls[-1] == original and filter_params(calls[-1]) == filter_params(original)
            dialog = SearchFilters(root, fixture, calls.append)
            pump(root)
            count = len(calls)
            dialog.reset_button.invoke()
            assert len(calls) == count and fixture == original
            assert dialog._value(dialog.ai) == "all" and dialog._value(dialog.leaderboard) == "All"
            assert not any(v.get() for v in list(dialog.modes.values()) + list(dialog.env.values()))
            assert not any(entry.get() for entry in dialog.bounds.values())
            assert all(dialog._value(pair) is None for pair in list(dialog.tri.values()) + list(dialog.tags.values()))
            dialog.cancel_button.invoke()
            assert len(calls) == count
            dialog = SearchFilters(root, {}, calls.append)
            pump(root)
            assert dialog._value(dialog.ai) == "human"
            for key, value in (("minNps", "9"), ("maxNps", "2")):
                dialog.bounds[key].insert(0, value)
            dialog.apply_button.invoke()
            assert len(calls) == count and dialog.winfo_exists() and dialog.error.cget("text")
            dialog.reset()
            identities = descendants(dialog)
            for scale in (1., 1.25, 1.5, 2.):
                ctk.set_widget_scaling(scale)
                ctk.set_window_scaling(scale)
                for size in ("700x560", "760x680", "1040x760"):
                    dialog.geometry(size)
                    pump(root)
                    for key in ("general", "tags", "environments", "metrics"):
                        dialog.tabs.set(tr("filter." + key))
                        pump(root, .12)
                        assert dialog.apply_button.winfo_ismapped()
                        assert dialog.footer.winfo_y() + dialog.footer.winfo_height() <= dialog.winfo_height()
                        for line, label, shell in dialog.choice_rows:
                            if line.master is dialog.panels[key]:
                                assert line.winfo_viewable()
                                assert shell.winfo_x() + shell.winfo_width() <= line.winfo_width() + 2
                                assert label.winfo_reqwidth() <= label.winfo_width() + 2
                        for body, cells in dialog.check_groups:
                            if body.master.master is dialog.panels[key]:
                                assert body.winfo_viewable()
                                for cell, label in cells:
                                    assert cell.winfo_x() + cell.winfo_width() <= body.winfo_width() + 2
                                    assert label.winfo_reqwidth() <= label.winfo_width() + 2, (language, scale, size, key, label.cget("text"), label.winfo_reqwidth(), label.winfo_width())
                        if scale == 1. and size == "760x680":
                            capture_client(dialog, work / f"{language}-{key}.png")
                assert descendants(dialog) == identities, "Resize recreated controls"
            ctk.set_widget_scaling(1.)
            ctk.set_window_scaling(1.)
            dialog.geometry("760x680")
            dialog.tabs.set(tr("filter.general"))
            pump(root)
            menu = dialog.ai[0]
            tk.Misc.focus_force(menu)
            pump(root, .05)
            assert menu.master.cget("border_color") == T["ACCENT"]
            before = menu.get()
            menu.event_generate("<Down>")
            pump(root, .05)
            assert menu.get() != before
            next_focus = str(dialog.tk.call("tk_focusNext", str(menu)))
            assert next_focus != str(menu) and next_focus in identities
            dropdown = next(child for child in menu.winfo_children() if isinstance(child, tk.Menu))
            # Windows native menu posting runs its own message loop; its Tk
            # winfo_ismapped is not a reliable popup visibility signal. Verify
            # real key dispatch to the native boundary without a blocking popup.
            with patch.object(dropdown, "post") as posted:
                menu.event_generate("<space>")
                pump(root, .05)
                posted.assert_called_once_with(menu.winfo_rootx(), menu.winfo_rooty() + menu.winfo_height() + 2)
            check = next(child for child in dialog.check_groups[0][1][0][0].winfo_children() if isinstance(child, ctk.CTkCheckBox))
            tk.Misc.focus_force(check)
            check.event_generate("<space>")
            pump(root, .05)
            assert dialog.modes["Standard"].get()
            check.configure(state="disabled")
            check.event_generate("<space>")
            pump(root, .05)
            assert dialog.modes["Standard"].get()
            check.configure(state="normal")
            start = dialog.layout_passes
            for _ in range(100):
                dialog.event_generate("<Configure>")
            pump(root)
            assert dialog.layout_passes - start <= 1
            assert descendants(dialog) == identities
            for mode in ("dark", "light"):
                ctk.set_appearance_mode(mode)
                pump(root)
                capture_client(dialog, work / f"{language}-{mode}.png")
            # Long pages can reach their last item; footer stays outside scrolling.
            dialog.tabs.set(tr("filter.environments"))
            pump(root)
            panel = dialog.panels["environments"]
            panel._parent_canvas.yview_moveto(1.)
            pump(root)
            assert panel._parent_canvas.yview()[1] >= .99
            measurements.append({"language": language, "controls": len(identities),
                                 "resize_layout_passes": dialog.layout_passes})
            dialog.destroy()
            assert len(calls) == count
        ctk.set_widget_scaling(2.)
        ctk.set_window_scaling(2.)
        dialog = SearchFilters(root, {}, calls.append)
        pump(root)
        assert dialog.winfo_height() <= dialog.winfo_screenheight()
        assert dialog.winfo_width() <= dialog.winfo_screenwidth()
        dialog.destroy()
        assert not errors, errors
        (work / "results.json").write_text(json.dumps(measurements, indent=2), encoding="utf-8")
        print("[PASS] filters: all values round-trip; draft/cancel/reset/invalid; zh/en; 4 scales x 3 sizes x 4 tabs; keyboard/disabled; no recreated controls/callback errors")
        return 0
    finally:
        ctk.set_widget_scaling(1.)
        ctk.set_window_scaling(1.)
        root.destroy()


if __name__ == "__main__":
    raise SystemExit(main())
