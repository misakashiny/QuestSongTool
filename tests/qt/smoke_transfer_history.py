"""Read-only bounded journals and explicit retry: sealed transports, real Qt."""
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))


def child():
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication
    from quest_tool import adb, device
    from quest_tool.messages import set_language, tr
    from quest_tool.qt_ui.device_protocol import descriptor
    from quest_tool.qt_ui.adb_metadata import READ_ROOTS
    from quest_tool.qt_ui.transfer_queue import TransferQueue, TransferPlan
    from quest_tool.qt_ui.transfer_dialog import TransferDialog
    from quest_tool.qt_ui.transfer_history import read_journals
    from quest_tool.qt_ui.transfer_history_dialog import TransferHistoryDialog
    from quest_tool.qt_ui.library_window import LibraryWindow
    from quest_tool.qt_ui.theme import apply_theme
    app = QApplication([]);app.setStyle('Fusion')
    language = os.environ['TEST_LANG'];set_language(language);apply_theme(app)
    errors = [];external = []
    def report(kind,value,tb):errors.append(repr(value));sys.__excepthook__(kind,value,tb)
    sys.excepthook = report
    def forbidden(*args,**kwargs):external.append(True);raise AssertionError('real device/process access')
    def wait(predicate):
        end = time.monotonic()+12
        while time.monotonic()<end:
            app.processEvents()
            if predicate():return
            time.sleep(.005)
        raise AssertionError('history/retry timeout')
    with tempfile.TemporaryDirectory() as directory, patch.object(subprocess,'Popen',forbidden), patch.object(adb.AdbBridge,'__init__',forbidden), patch.object(device,'scan_quest',forbidden):
        base = Path(directory).resolve();local = base/'local';local.mkdir()
        selected = descriptor(dict(kind='adb',device_id='["SIM","HW"]',root=sorted(READ_ROOTS)[0],
            label='Test Quest',state='device',model='Quest 3',codename='eureka',brand='oculus',identity_verified=True))
        fixed = {key:selected[key] for key in ('identity','kind','device_id','root','model','codename','brand')}
        journals = base/'journals';journals.mkdir()
        transaction = 'a'*32;stage = selected['root']+'/.quest-transfer-'+transaction
        valid = dict(transaction=transaction,device=fixed,folder='中文歌曲',stage=stage,
            target=selected['root']+'/中文歌曲',backup=stage+'/previous',replace_existing=False,state='committed')
        path = journals/(transaction+'.json');path.write_text(json.dumps(valid),encoding='utf-8')
        for index,change in enumerate(({'target':'/sdcard/escape'},{'backup':'/sdcard/escape'},
                {'device':dict(fixed,identity='forged')},{'folder':'../bad'},{'replace_existing':1},
                {'state':'unknown'},{'backup_created':'yes'})):
            tx = f'{index:032x}';record = dict(valid,transaction=tx,stage=selected['root']+'/.quest-transfer-'+tx)
            record['backup']=record['stage']+'/previous';record.update(change)
            (journals/(tx+'.json')).write_text(json.dumps(record),encoding='utf-8')
        (journals/('b'*32+'.json')).write_bytes(b'{truncated')
        (journals/('c'*32+'.json')).write_bytes(b'x'*65537)
        (journals/'not-a-transaction.json').write_text(json.dumps(valid),encoding='utf-8')
        snapshot = {p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in journals.iterdir()}
        assert read_journals(base/'absent')=={'rows':[],'limited':False} and not (base/'absent').exists()
        data = read_journals(journals)
        assert len(data['rows'])==11 and sum(row['record'] is not None for row in data['rows'])==1
        with patch('quest_tool.qt_ui.transfer_history_dialog.read_journals',side_effect=lambda value:(
                (_ for _ in ()).throw(AssertionError('UI I/O')) if threading.current_thread() is threading.main_thread() else read_journals(value))):
            view = TransferHistoryDialog(journals);view.show()
            wait(lambda:view.refresh.isEnabled())
            assert view.listing.topLevelItemCount()==11
            valid_item = next(view.listing.topLevelItem(i) for i in range(11)
                if view.listing.topLevelItem(i).data(0,Qt.ItemDataRole.UserRole))
            view.listing.setCurrentItem(valid_item);app.processEvents()
            assert '中文歌曲' in view.details.toPlainText() and view.details.isReadOnly()
            assert valid_item.text(0)==tr('qt.transfer.history_committed')
            view.reload();view.close();view.wait_closed();app.processEvents()
        assert snapshot=={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in journals.iterdir()}
        # Bounded directory view; no misleading promise that UUID ordering is date ordering.
        for index in range(110):(journals/(f'{index+100:032x}'+'.json')).write_text('{}',encoding='utf-8')
        assert read_journals(journals)['limited'] and len(read_journals(journals)['rows'])==100
        class Backend:
            def __init__(self):
                self.fail=True;self.calls=[];self.journal_dir=journals
                self.hold=False;self.entered=threading.Event();self.release=threading.Event()
            def revalidate(self,plan):return copy.deepcopy(selected)
            def exists(self,plan,name):
                self.calls.append(('exists',name));return name=='existing'
            def copy_verified(self,plan,name):
                self.calls.append(('copy',name))
                if self.hold:self.entered.set();assert self.release.wait(10)
                if name=='failed' and self.fail:raise OSError('<b>plain failure</b>')
                return True
        for direction in ('to_local','to_quest'):
            backend=Backend();queue=TransferQueue(backend)
            plan=TransferPlan.create(direction,local,selected,['ok','existing','failed'])
            allowed=[True]
            dialog=TransferDialog(queue,plan,simulation=False,before_start=lambda:allowed[0]);dialog.show()
            dialog.begin();wait(lambda:dialog.result is not None and not queue.tasks.busy())
            assert dialog.result['ok']==1 and dialog.result['skipped']==1 and dialog.result['failures'][0][0]=='failed'
            assert '<b>plain failure</b>' in dialog.failures.toPlainText() and dialog.retry.isEnabled()
            allowed[0]=False;dialog.prepare_retry()
            assert dialog.result is not None and dialog.generation==1 and len(backend.calls)==5
            allowed[0]=True;dialog.prepare_retry()
            assert dialog.plan.folders==('failed',) and dialog.result is None and dialog.generation is None
            assert len(backend.calls)==5 and dialog.start.isEnabled()  # review only, no auto-start
            allowed[0]=False;dialog.begin();assert dialog.generation is None and len(backend.calls)==5
            allowed[0]=True;backend.fail=False;dialog.begin();wait(lambda:dialog.result is not None and not queue.tasks.busy())
            assert dialog.result['ok']==1 and dialog.generation==2 and backend.calls[-2:]==[('exists','failed'),('copy','failed')]
            assert not dialog.retry.isEnabled()
            dialog.close();queue.wait_closed()
        # Integration: journals remain available without a connected snapshot.
        backend=Backend();window=LibraryWindow(base/'preview',language,push_backend=backend);window.show()
        try:
            assert not window.transfers.push.isEnabled() and window.transfers.history.isVisible()
            window.transfers.show_history();history=window.transfers.histories[-1]
            wait(lambda:history.refresh.isEnabled())
            for width in (620,760,1000):
                history.resize(width,540);app.processEvents();app.processEvents()
                assert history.hint.height()>=history.hint.heightForWidth(history.hint.width())
                assert history.rect().contains(history.refresh.geometry()) and history.listing.height()>60
            window.transfers.show_history();assert len(window.transfers.histories)==1
            assert backend.calls==[]
            # Closing a second attempt must retain the coordinator's completion
            # tracking, then invalidate a Quest snapshot after worker completion.
            for name in ('ok','failed'):
                song=local/name;song.mkdir()
                (song/'Info.dat').write_text(json.dumps({'_songName':name}),encoding='utf-8')
            assert window.set_target(str(local));wait(lambda:window.local_ready)
            window.device_browser.selected=selected;window.set_quest_snapshot([],selected['identity'])
            window.model.checked={'ok','failed'}
            window.transfers.open('local','to_quest');attempt=window.transfers.dialog
            attempt.begin();wait(lambda:attempt.result is not None and not attempt.queue.tasks.busy())
            assert attempt.result['ok']==1 and len(attempt.result['failures'])==1 and not window.quest_ready
            window.set_quest_snapshot([],selected['identity'])
            attempt.prepare_retry();backend.fail=False;backend.hold=True;attempt.begin()
            wait(backend.entered.is_set)
            assert attempt.queue in window.transfers.pending
            attempt.close();assert window.quest_ready
            backend.release.set()
            wait(lambda:not window.quest_ready and not attempt.queue.tasks.busy())
            shots=os.environ.get('QUEST_QT_SHOTS')
            if shots:
                folder=Path(shots);folder.mkdir(parents=True,exist_ok=True)
                history.grab().save(str(folder/f'history-{language}-{os.environ.get("QT_SCALE_FACTOR")}.png'))
        finally:window.close();window.wait_closed()
        assert history.closed and not external and not errors,(external,errors)
        assert not any(n.startswith(('tkinter','customtkinter')) for n in sys.modules)
        print('PASS bounded/invalid journals, read-only worker, failed-only retry, binding change, explicit confirmation, generation, history integration')


if __name__=='__main__':
    if '--child' in sys.argv:child()
    else:
        for language in ('zh','en'):
            for scale in ('1','1.25','1.5'):
                result=subprocess.run([sys.executable,'-B',__file__,'--child'],cwd=ROOT,
                    env=dict(os.environ,TEST_LANG=language,QT_SCALE_FACTOR=scale),capture_output=True,timeout=120)
                print(language,scale,result.stdout.decode('utf-8','replace'),result.stderr.decode('utf-8','replace'),flush=True)
                assert result.returncode==0
