"""Transfer confirmation estimates and result categories; no device or network I/O."""
from pathlib import Path
import json
import sys
import tempfile
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))


def plan_fixture(root):
    from quest_tool.qt_ui.transfer_queue import TransferPlan
    from quest_tool.qt_ui.device_protocol import descriptor
    device = descriptor(dict(kind='adb', device_id=json.dumps(['SIM','HW'],separators=(',',':')),
                              root='/sdcard/CustomLevels',label='Simulated Quest 3',state='device',
                              model='Quest_3', codename='eureka', brand='Meta',
                              identity_verified=True))
    return TransferPlan.create('to_quest', str(root), device, ('song-a','song-b'))


class Events:
    def __init__(self):self.values=[]
    def drain(self):
        values,self.values=self.values,[]
        return values
    def busy(self):return False


class Queue:
    def __init__(self):self.tasks=Events();self.started=[];self.cancelled=False
    def start(self,plan):self.started.append(plan);return 7
    def toggle_pause(self):return False
    def cancel(self):self.cancelled=True
    def close(self):pass


def main():
    from PySide6.QtWidgets import QApplication
    from quest_tool.qt_ui.transfer_dialog import TransferDialog
    from quest_tool.qt_ui.transfer_preflight import estimate_local_tree, format_bytes
    with tempfile.TemporaryDirectory(prefix='transfer-preflight-') as temp:
        root=Path(temp);(root/'song-a').mkdir();(root/'song-b').mkdir()
        (root/'song-a'/'Info.dat').write_text(json.dumps({'_songName':'A'}),encoding='utf-8')
        (root/'song-a'/'Expert.dat').write_bytes(b'a'*1024)
        (root/'song-b'/'Info.dat').write_text(json.dumps({'_songName':'B'}),encoding='utf-8')
        (root/'song-b'/'Expert.dat').write_bytes(b'b'*2048)
        estimate=estimate_local_tree(root,('song-a','song-b'))
        assert estimate['bytes']==len((root/'song-a'/'Info.dat').read_bytes())+1024+len((root/'song-b'/'Info.dat').read_bytes())+2048
        assert estimate['files']==4 and estimate['unreadable']==0 and estimate['unsafe']==0 and not estimate['limited']
        assert format_bytes(1024*1024)=='1.0 MiB'
        app=QApplication.instance() or QApplication([]);app.setStyle('Fusion')
        plan=plan_fixture(root);queue=Queue();dialog=TransferDialog(queue,plan,simulation=False)
        assert '本地估算' in dialog.preflight.text() or 'Local estimate' in dialog.preflight.text()
        assert '跳过' in dialog.preflight.text() or 'skipped' in dialog.preflight.text()
        dialog.generation=7
        queue.tasks.values=[SimpleNamespace(payload=dict(generation=7,state='done',ok=1,skipped=1,failures=(('song-b','checksum'),),error='',total=2))]
        dialog.poll()
        assert not dialog.result_summary.isHidden() and ('结果分类' in dialog.result_summary.text() or 'Result categories' in dialog.result_summary.text())
        assert dialog.retry.isEnabled()
        dialog.prepare_retry();assert dialog.plan.folders==('song-b',) and dialog.start.isEnabled() and dialog.result_summary.isHidden()
        dialog.close()
        for state in ('cancelled','blocked'):
            q=Queue();d=TransferDialog(q,plan,simulation=False);d.generation=7
            q.tasks.values=[SimpleNamespace(payload=dict(generation=7,state=state,ok=0,skipped=0,failures=(),error='transfer_identity' if state=='blocked' else '',total=2))]
            d.poll();assert not d.result_summary.isHidden();d.close()
        app.processEvents()
    print('PASS transfer preflight: bounded size estimate, skip policy, result categories, explicit failure retry, no device I/O')


if __name__=='__main__':main()
