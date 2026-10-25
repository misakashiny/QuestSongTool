"""No audio/files/network: exercise real card painting, geometry and mouse checks."""
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT))


def child():
    from PySide6.QtCore import QRect,Qt
    from PySide6.QtGui import QFont,QPainter,QPixmap
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication,QComboBox,QStyleOptionViewItem,QWidget
    from quest_tool.messages import set_language,tr
    from quest_tool.qt_ui.cover_browser import CoverCache
    from quest_tool.qt_ui.library_covers import LibraryGrid
    from quest_tool.qt_ui.library_window import LibraryModel
    from quest_tool.qt_ui.theme import apply_theme

    class Recorder(QPainter):
        def __init__(self,image):
            super().__init__(image);self.drawn=[]

        def drawText(self,rect,flags,text):
            self.drawn.append((QRect(rect),text,self.font().bold()))
            return super().drawText(rect,flags,text)

    app=QApplication([]);app.setStyle('Fusion');errors=[]
    def report(kind,value,tb):
        errors.append(str(value));sys.__excepthook__(kind,value,tb)
    sys.excepthook=report
    for language in ('zh','en'):
        set_language(language)
        for mode in ('light','dark'):
            selector=QComboBox();selector.addItem(mode,mode)
            context=SimpleNamespace(mode=selector,covers=SimpleNamespace(cache=CoverCache(),schedule=lambda:None))
            view=LibraryGrid(context);apply_theme(view,mode)
            model=LibraryModel(view)
            model.replace([dict(folder='known',title='A long title 中文 '*10,author='Artist '*10,duration=193,error=''),
                           dict(folder='unknown',title='Unknown duration',author='',duration=0,error=''),
                           dict(folder='long',title='Long duration',author='Artist',duration=360000,error='')])
            view.setModel(model);view.resize(420,720);view.show()
            assert QTest.qWaitForWindowExposed(view,5000)
            try:
                children=len(view.findChildren(QWidget));stacked_seen=False
                for pixels in (13,24):
                    view.setStyleSheet(view.styleSheet()+f'\nQListView {{ font-size: {pixels}px; }}')
                    QTest.qWait(20);font=QFont(view.font())
                    assert font.pixelSize()==pixels,(font.pixelSize(),pixels)
                    for width in (160,240,420):
                        view.resize(width,720);QTest.qWait(30)
                        size=view.cell_size();delegate=view.itemDelegate()
                        option=QStyleOptionViewItem();option.initFrom(view);option.rect=QRect(0,0,size.width(),size.height())
                        rect=option.rect.adjusted(4,4,-4,-4)
                        art,title,author,rating,duration=delegate.content_rects(rect)
                        for area in (art,title,author,rating,duration):
                            assert rect.contains(area),(language,pixels,width,rect,area)
                        assert not rating.intersects(duration),(rating,duration)
                        assert title.bottom()<author.top() and author.bottom()<rating.top()
                        stacked_seen|=duration.top()>rating.top()
                        for row,expected in ((0,'3:13'),(1,tr('search.unknown'))):
                            image=QPixmap(size);image.fill(Qt.GlobalColor.transparent)
                            painter=Recorder(image);painter.setFont(font)
                            delegate.paint(painter,option,model.index(row,0));painter.end()
                            assert any(text==expected and area==duration and not bold for area,text,bold in painter.drawn),painter.drawn
                            assert any(bold and area==title for area,text,bold in painter.drawn)
                            for area,text,_bold in painter.drawn:assert option.rect.contains(area),(area,text)
                        # Real view events must still toggle the folder identity.
                        index=model.index(0,0);view.scrollToTop();QTest.qWait(20)
                        hit=delegate.check_rect(view.visualRect(index).adjusted(4,4,-4,-4)).center()
                        for expected in (True,False):
                            QTest.mouseClick(view.viewport(),Qt.MouseButton.LeftButton,pos=hit);QTest.qWait(10)
                            assert ('known' in model.checked)==expected
                        assert len(view.findChildren(QWidget))<=children+2
                assert stacked_seen,'Large-font/narrow metadata never stacked'
                view.resize(420,720);view.setStyleSheet(view.styleSheet()+'\nQListView { font-size: 13px; }');QTest.qWait(50)
                if os.environ.get('QUEST_QT_SHOTS'):
                    output=Path(os.environ['QUEST_QT_SHOTS']);output.mkdir(parents=True,exist_ok=True)
                    assert view.grab().save(str(output/f'library-cards-{language}-{mode}-{os.environ.get("QT_SCALE_FACTOR")}.png'))
            finally:
                view.close();selector.close()
    assert not errors,errors
    assert not any(name.startswith(('tkinter','customtkinter')) for name in sys.modules)
    print('PASS card painting: bilingual/themes, durations, bold titles, large font/narrow layout, real mouse checks, bounded widgets')


if __name__=='__main__':
    if '--child' in sys.argv:child()
    else:
        for scale in ('1','1.25','1.5'):
            result=subprocess.run([sys.executable,'-B',__file__,'--child'],cwd=ROOT,
                env=dict(os.environ,QT_SCALE_FACTOR=scale),capture_output=True,timeout=90)
            print(scale,result.stdout.decode('utf-8','replace'),result.stderr.decode('utf-8','replace'))
            assert result.returncode==0
