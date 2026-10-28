"""Compact library header and one-line labels; no device or transfer work."""
from PySide6.QtCore import QEvent, QSize, QTimer, Qt
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QLayout, QSizePolicy, QVBoxLayout, QWidget

from .surfaces import surface


class InlineLabel(QLabel):
    """Keep full text for accessibility/tests, elide only its painted line."""
    def __init__(self, parent=None):
        super().__init__(parent)
        super().setMinimumWidth(0);super().setWordWrap(False)
        super().setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Preferred)

    def setText(self, text):
        super().setText(text)
        super().setAccessibleName(text);super().setToolTip(text)

    def minimumSizeHint(self):
        return QSize(0,super().sizeHint().height())

    def paintEvent(self, event):
        painter=QPainter(self)
        painter.setPen(self.palette().windowText().color())
        text=super().fontMetrics().elidedText(self.text().replace('\n',' · '),Qt.TextElideMode.ElideRight,super().contentsRect().width())
        painter.drawText(super().contentsRect(),int(super().alignment() | Qt.AlignmentFlag.AlignVCenter),text)


class LibraryHeader(QFrame):
    def __init__(self, parent):
        super().__init__(parent);surface(self,'toolbar');super().setObjectName('libraryHeader')
        self.column=QVBoxLayout(self);self.column.setContentsMargins(8,5,8,5);self.column.setSpacing(8)
        self.column.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        self.top=QHBoxLayout();self.top.setSpacing(12);self.column.addLayout(self.top)
        self.identity=QWidget();self.identity_layout=QHBoxLayout(self.identity)
        self.identity_layout.setContentsMargins(0,0,0,0);self.identity_layout.setSpacing(12)
        self.top.addWidget(self.identity,1)
        self.actions=QWidget();self.action_layout=QHBoxLayout(self.actions)
        self.action_layout.setContentsMargins(0,0,0,0);self.action_layout.setSpacing(8)
        self.top.addWidget(self.actions)
        self.wrapped=False;self.pending=False;self.controls=None;self.closed=False;self.prefer_action_width=False
        self.timer=QTimer(self);self.timer.setSingleShot(True);self.timer.timeout.connect(self.arrange)
        self.actions.installEventFilter(self)

    def add_controls(self, controls):
        self.controls=controls;self.top.addWidget(controls)
        self.schedule()

    def minimumSizeHint(self):
        controls=self.controls.sizeHint().width() if self.controls else 0
        width=max(self.actions.minimumSizeHint().width()+16,self.identity.minimumSizeHint().width()+controls+28)
        return QSize(width,self.column.minimumSize().height())

    def schedule(self):
        if not self.closed and not self.pending:
            self.pending=True;self.timer.start(0)

    def arrange(self):
        self.pending=False
        if self.closed:return
        # Move an existing toolbar only when crossing its fit boundary. Do not
        # rebuild widgets or relayout an open combo on every resize event.
        controls=self.controls.sizeHint().width() if self.controls else 0
        actions=self.actions.sizeHint().width() if self.prefer_action_width else self.actions.minimumSizeHint().width()
        needed=self.identity.minimumSizeHint().width()+actions+controls+48
        wrap=self.width()<needed
        if wrap==self.wrapped:return
        self.wrapped=wrap
        if wrap:
            self.top.removeWidget(self.actions);self.column.addWidget(self.actions)
        else:
            self.column.removeWidget(self.actions);self.top.insertWidget(1,self.actions)
        self.actions.show()

    def resizeEvent(self, event):
        super().resizeEvent(event);self.schedule()

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Type.LayoutRequest,QEvent.Type.FontChange,QEvent.Type.StyleChange):self.schedule()
        return False

    def shutdown(self):
        self.closed=True;self.timer.stop();self.pending=False
