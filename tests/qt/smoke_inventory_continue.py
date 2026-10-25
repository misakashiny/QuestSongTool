"""Real-file bounded continuation, force-once, cancellation and partial results."""
import json
from pathlib import Path
import sys
import tempfile
import threading
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT/'tests/qt')]
from smoke_verification import song
from quest_tool.downloads import DownloadError, validate_song
from quest_tool.qt_ui.local_inventory import InventoryClient
from quest_tool.song_search import SearchSession


def main():
    with tempfile.TemporaryDirectory(prefix='quest-continue-') as directory:
        target = Path(directory).resolve()
        for index in range(5):
            folder = target/f'song-{index}'
            song(folder)
            (folder/'Expert.dat').write_text(json.dumps({'padding': 'x'*4096}), encoding='utf-8')
        folder = target/'song-0'
        cost = 3*(folder/'Info.dat').stat().st_size+2*(folder/'Expert.dat').stat().st_size
        client = InventoryClient();client.MAX_BYTES = cost*2
        payload = dict(target=str(target), rows=[])
        cancel = threading.Event()
        def scan(force=False):
            with patch('quest_tool.qt_ui.local_verification.validate_song', wraps=validate_song) as calls:
                value = client.search(dict(payload, force=force), 0, '', cancel)['inventory']
            return value, calls.call_count
        value, count = scan(True)
        assert value['complete'] and value['valid'] == 5 and value['passes'] == 3 and count == 5, (value,count)
        value, count = scan()
        assert value['passes'] == 1 and value['reused'] == 5 and count == 0
        value, count = scan(True)
        assert value['complete'] and value['passes'] == 3 and count == 5, 'force cleared continuation cache'
        # More work than all three budgets: explicit partial result, no loop.
        for index in (5,6):
            folder = target/f'song-{index}';song(folder)
            (folder/'Expert.dat').write_bytes((target/'song-0'/'Expert.dat').read_bytes())
        value, count = scan(True)
        assert not value['complete'] and value['valid'] == 6 and value['passes'] == 3 and count == 6
        assert value['budget_limited'] and value['max_passes'] == 3
        value, count = scan()
        assert value['complete'] and value['valid'] == 7 and count == 1
        # A budget too small to verify even one song cannot make progress.
        client.MAX_BYTES = 1
        value, count = scan(True)
        assert value['passes'] == 1 and not value['complete'] and count == 0
        client.MAX_BYTES = cost*2
        # An undersized cache must not rehash the same prefix for all passes.
        client.MAX_CACHE_ITEMS = 1
        value, count = scan(True)
        assert not value['complete'] and value['passes'] == 2 and count == 4, (value,count)
        client.MAX_CACHE_ITEMS = 20000
        # Cancel exactly between passes: no subsequent hash or result escapes.
        original = client._scan
        def stop(*args):
            result = original(*args);cancel.set();return result
        with patch.object(client, '_scan', side_effect=stop) as scans:
            try:scan(True)
            except DownloadError as exc:assert exc.code == 'cancelled'
            else:raise AssertionError('cancel between passes ignored')
            assert scans.call_count == 1
        cancel.clear()
        # Structural/quantity failures alone never trigger continuation.
        client.MAX_BYTES = 256*1024*1024;client.MAX_FOLDERS = 1
        value, count = scan(True)
        assert value['passes'] == 1 and not value['complete'] and not value['budget_limited']
        client.MAX_FOLDERS = 2000
        # Rename the first song between passes: each individual pass could
        # inspect two folders, but this operation must never inspect a third.
        client.MAX_FOLDERS = 2;client.MAX_BYTES = cost
        renamed = target/'new-folder'
        renamed_once = False
        def rename_between(*args):
            nonlocal renamed_once
            result = original(*args)
            if not renamed_once:
                renamed_once = True;(target/'song-0').rename(renamed)
            return result
        with patch.object(client, '_scan', side_effect=rename_between):
            value, count = scan(True)
        assert len(client.operation_seen) == 2 and count == 2 and not value['complete'], (value,count)
        renamed.rename(target/'song-0')
        client.MAX_FOLDERS = 2000;client.MAX_BYTES = 256*1024*1024
        bad = target/'broken';bad.mkdir();(bad/'Info.dat').write_text('invalid')
        value, count = scan(True)
        assert value['passes'] == 1 and value['unknown'] == 1 and not value['complete']
        # One SearchSession consumer, replacement target and old cancellation.
        other = target/'empty';other.mkdir()
        client.MAX_BYTES = cost*2
        entered = threading.Event();gate = threading.Event();first = True
        def pause(*args):
            nonlocal first
            result = original(*args)
            if first:
                first = False;entered.set();assert gate.wait(5)
            return result
        session = SearchSession(client)
        try:
            with patch.object(client, '_scan', side_effect=pause):
                session.request(dict(payload,force=True),0,'')
                assert entered.wait(5)
                generation = session.request(dict(target=str(other),rows=[]),0,'')
                gate.set()
                for thread in tuple(session.tasks._threads):thread.join(5);assert not thread.is_alive()
                messages = session.tasks.drain()
                current = [m.payload for m in messages if m.kind == 'search']
                assert len(current) == 1 and current[0]['generation'] == generation, current
                assert current[0]['result']['inventory']['complete']
                assert current[0]['result']['inventory']['valid'] == 0 and not client.memo
        finally:
            gate.set();session.close()
            for thread in tuple(session.tasks._threads):thread.join(5)
    print('PASS real files: three-pass completion, warm reuse, force once, cap/partial, no progress, cancel, structural limits, target supersession')


if __name__ == '__main__':main()
