"""No playback or device access: detail layout and DPR-aware image reuse."""
import io
import os
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))


def child():
    from PIL import Image
    from PySide6.QtCore import QPoint,Qt
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication,QVBoxLayout,QWidget
    from quest_tool.messages import set_language,tr
    from quest_tool.qt_ui.cover_browser import CoverCache
    from quest_tool.qt_ui.library_detail import DetailPanel
    from quest_tool.qt_ui.theme import apply_theme
    app=QApplication([]);app.setStyle('Fusion');errors=[]
    def report(kind,value,tb):
        errors.append(str(value));sys.__excepthook__(kind,value,tb)
    sys.excepthook=report
    buffer=io.BytesIO();Image.new('RGB',(256,256),'red').save(buffer,format='PNG');red=buffer.getvalue()
    buffer=io.BytesIO();Image.new('RGB',(256,256),'blue').save(buffer,format='PNG');blue=buffer.getvalue()
    puts=[];original=CoverCache.put
    def put(self,key,data):
        puts.append((key,data));return original(self,key,data)
    CoverCache.put=put
    for language in ('zh','en'):
        set_language(language)
        for mode in ('light','dark'):
            apply_theme(app,mode);window=QWidget();layout=QVBoxLayout(window)
            panel=DetailPanel(window);layout.addWidget(panel.page);window.resize(320,720);window.show()
            assert QTest.qWaitForWindowExposed(window,5000)
            try:
                assert panel.subtitle.isHidden() and panel.author.isHidden()
                count=len(puts)
                title='<b>Literal 中文 title</b> '*12
                panel.key=('local','fixture','folder')
                panel.data=dict(title=title,subtitle='',author='Studio Demo',folder='a_very_long_folder_'*12,
                    bpm=128,duration=193,version='2.0.0',size=12345,difficulties=('ExpertPlus '*30,),image=red,error='')
                panel.render();QTest.qWait(50)
                assert len(puts)==count+1
                assert panel.title.text()==title and panel.title.textFormat()==Qt.TextFormat.PlainText
                assert panel.subtitle.isHidden() and not panel.author.isHidden()
                assert panel.fields['duration'].text()=='3:13'
                assert panel.state.property('status')=='normal'
                cache=panel.cover.cache;misses=cache.misses;key=panel.cover.pixmap().cacheKey()
                for _ in range(20):panel.render()
                assert panel.cover.cache is cache and cache.misses==misses
                assert panel.cover.pixmap().cacheKey()==key and len(puts)==count+1
                for pixels in (13,20):
                    window.setStyleSheet(f'QLabel {{ font-size: {pixels}px; }}')
                    QTest.qWait(20)
                    for width in (200,320,420):
                        window.resize(width,720);QTest.qWait(60)
                        assert window.width()==width,(language,width,window.width())
                        assert panel.scroll.horizontalScrollBar().maximum()==0,(language,pixels,width)
                        image=panel.cover.pixmap();ratio=panel.cover.devicePixelRatioF()
                        assert image is not None and not image.isNull()
                        assert abs(image.devicePixelRatioF()-ratio)<.01
                        assert image.width()/ratio<=panel.cover.contentsRect().width()+1
                        assert image.height()/ratio<=panel.cover.height()+1
                        assert image.toImage().pixelColor(0,0).red()>200
                        for value in panel.fields.values():
                            point=value.mapTo(panel.metadata,QPoint())
                            assert point.x()>=0 and point.x()+value.width()<=panel.metadata.width()+1,(language,pixels,width,point,value.size(),panel.metadata.size())
                        panel.scroll.ensureWidgetVisible(panel.fields['difficulties']);QTest.qWait(20)
                        assert panel.fields['difficulties'].visibleRegion().boundingRect().height()>0
                assert len(puts)==count+1,'Resize decoded source PNG again'
                panel.data['image']=blue;panel.render();assert len(puts)==count+2
                assert panel.cover.pixmap().toImage().pixelColor(0,0).blue()>200
                panel.failed=True;panel.render();assert panel.state.property('status')=='error' and panel.retry.isVisible()
                panel.failed=False;panel.data['error']='partial';panel.render()
                assert panel.state.property('status')=='warning' and not panel.retry.isVisible()
                panel.data['image']=b'broken';panel.render()
                assert not panel.cover.ready and panel.cover.text()==tr('media.no_cover')
                panel.data['image']=None;panel.render();assert not panel.cover.cache.sources
                panel.data.update(title='Blue Horizon',folder='demo',difficulties=('Expert','ExpertPlus'),
                                  subtitle='Live version',image=blue,error='')
                panel.render();window.setStyleSheet('');window.resize(320,720)
                panel.scroll.verticalScrollBar().setValue(0);QTest.qWait(80)
                assert not panel.subtitle.isHidden()
                if os.environ.get('QUEST_QT_SHOTS'):
                    output=Path(os.environ['QUEST_QT_SHOTS']);output.mkdir(parents=True,exist_ok=True)
                    assert window.grab().save(str(output/f'detail-design-{language}-{mode}-{os.environ.get("QT_SCALE_FACTOR")}.png'))
                panel.clear();assert not panel.cover.ready and panel.subtitle.isHidden()
            finally:
                panel.close();panel.wait_closed();window.close()
    assert not errors,errors
    print('PASS details: bilingual/themes, long plain text, narrow/large font scrolling, DPR images, decode reuse, empty/error states')


if __name__=='__main__':
    if '--child' in sys.argv:child()
    else:
        for scale in ('1','1.25','1.5'):
            result=subprocess.run([sys.executable,'-B',__file__,'--child'],cwd=ROOT,
                env=dict(os.environ,QT_SCALE_FACTOR=scale),capture_output=True,timeout=150)
            print(scale,result.stdout.decode('utf-8','replace'),result.stderr.decode('utf-8','replace'))
            assert result.returncode==0
