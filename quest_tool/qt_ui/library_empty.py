"""Read-only viewport hints; model state and all input stay with the existing views."""
from PySide6.QtCore import QEvent, QObject, QRect, Qt, QTimer
from PySide6.QtWidgets import QLabel

from ..messages import tr
from ..theme import SPACE


class LibraryEmptyStates(QObject):
    def __init__(self,window):
        super().__init__(window)
        self.window = window;self.closed = False
        self.views = ((window.table,'local'),(window.grid,'local'),
                      (window.quest_table,'quest'),(window.missing_table,'missing'))
        self.labels = {}
        self.timer = QTimer(self);self.timer.setSingleShot(True);self.timer.setInterval(0)
        self.timer.timeout.connect(self.refresh)
        for view,_key in self.views:
            label = QLabel(view.viewport());label.setObjectName('libraryEmptyState')
            label.setProperty('role','muted');label.setTextFormat(Qt.TextFormat.PlainText)
            label.setWordWrap(True);label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            label.setFocusPolicy(Qt.FocusPolicy.NoFocus);label.hide()
            self.labels[view] = label
            view.viewport().installEventFilter(self)
        for proxy in (window.proxy,window.quest_proxy,window.missing_proxy):
            for signal in (proxy.modelReset,proxy.rowsInserted,proxy.rowsRemoved,proxy.layoutChanged):signal.connect(self.schedule)

    def schedule(self,*_args):
        if not self.closed:self.timer.start()

    def state(self,key):
        window = self.window
        if key=='local':
            if window.scanning:return 'qt.local.loading',''
            if window.failed:return 'qt.empty.failed','qt.empty.failed_hint'
            if not window.local_ready:return 'qt.empty.local','qt.empty.local_hint'
            model = window.model
        elif key=='quest':
            if not window.quest_ready:return 'qt.empty.quest','qt.empty.quest_hint'
            model = window.quest_model
        else:
            if not (window.local_ready and window.quest_ready):return 'qt.empty.both','qt.empty.both_hint'
            if window.quest_incomplete:return 'qt.empty.partial','qt.empty.partial_hint'
            model = window.missing_model
            if not model.rowCount():return 'qt.empty.complete','qt.empty.complete_hint'
        return ('qt.empty.none','qt.empty.none_hint') if not model.rowCount() else ('qt.empty.filtered','qt.empty.filtered_hint')

    def refresh(self):
        if self.closed or self.window.closed:return
        for view,key in self.views:
            label = self.labels[view]
            visible = view.model().rowCount()==0
            if visible:
                title_key,hint_key = self.state(key)
                title = tr(title_key)
                hint = tr(hint_key,browse=tr('dl.browse'),refresh=tr('qt.local.refresh')) if hint_key else ''
                rect = view.viewport().rect().adjusted(SPACE.M,SPACE.M,-SPACE.M,-SPACE.M)
                full = title+('\n\n'+hint if hint else '')
                height = label.fontMetrics().boundingRect(QRect(0,0,max(1,rect.width()),10000),int(Qt.TextFlag.TextWordWrap),full).height()
                text = full if height<=rect.height() else title
                if label.text()!=text:label.setText(text)
                label.setProperty('state',title_key);label.setAccessibleName(full)
                if label.geometry()!=rect:label.setGeometry(rect)
                label.raise_()
            label.setVisible(visible)

    def eventFilter(self,watched,event):
        if event.type() in (QEvent.Type.Resize,QEvent.Type.FontChange,QEvent.Type.StyleChange):self.schedule()
        return False

    def close(self):
        self.closed = True;self.timer.stop()
        for view,_key in self.views:view.viewport().removeEventFilter(self)
