"""Real Qt design checks: existing hit targets, popup, fixed CTA and bounded art."""
import io,json,os,subprocess,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(ROOT/'tests/qt'))
from smoke_search_browser import ScriptedClient

class DesignClient(ScriptedClient):
    def fetch(self,url,destination,cancel,progress,**kwargs):
        super().fetch(url,destination,cancel,progress,**kwargs)
        data=json.loads(destination.read_text(encoding='utf-8'))
        for number,item in enumerate(data['docs']):
            item['metadata'].update(songName=('Blue Horizon','星海回声','Electric Garden')[number%3],songAuthorName='Studio Demo',duration=193,bpm=128)
            item['versions'][0]['coverURL']='https://cdn.beatsaver.com/design-'+item['id']+'.png'
            item['description']=('长说明 Long description · '*40+'\n')*12
        destination.write_text(json.dumps(data),encoding='utf-8')

def child():
    from PIL import Image,ImageDraw
    from PySide6.QtCore import QPoint,Qt,QSize
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication
    from quest_tool.qt_ui.download_window import DownloadWindow
    from quest_tool.song_search import SearchSession
    from quest_tool.qt_ui.cover_browser import CoverCache
    from quest_tool.qt_ui.theme import arrow_image
    from quest_tool.theme import DOWNLOAD_UI,get_palette
    app=QApplication([]);app.setStyle('Fusion');errors=[]
    def record_error(kind,value,tb):
        errors.append(repr(value));sys.__excepthook__(kind,value,tb)
    sys.excepthook=record_error
    colors=('#4967b8','#a54a6f','#367e84','#be913b','#7054a4','#47736a')
    def wait(done):
        end=time.monotonic()+10
        while time.monotonic()<end:
            QTest.qWait(20)
            if done():return
        raise AssertionError('design UI wait')
    for language in ('zh','en'):
        for mode in ('light','dark'):
            window=DownloadWindow(language,mode);session=SearchSession(DesignClient());window.enable_search(session)
            window.move(app.primaryScreen().availableGeometry().topLeft());window.show()
            assert QTest.qWaitForWindowExposed(window,5000)
            try:
                wait(lambda:window.browser.generation is None)
                records=window.song_model.visible_songs();assert len(records)==48
                cache=CoverCache()
                for number,song in enumerate(records):
                    image=Image.new('RGB',(256,256),colors[number%len(colors)]);draw=ImageDraw.Draw(image)
                    draw.ellipse((30,30,225,225),outline='#e8edf6',width=3);draw.rectangle((70,70,185,185),outline='#e8edf6',width=2)
                    draw.text((76,120),'STUDIO DEMO',fill='#ffffff');data=io.BytesIO();image.save(data,'PNG');assert cache.put(song.cover_url,data.getvalue())
                window.results.online=True;window.results.cover_cache=cache
                window.cover.setPixmap(cache.image(records[0].cover_url,QSize(170,170),window.devicePixelRatioF()))
                assert window.columns.currentText()=='6' and window.rows.currentText()=='8' and window.song_model.rowCount()==48
                assert window.search.font().pixelSize()==DOWNLOAD_UI.BODY_PX
                arrow=Path(arrow_image(get_palette(mode).TEXT_SECONDARY));assert arrow.is_file() and arrow.stat().st_size>0
                assert arrow_image(get_palette(mode).TEXT_SECONDARY)==str(arrow).replace('\\','/')
                for width in (1000,1280,1600):
                    window.resize(min(width,app.primaryScreen().availableGeometry().width()),min(824,app.primaryScreen().availableGeometry().height()-40));QTest.qWait(80)
                    window.show_song(records[0]);window.more_details.setChecked(True);QTest.qWait(80)
                    assert window.download_one.parentWidget() is window.detail_panel
                    assert window.detail_panel.rect().contains(window.download_one.geometry())
                    assert window.details.horizontalScrollBar().maximum()==0 and window.details.verticalScrollBar().maximum()>0,(language,mode,width,window.details.horizontalScrollBar().maximum(),window.details.verticalScrollBar().maximum(),window.song_info.size())
                    before=window.download_one.mapTo(window,QPoint()).y();window.details.verticalScrollBar().setValue(window.details.verticalScrollBar().maximum());QTest.qWait(20)
                    assert window.download_one.mapTo(window,QPoint()).y()==before
                    assert window.footer.geometry().bottom()<window.player.geometry().top()
                    assert all(button.width()>=button.sizeHint().width() for key,button in window.categories.buttons.items() if key!="Search")
                window.resize(1280,824);QTest.qWait(50)
                window.columns.setCurrentText('3');window.columns.setFocus()
                QTest.mouseClick(window.columns,Qt.MouseButton.LeftButton,pos=QPoint(window.columns.width()-12,window.columns.height()//2));QTest.qWait(30)
                assert window.columns.view().isVisible()
                QTest.keyClick(window.columns.view(),Qt.Key.Key_Down);QTest.keyClick(window.columns.view(),Qt.Key.Key_Return);QTest.qWait(40)
                assert window.columns.currentText()=='4';window.columns.setCurrentText('3');wait(lambda:window.browser.generation is None);QTest.qWait(40)
                index=window.song_model.index(0);rect=window.results.visualRect(index).adjusted(4,4,-4,-4)
                check,play=window.results.itemDelegate().controls(rect);assert check.size()==play.size()
                QTest.mouseClick(window.results.viewport(),Qt.MouseButton.LeftButton,pos=check.center());assert records[0].key in window.song_model.checked
                window.show_song(records[0]);window.cover.setPixmap(cache.image(records[0].cover_url,QSize(170,170),window.devicePixelRatioF()));QTest.qWait(50)
                shots=os.environ.get('QUEST_QT_SHOTS')
                if shots:
                    folder=Path(shots);folder.mkdir(parents=True,exist_ok=True);window.grab().save(str(folder/f'design-{language}-{mode}-{os.environ.get("QT_SCALE_FACTOR")}.png'))
                assert cache.source_bytes<=cache.SOURCE_BYTES and cache.scaled_bytes<=cache.SCALED_BYTES
                assert not errors,errors
            finally:
                window.close()
                for thread in tuple(session.tasks._threads):thread.join()
    assert not any(n.startswith(('tkinter','customtkinter')) for n in sys.modules)
    print('PASS design: bilingual/themes/scales, native popup keyboard, fixed detail CTA, equal card targets, 48 items, cached covers, no GUI errors')

if __name__=='__main__':
    if '--child' in sys.argv:child()
    else:
        for scale in ('1','1.25','1.5'):
            p=subprocess.run([sys.executable,'-B',__file__,'--child'],cwd=ROOT,env=dict(os.environ,QT_SCALE_FACTOR=scale,PYTHONIOENCODING='utf-8'),capture_output=True,timeout=100)
            print(scale,p.stdout.decode('utf-8','replace'),p.stderr.decode('utf-8','replace'),flush=True)
            assert p.returncode==0
