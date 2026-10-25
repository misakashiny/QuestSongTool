"""Offline Qt layout: live models, device controls, resize and stable selection."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))


def child():
    from PIL import Image,ImageDraw
    from PySide6.QtCore import QPoint,Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication,QWidget,QStyle,QStyleOptionViewItem
    from quest_tool.qt_ui.library_window import LibraryWindow
    from quest_tool.theme import DESKTOP
    app=QApplication([]);app.setStyle('Fusion');errors=[]
    def error(kind,value,tb):
        errors.append(repr(value));sys.__excepthook__(kind,value,tb)
    sys.excepthook=error
    def wait(predicate):
        end=time.monotonic()+10
        while time.monotonic()<end:
            QTest.qWait(20)
            if predicate():return
        raise AssertionError('library design wait')
    def inside(widget,ancestor):
        point=widget.mapTo(ancestor,QPoint())
        assert point.x()>=0 and point.y()>=0,(widget.objectName(),point)
        assert point.x()+widget.width()<=ancestor.width()+1,(widget.objectName(),widget.width(),ancestor.width())
        assert point.y()+widget.height()<=ancestor.height()+1,(widget.objectName(),widget.height(),ancestor.height())
    device=dict(kind='adb',device_id='fixture',root='/sdcard/CustomLevels',label='Quest 3 · Studio Demo',
                state='device',model='Quest 3',codename='eureka',brand='oculus',identity_verified=True)
    class Provider:
        def list_devices(self,cancel):return [dict(device)]
        def read_library(self,selected,cancel):return dict(device=dict(device),connected=True,rows=list(snapshot))
    with tempfile.TemporaryDirectory() as temp:
        base=Path(temp);library=base/'本地曲库';library.mkdir()
        for number in range(24):
            folder=library/f'demo-{number:02}';folder.mkdir()
            title=('Blue Horizon','星海回声','Electric Garden')[number%3]+f' {number+1:02}'
            (folder/'Info.dat').write_text(json.dumps(dict(_version='2.0.0',_songName=title,
                _songAuthorName='Studio Demo',_beatsPerMinute=128,_songDuration=193,_coverImageFilename='cover.png')),encoding='utf-8')
            image=Image.new('RGB',(256,256),('#4967b8','#a54a6f','#367e84')[number%3]);draw=ImageDraw.Draw(image)
            draw.ellipse((32,32,224,224),outline='white',width=4);draw.text((78,120),'STUDIO DEMO',fill='white')
            image.save(folder/'cover.png')
        for language in ('zh','en'):
            for mode in ('light','dark'):
                window=LibraryWindow(base/(language+mode),language,mode,device_provider=Provider())
                window.move(app.primaryScreen().availableGeometry().topLeft());window.show()
                assert QTest.qWaitForWindowExposed(window,5000)
                try:
                    window.set_target(str(library));wait(lambda:window.local_ready and not window.ratings.pending)
                    snapshot=[dict(row) for row in window.model.rows[:16]]
                    snapshot.append(dict(folder='quest-only',title='Quest exclusive',author='Studio Demo',duration=201,error=''))
                    browser=window.device_browser
                    QTest.mouseClick(browser.refresh,Qt.MouseButton.LeftButton);wait(lambda:not browser.busy)
                    browser.choices.setCurrentIndex(1)
                    QTest.mouseClick(browser.read,Qt.MouseButton.LeftButton);wait(lambda:window.quest_ready)
                    assert window.quest_model.rowCount()==17 and window.missing_model.rowCount()==8
                    window.view_mode.setCurrentIndex(window.view_mode.findData('list'));QTest.qWait(50)
                    index=window.proxy.index(0,0);rect=window.table.visualRect(index)
                    option=QStyleOptionViewItem();option.initFrom(window.table)
                    window.table.itemDelegate().initStyleOption(option,index);option.rect=rect
                    hit=window.table.style().subElementRect(QStyle.SubElement.SE_ItemViewItemCheckIndicator,option,window.table)
                    key=index.data(Qt.ItemDataRole.UserRole)['folder']
                    for expected in (True,False,True):
                        QTest.mouseClick(window.table.viewport(),Qt.MouseButton.LeftButton,pos=QPoint(hit.center().x(),rect.center().y()))
                        QTest.qWait(20)
                        assert (key in window.model.checked)==expected
                    checked=set(window.model.checked)
                    window.table.setCurrentIndex(window.proxy.index(0,0));wait(lambda:not window.details.loading)
                    detail_key=window.details.key
                    widgets=len(window.findChildren(QWidget))
                    for width,height in ((1000,640),(1280,824),(1600,900),(1000,700)):
                        work=app.primaryScreen().availableGeometry()
                        window.resize(min(width,work.width()),min(height,work.height()));QTest.qWait(100)
                        assert window.width()<=width,(language,mode,'minimum width',window.width(),width)
                        assert window.height()<=height,(language,mode,'minimum height',window.height(),height)
                        for control in (window.browse,window.reload,browser.choices,browser.refresh,browser.read,browser.clear):
                            inside(control,window.connection_panel)
                            assert control.width()>=control.minimumSizeHint().width()
                        assert browser.page.mapTo(window,QPoint()).y()<window.search.mapTo(window,QPoint()).y()
                        sizes=window.library_split.sizes()
                        if height==640 and os.environ.get('QUEST_QT_SHOTS'):
                            output=Path(os.environ['QUEST_QT_SHOTS']);output.mkdir(parents=True,exist_ok=True)
                            window.grab().save(str(output/'compact-debug.png'))
                        assert min(sizes)>=DESKTOP.LIBRARY_CARD_MIN,(language,mode,sizes)
                        assert max(sizes[:3])-min(sizes[:3])<=8,(language,mode,sizes)
                        for table in (window.table,window.quest_table,window.missing_table):
                            assert table.viewport().height()>=70,(language,mode,height,table.viewport().size())
                            assert table.verticalHeader().defaultSectionSize()==DESKTOP.LIBRARY_ROW_H
                        assert window.library_split.geometry().bottom()<window.preview.page.geometry().top()
                        for combo in (window.local_filter,window.quest_filter,window.missing_side):
                            assert combo.width()>=combo.minimumSizeHint().width(),(language,mode,combo.currentText(),combo.width(),combo.minimumSizeHint().width())
                        assert set(window.model.checked)==checked and window.details.key==detail_key
                    # Burst resize and scroll through the virtual view without per-item widgets.
                    for number in range(16):
                        window.resize(1100+number*8,760);window.table.verticalScrollBar().setValue(number);QTest.qWait(10)
                    assert len(window.findChildren(QWidget))<=widgets+2
                    assert set(window.model.checked)==checked
                    window.search.setText('Blue Horizon');assert window.proxy.rowCount()==8
                    window.search.clear();assert window.proxy.rowCount()==24
                    window.view_mode.setCurrentIndex(window.view_mode.findData('covers'));QTest.qWait(100)
                    assert window.grid.isVisible() and set(window.model.checked)==checked
                    window.view_mode.setCurrentIndex(window.view_mode.findData('list'))
                    window.resize(1400,850);QTest.qWait(80)
                    shots=os.environ.get('QUEST_QT_SHOTS')
                    if shots:
                        output=Path(shots);output.mkdir(parents=True,exist_ok=True)
                        assert window.grab().save(str(output/f'library-design-{language}-{mode}-{os.environ.get("QT_SCALE_FACTOR")}.png'))
                    assert not errors,errors
                finally:
                    window.close();window.wait_closed()
    assert not any(name.startswith(('tkinter','customtkinter')) for name in sys.modules)
    print('PASS library design: bilingual/themes/scales, compact device row, balanced columns, resize, live search, shared selection, bounded widgets, close')


if __name__=='__main__':
    if '--child' in sys.argv:child()
    else:
        for factor in ('1','1.25','1.5'):
            result=subprocess.run([sys.executable,'-B',__file__,'--child'],cwd=ROOT,
                env=dict(os.environ,QT_SCALE_FACTOR=factor),capture_output=True,timeout=150)
            print(factor,result.stdout.decode('utf-8','replace'),result.stderr.decode('utf-8','replace'))
            assert result.returncode==0
