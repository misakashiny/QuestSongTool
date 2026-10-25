"""M7B1: real Qt + sandbox copies; all device/process access forbidden."""
import copy
from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def child():
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    from quest_tool import adb, device
    from quest_tool.fsapi import LocalFS, Location
    from quest_tool.messages import set_language
    from quest_tool.qt_ui.adb_metadata import READ_ROOTS
    from quest_tool.qt_ui.transfer_queue import TransferPlan, TransferQueue
    from quest_tool.qt_ui.transfer_dialog import TransferDialog
    from quest_tool.qt_ui.theme import apply_theme
    from quest_tool.workers import guard_copy, GuardError
    app = QApplication([])
    app.setStyle('Fusion')
    errors = []
    external = []
    def report(kind, value, tb):errors.append(repr(value))
    sys.excepthook = report
    def forbidden(*args, **kwargs):
        external.append('external')
        raise AssertionError('offline transfer test attempted real I/O')
    subprocess.Popen = forbidden
    adb.AdbBridge.__init__ = forbidden
    device.scan_quest = forbidden
    def wait(predicate, seconds=8):
        end = time.monotonic()+seconds
        while time.monotonic()<end:
            app.processEvents()
            if predicate():return
            time.sleep(.005)
        raise AssertionError('transfer timeout')
    def rejects(call):
        try:call()
        except (GuardError, ValueError):return
        raise AssertionError('invalid plan accepted')
    selected = dict(kind='adb', device_id='SIMULATED-QUEST', root=sorted(READ_ROOTS)[0],
        label='Simulated Quest', state='device', model='Quest 3', codename='eureka',
        brand='oculus', identity_verified=True)
    class Backend:
        def __init__(self, remote):
            self.remote = remote
            self.calls = []
            self.fail = set()
            self.invalid = set()
            self.fresh = copy.deepcopy(selected)
            self.entered = threading.Event()
            self.release = threading.Event()
            self.hold = False
            self.checks = 0
        def roots(self, plan):
            return (Path(plan.local), self.remote) if plan.direction=='to_quest' else (self.remote, Path(plan.local))
        def revalidate(self, plan):
            self.checks += 1
            self.calls.append(('identity', '', threading.get_ident()))
            return copy.deepcopy(self.fresh)
        def exists(self, plan, folder):
            self.calls.append(('exists', folder, threading.get_ident()))
            return (self.roots(plan)[1]/folder).exists()
        def copy_verified(self, plan, folder):
            self.calls.append(('copy', folder, threading.get_ident()))
            self.entered.set()
            if self.hold:assert self.release.wait(8)
            if folder in self.fail:raise OSError('simulated single song failure')
            if folder in self.invalid:return False
            source, target = self.roots(plan)
            src, _dst = guard_copy(source, target, folder)
            fs = LocalFS(Location(kind='local', root=str(target)))
            fs.copy_into(folder, src)
            fs.verify_copy(folder, src)
            return True
    with tempfile.TemporaryDirectory() as directory:
        base = Path(directory).resolve()
        local = base/'local';local.mkdir()
        for name in ('中文歌曲', 'second', 'existing', 'failed', 'unverified'):
            song = local/name;song.mkdir()
            (song/'Info.dat').write_text(json.dumps({'_songName':name}), encoding='utf-8')
        original = {p.relative_to(local).as_posix():p.read_bytes() for p in local.rglob('*') if p.is_file()}
        plan = TransferPlan.create('to_quest', local, selected, ['中文歌曲', 'existing', 'failed', 'unverified', 'second'])
        for names in ([], ['..'], ['../bad'], ['bad/name'], ['x\\y'], ['x:y'], ['NUL'], ['a.'], [' bad'], ['.hidden'], ['a__旧版本_questbak'], ['a','A'], ['x\x00']):
            rejects(lambda names=names:TransferPlan.create('to_quest', local, selected, names))
        rejects(lambda:TransferPlan.create('bad', local, selected, ['second']))
        rejects(lambda:TransferPlan.create('to_quest', 'relative', selected, ['second']))
        rejects(lambda:TransferPlan.create('to_quest', local, dict(selected, root='/sdcard/Other'), ['second']))
        rejects(lambda:replace(plan, folders=('../escape',)).validate())
        rejects(lambda:TransferQueue(None))
        # Plan owns a stable immutable identity, independent of caller dictionaries.
        altered = copy.deepcopy(selected)
        fixed = TransferPlan.create('to_quest', local, altered, ['second'])
        altered['device_id'] = 'OTHER'
        assert dict(fixed.device)['device_id']=='SIMULATED-QUEST'
        remote = base/'remote';remote.mkdir();(remote/'existing').mkdir()
        (remote/'existing'/'preserve.txt').write_bytes(b'preserve')
        backend = Backend(remote);backend.fail={'failed'};backend.invalid={'unverified'}
        queue = TransferQueue(backend)
        generation = queue.start(plan);queue.wait_closed()
        messages = queue.tasks.drain();result = messages[-1].payload
        assert result['state']=='done' and result['ok']==2 and result['skipped']==1
        assert [name for name,_error in result['failures']]==['failed','unverified']
        assert backend.checks==5 and all(m.payload['generation']==generation for m in messages)
        assert (remote/'existing'/'preserve.txt').read_bytes()==b'preserve'
        assert not (remote/'unverified').exists()
        # Reverse direction also goes through existing LocalFS verification.
        destination = base/'reverse';destination.mkdir()
        queue.start(TransferPlan.create('to_local', destination, selected, ['中文歌曲','second']))
        queue.wait_closed()
        assert queue.tasks.drain()[-1].payload['ok']==2
        assert (destination/'中文歌曲'/'Info.dat').read_bytes()==original['中文歌曲/Info.dat']
        # A changed device (including hardware on the same serial) blocks before exists/copy.
        for change in ({'device_id':'OTHER'}, {'root':sorted(READ_ROOTS)[-1]}, {'model':'Quest 2','codename':'hollywood'}):
            blocked = Backend(remote);blocked.fresh.update(change)
            job = TransferQueue(blocked);job.start(fixed);job.wait_closed()
            assert job.tasks.drain()[-1].payload['state']=='blocked'
            assert [call[0] for call in blocked.calls]==['identity']
        # Cancellation after an existence probe must not begin the song copy.
        blocked = Backend(remote);job = TransferQueue(blocked)
        def cancel_exists(_plan, _folder):job.cancel();return False
        blocked.exists = cancel_exists
        job.start(fixed);job.wait_closed()
        assert job.tasks.drain()[-1].payload['state']=='cancelled'
        assert not any(call[0]=='copy' for call in blocked.calls)
        # Identity changes between songs stop the next song, preserving partial success.
        changing_target=base/'changing';changing_target.mkdir()
        changing=Backend(changing_target);original_check=changing.revalidate
        def changing_check(item):
            fresh=original_check(item)
            if changing.checks>1:fresh['device_id']='REPLACED'
            return fresh
        changing.revalidate=changing_check
        job=TransferQueue(changing)
        job.start(TransferPlan.create('to_quest',local,selected,['中文歌曲','second']))
        job.wait_closed();result=job.tasks.drain()[-1].payload
        assert result['state']=='blocked' and result['ok']==1 and not (changing_target/'second').exists()
        # Pause/resume at a boundary resumes the remaining song exactly once.
        resumed_target=base/'resumed';resumed_target.mkdir()
        resumed=Backend(resumed_target);resumed.hold=True;job=TransferQueue(resumed)
        job.start(TransferPlan.create('to_quest',local,selected,['中文歌曲','second']))
        wait(resumed.entered.is_set);assert job.toggle_pause()
        resumed.release.set();wait(lambda:(resumed_target/'中文歌曲').exists())
        assert not job.toggle_pause()
        job.wait_closed();assert job.tasks.drain()[-1].payload['ok']==2
        for language in ('zh','en'):
            set_language(language);apply_theme(app, 'light')
            target = base/language;target.mkdir()
            backend = Backend(target);backend.hold=True
            job = TransferQueue(backend)
            dialog = TransferDialog(job, TransferPlan.create('to_quest',local,selected,['中文歌曲','second']))
            installed = []
            dialog.resultReady.connect(lambda data:installed.append((threading.get_ident(), data)))
            dialog.show();dialog.start.click();wait(backend.entered.is_set)
            rejects(lambda:job.start(fixed))
            dialog.pause.click();assert job.gate.paused
            backend.release.set()
            wait(lambda:(target/'中文歌曲'/'Info.dat').exists())
            # Paused at the song boundary: second never starts.
            for _ in range(30):app.processEvents();time.sleep(.005)
            assert not (target/'second').exists()
            dialog.cancel_button.click()
            wait(lambda:dialog.result is not None)
            assert dialog.result['state']=='cancelled' and dialog.result['ok']==1
            assert installed[0][0]==threading.get_ident()
            assert all(thread!=threading.get_ident() for _action,_folder,thread in backend.calls)
            # A late message for another generation cannot replace the displayed result.
            previous = dialog.result
            job.tasks.emit('transfer',payload={'generation':-1,'state':'done'})
            dialog.poll();assert dialog.result is previous
            for width in (540,760):
                dialog.resize(width,350);app.processEvents()
                controls=(dialog.scope,dialog.summary,dialog.status,dialog.progress,dialog.start,dialog.pause,dialog.cancel_button)
                for control in controls:
                    assert control.width()>0 and control.height()>0
                    assert dialog.rect().contains(control.geometry()), (language, control.geometry(),dialog.rect())
                for label in (dialog.scope,dialog.summary,dialog.status):
                    assert label.height()>=label.heightForWidth(label.width())
                for first,second in zip(controls[:4], controls[1:4]):assert not first.geometry().intersects(second.geometry())
            shots = os.environ.get('QUEST_QT_SHOTS')
            if shots:
                path = Path(shots);path.mkdir(parents=True,exist_ok=True)
                dialog.grab().save(str(path/f'transfer-{language}-{os.environ.get("QT_SCALE_FACTOR","1")}.png'))
            dialog.close();job.wait_closed();assert not dialog.timer.isActive()
            # Closing an active dialog cancels only after its in-flight song,
            # never starts the next one, and cannot install a late result.
            closing_target=base/('closing-'+language);closing_target.mkdir()
            backend=Backend(closing_target);backend.hold=True;job=TransferQueue(backend)
            dialog=TransferDialog(job,TransferPlan.create('to_quest',local,selected,['中文歌曲','second']))
            dialog.show();dialog.start.click();wait(backend.entered.is_set)
            dialog.close();backend.release.set();job.wait_closed();dialog.poll()
            assert dialog.result is None and not dialog.timer.isActive()
            assert (closing_target/'中文歌曲').exists() and not (closing_target/'second').exists()
            # Escape follows close/cancel cleanup instead of merely hiding QDialog.
            backend = Backend(target);job = TransferQueue(backend)
            dialog = TransferDialog(job,fixed);dialog.show()
            QTest.keyClick(dialog,Qt.Key.Key_Escape);app.processEvents()
            assert dialog.closed and job.closed
            rejects(lambda:job.start(fixed))
            print('PASS',language,'both-directions/guards/verification/skip/failure/pause/cancel/late/close/layout')
        assert {p.relative_to(local).as_posix():p.read_bytes() for p in local.rglob('*') if p.is_file()}==original
        assert not external and not errors,(external,errors)
        assert not any(name.startswith(('tkinter','customtkinter')) for name in sys.modules)


if __name__=='__main__':
    if '--child' in sys.argv:child()
    else:
        for scale in ('1','1.25','1.5'):
            result = subprocess.run([sys.executable,'-B',__file__,'--child'],cwd=ROOT,
                env=dict(os.environ, QT_SCALE_FACTOR=scale), capture_output=True, timeout=100)
            print(scale,result.stdout.decode('utf-8','replace'),result.stderr.decode('utf-8','replace'))
            assert result.returncode==0
