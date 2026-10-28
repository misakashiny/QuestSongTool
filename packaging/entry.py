"""Single-file desktop entry. Production UI and business modules stay shared."""
from pathlib import Path
import json
import subprocess
import sys
import traceback


def bundle_root():
    return Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parents[1]))


def smoke_mode(report, args):
    """Explicit isolated build verification; never probes or changes a headset."""
    from PySide6.QtCore import QTimer, qVersion
    from PySide6.QtWidgets import QApplication
    from quest_tool.constants import APP_VERSION
    from quest_tool.qt_ui.library_window import LibraryWindow
    original=QApplication.exec
    def execute():
        app=QApplication.instance()
        def check():
            result=dict(passed=False,version=APP_VERSION,qt=qVersion(),frozen=bool(getattr(sys,'frozen',False)))
            try:
                import miniaudio
                import io
                import wave
                from PIL import Image
                from quest_tool.qt_ui.download_window import DownloadWindow
                from quest_tool.qt_ui.wireless_form import WirelessForm
                from quest_tool.qt_ui.icons import line_icon
                from quest_tool.qt_ui.adb_playback import AUDIO_LIMIT
                window=next(w for w in app.topLevelWidgets() if isinstance(w,LibraryWindow))
                assert window.isVisible() and not window.closed
                assert not line_icon('music','#ffffff').isNull()
                assert Image.new('RGB',(1,1)).size==(1,1) and callable(miniaudio.stream_any)
                encoded=io.BytesIO()
                with wave.open(encoded,'wb') as fixture:
                    fixture.setnchannels(1);fixture.setsampwidth(2);fixture.setframerate(8000)
                    fixture.writeframes(b'\x00\x01'*800)
                assert miniaudio.decode(encoded.getvalue(),sample_rate=8000,nchannels=1).num_frames==800
                result['native_audio_decode']=True
                assert AUDIO_LIMIT==64*1024*1024
                adb=bundle_root()/'platform-tools'/'adb.exe'
                if adb.is_file():
                    text=subprocess.check_output([str(adb),'version'],timeout=15,creationflags=subprocess.CREATE_NO_WINDOW).decode('utf-8','replace')
                    assert 'Android Debug Bridge' in text;result['bundled_adb']=text.splitlines()[0]
                elif getattr(sys,'frozen',False):raise AssertionError('Bundled ADB missing')
                window.playlists.show();window.playlists.dialog.close()
                device=WirelessForm(window);device.show();device.close()
                download=DownloadWindow('zh','light',songs=[]);download.show();download.close()
                window.grab().save(str(report.with_suffix('.png')))
                result.update(passed=True,library=True,playlists=True,device_manager=True,downloads=True,media_imports=True)
            except Exception:result['error']=traceback.format_exc()
            finally:
                report.parent.mkdir(parents=True,exist_ok=True)
                report.write_text(json.dumps(result,ensure_ascii=False,indent=2),'utf-8')
                app.closeAllWindows();app.quit()
        QTimer.singleShot(400,check)
        return original()
    QApplication.exec=staticmethod(execute)


def main():
    args=list(sys.argv[1:]);report=None
    if '--self-test-report' in args:
        index=args.index('--self-test-report');report=Path(args[index+1]).absolute();del args[index:index+2]
        if '--data-dir' not in args:raise SystemExit('Self-test requires an isolated --data-dir')
    download='--downloads' in args
    if download:args.remove('--downloads')
    if not download and not any(flag in args for flag in ('--components','--samples','--help','-h')):
        if '--library' not in args:args.insert(0,'--library')
        if '--quest-adb' not in args:args.append('--quest-adb')
        if '--adb-path' not in args:
            adb=bundle_root()/'platform-tools'/'adb.exe'
            if adb.is_file():args.extend(('--adb-path',str(adb)))
    if report:smoke_mode(report,args)
    from quest_tool.qt_ui.bootstrap import run
    code=run(args)
    if report and (not report.is_file() or not json.loads(report.read_text('utf-8')).get('passed')):return 1
    return code


if __name__=='__main__':raise SystemExit(main())
