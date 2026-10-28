"""A/C UI: native checks, cached art, fixed actions and compact player geometry."""
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT/'tests/qt')]


def child():
    from PIL import Image
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication, QStyle, QStyleOptionViewItem
    from quest_tool.messages import tr
    from quest_tool.qt_ui.download_window import DownloadWindow
    from quest_tool.qt_ui.library_window import LibraryWindow
    from quest_tool.qt_ui.song_model import Song
    from smoke_prefetch import Session

    app = QApplication([]); app.setStyle('Fusion')
    errors = []
    def report(kind, value, tb):
        errors.append(str(value)); sys.__excepthook__(kind, value, tb)
    sys.excepthook = report
    def wait(check):
        end = time.monotonic()+10
        while time.monotonic()<end:
            time.sleep(.005)  # Let Python file workers run while pumping Qt.
            QTest.qWait(20)
            if check():return
        raise AssertionError('A/C UI wait')
    def ordered(widgets, parent):
        previous = 0
        for widget in widgets:
            point = widget.mapTo(parent, QPoint())
            assert point.x() >= previous, (widget.objectName(), point.x(), previous)
            assert point.x()+widget.width() <= parent.width()
            previous = point.x()+widget.width()
    png = io.BytesIO(); Image.new('RGB',(64,64),'blue').save(png,'PNG')
    image = png.getvalue()
    one = Song('abc','Blue Horizon 中文','Artist','Mapper',193,128,96,
               'a'*40,cover_url='https://cdn.beatsaver.com/one.png',preview_url='https://cdn.beatsaver.com/one.mp3')
    with tempfile.TemporaryDirectory(prefix='quest-ui-ac-') as temporary:
        root = Path(temporary); library = root/'library'; library.mkdir()
        for number in range(70):
            folder = library/f'song-{number:03}'; folder.mkdir()
            (folder/'Info.dat').write_text(json.dumps(dict(_version='2.0.0',_songName=f'Blue Horizon {number:03}',
                _songAuthorName='Studio Artist',_songDuration=193,_beatsPerMinute=128,_coverImageFilename='cover.png')),encoding='utf-8')
            (folder/'cover.png').write_bytes(image)
        for language, mode in (('zh','light'),('en','dark')):
            window = DownloadWindow(language, mode, songs=(one,))
            window.enable_downloads(root/('downloads-'+language))
            window.enable_covers(Session()); window.enable_preview()
            window.cover_browser.cache.put(one.cover_url,image)
            window.show_song(one); window.show(); QTest.qWait(60)
            try:
                assert not window.preview_cover.pixmap().isNull()
                assert window.preview_name.toolTip() == one.title+' · '+one.artist
                assert window.favorite.isEnabled() and not window.favorite.isChecked()
                QTest.mouseClick(window.favorite,Qt.MouseButton.LeftButton)
                assert window.favorite.isChecked() and window.favorites_browser.toggle_action.isChecked()
                assert window.favorites_browser.store.load()=={one.key:one}
                assert not window.song_model.checked and not window.download_browser.manager.snapshot()
                window.favorites_browser.toggle_action.trigger()
                assert not window.favorite.isChecked() and not window.favorites_browser.store.load()
                with patch.object(window.favorites_browser.store,'update',side_effect=OSError('controlled failure')):
                    window.favorite.click()
                assert not window.favorite.isChecked()
                assert window.favorite.toolTip()==tr('qt.favorites.error')
                window.favorite.click(); assert window.favorite.isChecked()
                for width in (1000,1280,1600):
                    window.resize(width,824);QTest.qWait(50)
                    ordered((window.preview_cover,window.preview_name,window.preview_time,window.seek,window.preview_total,window.preview_previous,window.play,window.preview_next,window.volume),window.player)
                    ordered((window.favorite,window.download_one),window.detail_panel)
                window.preview_browser.song=one
                window.show_song(None)
                assert not window.preview_cover.pixmap().isNull(), 'Playing identity must survive selection changes'
                window.preview_browser.song=None;window.preview_browser.render()
                assert window.preview_cover.pixmap() is None or window.preview_cover.pixmap().isNull()
            finally:
                window.close();window.download_browser.wait_closed()
            window = LibraryWindow(root/('library-'+language),language,mode)
            window.show();window.set_target(library)
            try:
                wait(lambda:window.local_ready and bool(window.covers.cache.sources))
                assert 0<len(window.covers.cache.sources)<70, 'List thumbnails must remain lazy'
                index=window.proxy.index(0,0);window.table.scrollTo(index);window.covers.schedule()
                key=index.data(Qt.ItemDataRole.UserRole)['folder']
                wait(lambda:key in window.covers.cache.sources)
                option=QStyleOptionViewItem();option.initFrom(window.table)
                delegate=window.table.itemDelegateForColumn(0)
                delegate.initStyleOption(option,index);option.rect=window.table.visualRect(index)
                assert '\nStudio Artist' in option.text and not option.icon.isNull()
                assert window.table.isColumnHidden(1)
                hit=window.table.style().subElementRect(QStyle.SubElement.SE_ItemViewItemCheckIndicator,option,window.table)
                QTest.mouseClick(window.table.viewport(),Qt.MouseButton.LeftButton,pos=hit.center())
                assert key in window.model.checked
                window.details.select('local',index);wait(lambda:not window.details.loading)
                assert not window.preview.cover.pixmap().isNull()
                assert not window.preview.play.text() and window.preview.play.accessibleName()==tr('qt.local_audio.play')
                for width,height in ((1000,640),(1280,824),(1600,900)):
                    window.resize(width,height);QTest.qWait(50)
                    ordered((window.preview.cover,window.preview.title,window.preview.time,window.preview.seek,window.preview.total_label,
                             window.preview.previous,window.preview.play,window.preview.next_button,window.preview.volume),window.preview.page)
                source='Quest 3 / long source '+('CustomLevels/'*30)
                window.set_quest_snapshot(window.model.rows[:2],source,skipped=1)
                assert source not in window.quest_summary.text()
                assert source in window.quest_summary.toolTip() and source in window.quest_summary.accessibleName()
                assert window.quest_incomplete and not window.missing_model.rowCount()
                assert window.preview.player is None, 'Layout must not initialize audio'
                shots=os.environ.get('QUEST_QT_SHOTS')
                if shots:
                    folder=Path(shots);folder.mkdir(parents=True,exist_ok=True)
                    window.grab().save(str(folder/f'ui-ac-library-{language}-{os.environ.get("QT_SCALE_FACTOR")}.png'))
            finally:
                window.close();window.wait_closed();QTest.qWait(20)
    assert not errors, errors
    assert 'miniaudio' not in sys.modules
    print('PASS A/C: shared favorite state/error, native checks, lazy list covers, active identity, non-overlapping players, complete snapshot tooltip, no audio/device initialization')


if __name__=='__main__':
    if '--child' in sys.argv:child()
    else:
        for scale in ('1','1.25','1.5'):
            result=subprocess.run([sys.executable,'-B',__file__,'--child'],cwd=ROOT,
                env=dict(os.environ,QT_SCALE_FACTOR=scale,PYTHONIOENCODING='utf-8'),capture_output=True,timeout=150)
            print(scale,result.stdout.decode('utf-8','replace'),result.stderr.decode('utf-8','replace'),flush=True)
            assert result.returncode==0
