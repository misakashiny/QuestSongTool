"""Qt report export and metadata preservation with sealed device/process access."""
import copy
import csv
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))


def child():
    from PySide6.QtWidgets import QApplication, QFileDialog
    from quest_tool import adb, device
    from quest_tool.adb import AdbDevice
    from quest_tool.downloads import DownloadError
    from quest_tool.messages import tr
    from quest_tool.qt_ui.device_protocol import descriptor,snapshot_rows,optional_metadata,report_fields
    from quest_tool.qt_ui.adb_metadata import READ_ROOTS
    from quest_tool.qt_ui.adb_provider import AdbReadOnlyProvider
    from quest_tool.qt_ui.library_window import LibraryWindow
    from quest_tool.qt_ui import library_report as module
    from quest_tool.songs import SongMeta
    app=QApplication([]);app.setStyle('Fusion');errors=[];external=[];threads=[]
    def report(kind,value,tb):errors.append(repr(value));sys.__excepthook__(kind,value,tb)
    sys.excepthook=report
    def forbidden(*args,**kwargs):external.append(True);raise AssertionError('real external I/O')
    def wait(predicate):
        end=time.monotonic()+12
        while time.monotonic()<end:
            app.processEvents()
            if predicate():return
            time.sleep(.005)
        raise AssertionError('Qt report timeout')
    def rejects(call):
        try:call()
        except (ValueError,DownloadError):return
        raise AssertionError('invalid metadata accepted')
    minimal=dict(folder='legacy',title='Legacy',author='',error='',duration=1)
    assert snapshot_rows([minimal])==[minimal] and optional_metadata(minimal)=={}
    for value in (True,-1,float('nan'),float('inf'),'120'):
        rejects(lambda value=value:snapshot_rows([dict(minimal,bpm=value)]))
    for value in ('Expert', [1], ['bad\nname'], ['x'*257], ['Hard']*129):
        rejects(lambda value=value:snapshot_rows([dict(minimal,difficulties=value)]))
    assert report_fields(SongMeta('bad',bpm=float('nan'),difficulties=('Hard',)))=={'difficulties':('Hard',)}
    root=sorted(READ_ROOTS)[0]
    selected=descriptor(dict(kind='adb',device_id='["SIM","HW"]',root=root,label='Test Quest',
        state='device',model='Quest 3',codename='eureka',brand='oculus',identity_verified=True))
    info={'_songName':'中文, "标题"','_songAuthorName':'Artist','_beatsPerMinute':120,
        '_songDuration':123.6,'_difficultyBeatmapSets':[{'_difficultyBeatmaps':[{'_difficulty':'ExpertPlus'}]}]}
    with tempfile.TemporaryDirectory() as directory,patch.object(subprocess,'Popen',forbidden),patch.object(adb.AdbBridge,'__init__',forbidden),patch.object(device,'scan_quest',forbidden):
        base=Path(directory).resolve();local=base/'local';local.mkdir()
        for name in ('both','local-only'):
            song=local/name;song.mkdir();(song/'Info.dat').write_text(json.dumps(info),encoding='utf-8')
        before={p.relative_to(local).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in local.rglob('*') if p.is_file()}
        # Exercise the actual provider parsing path with all transport functions sealed.
        provider=AdbReadOnlyProvider('unused.exe');calls=[];item=AdbDevice('SIM','device','Quest 3','eureka','oculus')
        def run(_bridge,args,*_args,**_kwargs):calls.append(args);assert args[0]=='shell';return 'both/\n'
        with (patch.object(provider,'_devices',return_value=[item]),
              patch.object(provider,'_identify',return_value=(SimpleNamespace(serial='SIM'),item,'HW')),
              patch.object(provider,'_root',return_value=root),patch.object(provider,'_run',side_effect=run),
              patch.object(provider,'_contents',return_value=iter([('both',json.dumps(info).encode())]))):
            parsed=provider.read_library(selected,threading.Event())['rows']
        assert len(calls)==1 and parsed[0]['bpm']==120 and parsed[0]['difficulties']==('ExpertPlus',)
        language=os.environ['TEST_LANG'];window=LibraryWindow(base/'preview',language);window.show()
        try:
            reports=window.reports
            # Export tests use declared fixture identities. Real map hashing and
            # renamed/same-name-different-content cases live in smoke_usability.
            original_snapshot=window.set_quest_snapshot
            def verified_snapshot(rows,identity):
                original_snapshot(rows,identity)
                window.identity_checks.values={side:{row['folder']:hashlib.sha1(row['folder'].encode()).hexdigest() for row in model.rows} for side,model in (('local',window.model),('quest',window.quest_model))}
                window.update_comparison();reports.changed()
            window.set_quest_snapshot=verified_snapshot
            assert not reports.button.isEnabled()
            assert window.set_target(str(local));wait(lambda:window.local_ready and window.ratings.ready)
            assert window.model.rows[0]['bpm']==120 and window.model.rows[0]['difficulties']==('ExpertPlus',)
            window.device_browser.selected=selected
            quest=[dict(parsed[0],bpm=999),dict(folder='quest-only',title='Remote',author='Q',error='',duration=61,bpm=95,difficulties=('Hard',))]
            window.set_quest_snapshot(quest,selected['identity']);window.ratings.values={'both':4};window.ratings.publish()
            wait(lambda:reports.button.isEnabled())
            assert window.quest_model.rows[0]['bpm']==999 and window.quest_model.rows[1]['difficulties']==('Hard',)
            assert reports.button.menu() is reports.menu and reports.actions['csv'].text()==tr('qt.report.csv')
            window.search.setText('NO_MATCH');window.model.checked={'both'};app.processEvents();assert window.proxy.rowCount()==0
            original_writer=module.write_report
            def tracked(*args):threads.append(threading.get_ident());return original_writer(*args)
            output=base/'comparison.csv'
            with patch.object(QFileDialog,'getSaveFileName',return_value=(str(output),'CSV')),patch.object(module,'write_report',side_effect=tracked):
                reports.actions['csv'].trigger();wait(lambda:not reports.busy and reports.state=='saved')
            assert output.read_bytes().startswith(b'\xef\xbb\xbf') and b'\r\r\n' not in output.read_bytes()
            with output.open(encoding='utf-8-sig',newline='') as stream:rows=list(csv.reader(stream))
            assert len(rows)==4 and len(rows[0])==8
            byname={row[1]:row for row in rows[1:]}
            assert byname['both'][4:] == ['120','2:04','ExpertPlus','4']
            assert byname['local-only'][7]=='' and byname['quest-only'][7]==tr('search.unknown')
            assert byname['quest-only'][4:7]==['95','1:01','Hard']
            assert byname['both'][0]==tr('report.state.both')
            md=base/'comparison.md'
            with patch.object(QFileDialog,'getSaveFileName',return_value=(str(md),'Markdown')):
                reports.open('md');wait(lambda:not reports.busy and reports.state=='saved')
            text=md.read_text(encoding='utf-8');assert tr('report.document_title') in text and root in text
            assert tr('report.summary_line',total=3,both=1,local=1,quest=1) in text
            with patch.object(QFileDialog,'getSaveFileName',return_value=('','')):reports.open('csv')
            assert not reports.busy
            for path in (local/'forbidden.csv',window.sandbox/'forbidden.csv',base/'wrong.json'):
                with patch.object(QFileDialog,'getSaveFileName',return_value=(str(path),'')):
                    reports.open('csv');wait(lambda:not reports.busy and reports.state=='failed')
                assert not path.exists()
            # A failed renderer preserves an existing selected report.
            old=base/'old.csv';old.write_bytes(b'preserve')
            with patch.object(QFileDialog,'getSaveFileName',return_value=(str(old),'')),patch.object(module,'render_report',side_effect=ValueError('test failure')):
                reports.open('csv');wait(lambda:not reports.busy and reports.state=='failed')
            assert old.read_bytes()==b'preserve'
            # A legacy provider may omit optional fields; keep them explicitly unknown.
            window.set_quest_snapshot([minimal],selected['identity'])
            legacy=module.report_rows(reports.snapshot())
            legacyrow=next(row for row in legacy if row[1]=='legacy')
            assert legacyrow[4]==legacyrow[6]==legacyrow[7]==tr('search.unknown')
            # Empty, loaded libraries are distinct from never-loaded libraries.
            local_rows=copy.deepcopy(window.model.rows);window.model.replace([])
            window.set_quest_snapshot([],selected['identity']);empty=base/'empty.csv'
            with patch.object(QFileDialog,'getSaveFileName',return_value=(str(empty),'')):
                reports.open('csv');wait(lambda:not reports.busy and reports.state=='saved')
            with empty.open(encoding='utf-8-sig',newline='') as stream:assert len(list(csv.reader(stream)))==1
            window.model.replace(local_rows);window.set_quest_snapshot(quest,selected['identity'])
            entered=threading.Event();release=threading.Event();cancelled=base/'cancelled.csv'
            def held(*args):entered.set();assert release.wait(10);return original_writer(*args)
            with patch.object(QFileDialog,'getSaveFileName',return_value=(str(cancelled),'')),patch.object(module,'write_report',side_effect=held):
                reports.open('csv');wait(entered.is_set);window.clear_quest_snapshot();release.set()
                wait(lambda:not reports.busy and not reports.tasks.busy())
            assert not cancelled.exists() and not reports.button.isEnabled()
            window.set_quest_snapshot(quest,selected['identity']);wait(lambda:reports.button.isEnabled())
            # A switch after publication retains the valid earlier-snapshot file.
            entered.clear();release.clear();earlier=base/'earlier.csv'
            def published(*args):
                result=original_writer(*args);entered.set();assert release.wait(10);return result
            with patch.object(QFileDialog,'getSaveFileName',return_value=(str(earlier),'')),patch.object(module,'write_report',side_effect=published):
                reports.open('csv');wait(entered.is_set);window.clear_quest_snapshot();release.set()
                wait(lambda:not reports.busy and not reports.tasks.busy())
            assert earlier.exists() and reports.state=='snapshot_saved'
            window.set_quest_snapshot(quest,selected['identity']);window.search.clear();app.processEvents()
            for width in (1000,1400):
                window.resize(width,850);app.processEvents()
                assert not reports.button.isVisible() and not reports.status.isVisible()
            shots=os.environ.get('QUEST_QT_SHOTS')
            if shots:
                folder=Path(shots);folder.mkdir(parents=True,exist_ok=True)
                window.grab().save(str(folder/f'report-{language}-{os.environ.get("QT_SCALE_FACTOR")}.png'))
            entered.clear();release.clear();closed=base/'closed.csv'
            with patch.object(QFileDialog,'getSaveFileName',return_value=(str(closed),'')),patch.object(module,'write_report',side_effect=held):
                reports.open('csv');wait(entered.is_set);window.close();release.set();window.wait_closed()
            assert not closed.exists() and not list(base.rglob('.quest-report-*.tmp'))
            assert before=={p.relative_to(local).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in local.rglob('*') if p.is_file()}
            assert threads and all(t!=threading.get_ident() for t in threads)
        finally:
            if not window.closed:window.close();window.wait_closed()
        assert not errors and not external,(errors,external)
        assert not any(n.startswith(('tkinter','customtkinter')) for n in sys.modules)
        print('PASS Qt report: optional metadata/local/provider/window, bilingual whole-union CSV/MD, ratings/unknown, empty/cancel/error/late/close, source preserved')


if __name__=='__main__':
    if '--child' in sys.argv:child()
    else:
        for language in ('zh','en'):
            for scale in ('1','1.25','1.5'):
                result=subprocess.run([sys.executable,'-B',__file__,'--child'],cwd=ROOT,
                    env=dict(os.environ,TEST_LANG=language,QT_SCALE_FACTOR=scale),capture_output=True,timeout=120)
                print(language,scale,result.stdout.decode('utf-8','replace'),result.stderr.decode('utf-8','replace'),flush=True)
                assert result.returncode==0
