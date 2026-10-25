"""Real window: approved cover/list layout, shared player and three-page flow."""
from __future__ import annotations

import os
import sys
import tempfile
import time
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import customtkinter as ctk
from quest_tool import audio
from quest_tool.downloads import DownloadManager, TERMINAL
from quest_tool.messages import tr, set_language
from quest_tool.playlist import song_hash
from quest_tool.song_search import SearchSession, search_map
from quest_tool.ui_downloads import DownloadWindow
from smoke_discover import FakeMedia
from smoke_downloads import FakeClient, make_archive
from smoke_search import sample
from window_capture import capture_client


def walk(widget):
    yield widget
    for child in widget.winfo_children():
        yield from walk(child)


def inside(widget, window):
    return (widget.winfo_rootx() >= window.winfo_rootx() and
            widget.winfo_rootx()+widget.winfo_width() <= window.winfo_rootx()+window.winfo_width()+2 and
            widget.winfo_rooty() >= window.winfo_rooty() and
            widget.winfo_rooty()+widget.winfo_height() <= window.winfo_rooty()+window.winfo_height()+2)


def main():
    work = Path(__file__).resolve().parent / '.tmp/download_layout'
    work.mkdir(parents=True, exist_ok=True)
    root = ctk.CTk(); root.withdraw()
    errors = []
    root.report_callback_exception = lambda *args: errors.append(str(args))
    def pump(seconds=.25):
        end = time.monotonic()+seconds
        while time.monotonic() < end:
            root.update(); time.sleep(.01)
    def wait(predicate):
        end = time.monotonic()+8
        while time.monotonic() < end:
            pump(.05)
            if predicate(): return
        raise AssertionError('Layout UI timed out')
    class Client:
        def __init__(self): self.calls = []
        def search(self, query, page, order, cancel, **kwargs):
            self.calls.append((query, page, order, kwargs))
            rows = []
            for i in range(kwargs.get("page_size",21) if page == 0 else 1):
                value = page*90+i+1
                raw = sample(hex(value)[2:], f'Layout Song {value}')
                raw['versions'][0].update(coverURL=f'https://r2cdn.beatsaver.com/cover{value}.png',
                                           previewURL=f'https://r2cdn.beatsaver.com/audio{value}.ogg')
                rows.append(search_map(raw))
            return {'rows': rows, 'query':query, 'page':page, 'order':order, 'has_next':page==0}
    with tempfile.TemporaryDirectory(dir=work) as tmp:
        temp = Path(tmp); target = temp/'songs'; target.mkdir()
        archive = make_archive(temp); fixture=temp/'fixture'; fixture.mkdir()
        with zipfile.ZipFile(archive) as z: z.extractall(fixture)
        manager = DownloadManager(temp/'history.json', FakeClient(archive, song_hash(fixture)))
        window = None
        try:
            for language in ('zh', 'en'):
                set_language(language)
                window = DownloadWindow(root, manager, str(target))
                pane = window.search_pane
                client = Client(); pane.session.close(); pane.session = SearchSession(client)
                pane.media.covers.client.media = FakeMedia()
                pane.media.preview.client.media = FakeMedia()
                pump()
                assert pane.view_mode == 'grid'
                assert not window.input.winfo_ismapped() and not window.tree.winfo_ismapped()
                assert window.target_entry.winfo_ismapped() and pane.play_button.winfo_ismapped()
                pane.preset_buttons['Curated'].invoke()
                wait(lambda: not pane.busy and len(pane._covers)==21)
                pump(.4)
                assert pane.grid_columns==3 and pane.grid_rows==7 and pane._columns==3 and len(pane.song_views)==21
                first = pane.rows[0]
                box=pane.list.hitboxes[(first['id'],'check')]
                play=pane.list.hitboxes[(first['id'],'play')]
                assert box[2]-box[0]==play[2]-play[0] and box[3]<play[1]
                pane.list.event_generate('<Button-1>',x=int((box[0]+box[2])/2),y=int((box[1]+box[3])/2));pump()
                selected = set(pane.selected)
                calls = len(client.calls)
                pane.view_menu._buttons_dict[tr('layout.list')].invoke(); pump()
                assert pane.view_mode=='list' and set(pane.selected)==selected and pane.checks[first['id']].get()
                pane.view_menu._buttons_dict[tr('layout.covers')].invoke(); pump()
                assert pane.view_mode=='grid' and set(pane.selected)==selected and len(client.calls)==calls
                assert len(pane._covers)==21, 'View switching refetched or lost covers'
                pane.next.invoke(); wait(lambda:not pane.busy)
                second=pane.rows[0]; pane.checks[second['id']].set(True); pane.toggle(second)
                pane.previous.invoke(); wait(lambda:not pane.busy)
                assert pane.checks[first['id']].get() and len(pane.selected)==2
                print(f'[PASS] {language}: cards/list share song IDs, checked state, covers and cross-page choices; no extra search')
                for label in (tr('search.tasks'), tr('layout.import'), tr('search.tab')):
                    window.tabs.set(label); pump(.2)
                    assert pane.play_button.winfo_ismapped() and inside(pane.play_button, window), (language, label, window.winfo_geometry(), window.winfo_reqheight(), pane.play_button.winfo_rooty()-window.winfo_rooty(), pane.play_button.winfo_height())
                    assert window.target_entry.winfo_ismapped() and inside(window.target_entry, window)
                    assert window.input.winfo_ismapped() == (label == tr('layout.import'))
                    assert window.tree.winfo_ismapped() == (label == tr('search.tasks'))
                assert pane.winfo_ismapped()
                # Long metadata may wrap, but must never grow the window sideways.
                pane.rows[0]['title']='Long song name ' * 14
                pane._render(); pump()
                for view in (tr('layout.covers'), tr('layout.list')):
                    pane.set_view(view)
                    for scale in (1.,1.25,1.5):
                        ctk.set_widget_scaling(scale);window.geometry('1000x760');pump(.3)
                        assert window.winfo_reqwidth() <= window.winfo_width()+2, (language, view, scale, 'overflow')
                        if language == 'en' and scale == 1.25:
                            # Simulate a stale Configure width during a DPI change.
                            actual_width = pane.winfo_width
                            pane.winfo_width = lambda: 1600
                            try:
                                pane._adapt_toolbar()
                            finally:
                                pane.winfo_width = actual_width
                            window.update_idletasks()
                            assert window.winfo_reqwidth() <= window.winfo_width()+2, (language, view, scale, 'stale width overflow')
                        for control in (pane.download_button, pane.download_one, pane.next, pane.play_button, pane.volume, pane.entry):
                            assert inside(control, window), (language, view, scale, 'clipped', control)
                ctk.set_widget_scaling(1.);window.geometry('1280x900');pane.set_view(tr('layout.covers'));pump(.4)
                assert pane._columns == 3
                pane.rows[0]['title']='Layout Song 1';pane._render();pump()
                assert not hasattr(pane, 'mode_menu'), 'Removed category dropdown still exists'
                assert not pane.sort.winfo_manager(), 'Fixed recommendation order still shows a redundant dropdown'
                assert all(button.winfo_ismapped() and inside(button,window) for button in pane.preset_buttons.values())
                # User-edited reference: compact toolbar, pagination status and shared bars.
                window.geometry('1280x824');pump(.4)
                if language == 'zh':
                    assert len({button.winfo_y() for button in pane.preset_buttons.values()}) == 1, 'Nine categories must share one row at reference width'
                assert pane.filter_button.winfo_rootx() > pane.category_bar.winfo_rootx()+pane.category_bar.winfo_width()
                assert pane.status.master is pane.pagination_bar
                assert pane.status.winfo_rootx()+pane.status.winfo_width() <= pane.previous.winfo_rootx(), 'Result status overlaps page navigation'
                assert pane.results_body.winfo_height() > 400, 'Compact chrome consumed the gallery viewport'
                assert pane.detail_panel.winfo_rooty() == pane.list.winfo_rooty()
                assert pane.detail_panel.winfo_rootx() > pane.list.winfo_rootx()+pane.list.winfo_width()
                assert pane.download_one.winfo_rooty()+pane.download_one.winfo_height() < pane.next.winfo_rooty()
                assert window.target_entry.winfo_rooty() > pane.next.winfo_rooty()+pane.next.winfo_height()
                assert pane.play_button.winfo_rooty() > window.target_entry.winfo_rooty()+window.target_entry.winfo_height()
                assert not pane.player_cover.winfo_ismapped(), 'Reference player has no separate cover thumbnail'
                print(f'[PASS] {language}: reference layout, separate result/detail columns, pagination, download bar and compact player')
                original_canvas = pane.list
                children = tuple(pane.list.winfo_children())
                timings = []
                for _ in range(8):
                    started=time.perf_counter();pane.list._paint();timings.append((time.perf_counter()-started)*1000)
                assert pane.list is original_canvas and tuple(pane.list.winfo_children())==children
                assert max(timings)<200, ('Canvas paint exceeded 200ms',timings)
                assert len(pane.list.find_all())<250, 'Offscreen cards were drawn'
                assert len(pane.list._photos)<=12, 'Offscreen covers were allocated'
                pane.columns_menu.set('4');pane.rows_menu.set('6');pane._grid_changed()
                wait(lambda:not pane.busy and len(pane.rows)==24);pump()
                assert client.calls[-1][3]['page_size']==24 and len(pane.selected)==2
                pane.columns_menu.set('3');pane.rows_menu.set('7');pane._grid_changed()
                wait(lambda:not pane.busy and len(pane.rows)==21);pump()
                print(f'[PASS] {language}: all categories visible, no index widgets, checkbox/play equal size; 4x6=24 -> 3x7=21; cached canvas paint max {max(timings):.1f}ms')
                if language=='zh':
                    # A gallery selection still reaches the original installation path.
                    pane.download_button.invoke()
                    wait(lambda:len(manager.snapshot())==2 and all(job['state'] in TERMINAL for job in manager.snapshot()))
                    assert {job['state'] for job in manager.snapshot()}=={'done','exists'}
                    window.tabs.set(tr('search.tab'));pump(.3)
                    if os.environ.get('QUEST_CAPTURE_DOWNLOAD_LAYOUT')=='1':
                        for mode in ('Light','Dark'):
                            ctk.set_appearance_mode(mode);pump(.3)
                            capture_client(window,Path(__file__).resolve().parent.parent/f'docs/images/v20.3-download-{mode.lower()}.png')
                print(f'[PASS] {language}: three pages, shared footer/player, long labels and 100/125/150% card/list geometry')
                window.close(); window=None; pump(.1)
            assert not errors, errors
        finally:
            if window is not None: window.close()
            manager.shutdown()
            for thread in manager.tasks._threads: thread.join(3)
            audio.stop_all(); root.destroy()
    print('[PASS] DOWNLOAD LAYOUT REGRESSION')


if __name__=='__main__': main()
