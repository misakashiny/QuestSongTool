"""Merged download chrome, rounded frames, bounded read-only logs and record filters."""
import hashlib
import logging
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))


def child():
    from PySide6.QtCore import QPoint,Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication,QDialog
    from quest_tool.qt_ui.library_window import LibraryWindow
    from quest_tool.qt_ui.download_window import DownloadWindow
    from quest_tool.qt_ui.diagnostics import read_logs,redact
    from quest_tool.qt_ui.runtime import PreviewRuntime
    app=QApplication([]);app.setStyle('Fusion');errors=[]
    sys.excepthook=lambda kind,value,tb:(errors.append(repr(value)),sys.__excepthook__(kind,value,tb))
    def wait(predicate):
        end=time.monotonic()+8
        while time.monotonic()<end:
            QTest.qWait(20)
            if predicate():return
        raise AssertionError(('timeout',errors))
    assert '[IP]' in redact('192.168.1.42 token=secret api_key=other')
    assert 'secret' not in redact('token=secret') and 'other' not in redact('api_key=other')
    with tempfile.TemporaryDirectory(prefix='diagnostics-shell-') as temporary:
        root=Path(temporary);data=root/'data';data.mkdir();log=data/'preview.log'
        log.write_text(('x'*70000)+'\nERROR local_audio 192.168.1.42 token=secret\n',encoding='utf-8')
        before=hashlib.sha256(log.read_bytes()).hexdigest();rows=read_logs(data)
        assert len(rows)==1 and rows[0]['limited'] and len(rows[0]['text'])<=65536
        assert 'secret' not in rows[0]['text'] and '[IP]' in rows[0]['text']
        original=Path.is_symlink
        with patch.object(Path,'is_symlink',lambda path:path==log or original(path)):
            try:read_logs(data)
            except ValueError:pass
            else:raise AssertionError('Linked log accepted')
        runtime=PreviewRuntime(root/'runtime')
        audio_logger=logging.getLogger('quest_tool.audio');audio_logger.warning('audio test diagnostic')
        runtime.handler.flush();assert 'audio test diagnostic' in (runtime.path/'preview.log').read_text('utf-8')
        handler=runtime.handler;runtime.close();assert handler not in audio_logger.handlers
        for language in ('zh','en'):
            window=LibraryWindow(data,language);window.show();QTest.qWait(80)
            appearance=app.quest_window_appearance;native=appearance.native;appearance.native=None
            fallback=QDialog(window);fallback.resize(560,420);fallback.show();QTest.qWait(20)
            assert fallback.property('frameRoundFallback') and not fallback.mask().isEmpty()
            fallback.showMaximized();QTest.qWait(20);assert fallback.mask().isEmpty()
            fallback.showNormal();QTest.qWait(20);assert not fallback.mask().isEmpty()
            fallback.close();appearance.native=native
            window.logs.button.click();dialog=window.logs.dialogs[-1];wait(lambda:dialog.refresh.isEnabled())
            assert 'local_audio' in dialog.text.toPlainText() and 'secret' not in dialog.text.toPlainText()
            class Clipboard:
                text=''
                def setText(self,text):self.text=text
            clipboard=Clipboard()
            with patch.object(QApplication,'clipboard',return_value=clipboard):dialog.copy.click()
            assert clipboard.text==dialog.text.toPlainText()
            dialog.refresh.click();dialog.close();dialog.wait_closed();window.close();window.wait_closed()
            online=DownloadWindow(language=language,songs=());online.show();QTest.qWait(80)
            assert online.windowFlags() & Qt.WindowType.FramelessWindowHint
            for index in range(online.tabs.count()):
                online.tabs.setCurrentIndex(index);QTest.qWait(30)
                point=online.tabs.mapTo(online,online.tabs.rect().center())
                assert online.chrome.hit_test(point)==1
            for size in ((1000,760),(1700,1000)):
                online.resize(*size);QTest.qWait(80)
                assert online.width()<=size[0],(language,online.size(),online.minimumSizeHint())
                for button in (online.chrome.minimize,online.chrome.maximize,online.chrome.close_button):
                    point=button.mapTo(online.header,QPoint());assert online.header.rect().contains(point)
                for index in range(online.tabs.count()):assert online.tabs.rect().contains(online.tabs.tabRect(index))
                if size[0]==1000:assert online.header.wrapped
                if not online.header.wrapped:assert online.header.height()==61,(online.header.height(),online.header.wrapped)
                else:assert online.header.height()>61
            if sys.platform=='win32' and sys.getwindowsversion().build>=22000:
                assert online.property('windowCornerResult')==0
                assert not online.property('frameRoundFallback')
            online.chrome.maximize.click();wait(online.isMaximized);online.chrome.maximize.click();wait(lambda:not online.isMaximized())
            online.logs_action.trigger();logs=online.logs.dialogs[-1];wait(lambda:logs.refresh.isEnabled())
            assert logs.text.toPlainText() and not logs.root
            logs.close();logs.wait_closed()
            if os.environ.get('QUEST_QT_SHOTS'):
                online.grab().save(str(Path(os.environ['QUEST_QT_SHOTS'])/f'download-shell-{language}-{os.environ.get("QT_SCALE_FACTOR")}.png'))
            online.close();online.logs.wait_closed();QTest.qWait(30)
        assert hashlib.sha256(log.read_bytes()).hexdigest()==before and not errors,errors
    print('PASS merged download chrome/native corners/compact header/read-only bounded logs/redaction/audio logging/copy/close')


if __name__=='__main__':
    if '--child' in sys.argv:child()
    else:
        for scale in ('1','1.25','1.5'):
            result=subprocess.run([sys.executable,'-B',__file__,'--child'],cwd=ROOT,env=dict(os.environ,QT_SCALE_FACTOR=scale),capture_output=True,timeout=120)
            print(scale,result.stdout.decode('utf-8','replace'),result.stderr.decode('utf-8','replace'),flush=True);assert result.returncode==0
