"""Shared transport widgets and absolute, single-commit seek gestures."""
from PySide6.QtCore import QTimer, Qt, Signal, QSignalBlocker
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QSlider, QStyle, QStyleOptionSlider, QToolButton, QVBoxLayout, QWidget

from ..theme import PLAYLIST_UI
from .playlist_widgets import RoundedCover
from .surfaces import action_icons


class SeekSlider(QSlider):
    seekRequested=Signal(int)

    def __init__(self):
        super().__init__(Qt.Orientation.Horizontal)
        self.queued=False;self.pending_value=0
        self.commit_timer=QTimer(self);self.commit_timer.setSingleShot(True);self.commit_timer.timeout.connect(self.commit)
        super().setRange(0,1000);super().setSingleStep(10);super().setPageStep(100)
        self.sliderReleased.connect(self.released)
        self.actionTriggered.connect(self.action)

    def user_seeking(self):
        return super().isSliderDown() or self.queued

    def released(self):
        self.commit_timer.stop();self.queued=False;self.seekRequested.emit(super().sliderPosition())

    def action(self, _action):
        if super().isSliderDown():return
        self.pending_value=super().sliderPosition();self.queued=True;self.commit_timer.start(0)

    def commit(self):
        self.queued=False;self.seekRequested.emit(self.pending_value)

    def at(self, x):
        option=QStyleOptionSlider();super().initStyleOption(option)
        handle=super().style().subControlRect(QStyle.ComplexControl.CC_Slider,option,QStyle.SubControl.SC_SliderHandle,self)
        groove=super().style().subControlRect(QStyle.ComplexControl.CC_Slider,option,QStyle.SubControl.SC_SliderGroove,self)
        start=groove.left()+handle.width()//2;span=max(1,groove.width()-handle.width())
        return QStyle.sliderValueFromPosition(super().minimum(),super().maximum(),round(x)-start,span,option.upsideDown)

    def mousePressEvent(self, event):
        if event.button()==Qt.MouseButton.LeftButton and super().isEnabled():
            super().setFocus(Qt.FocusReason.MouseFocusReason)
            super().setSliderDown(True);super().setSliderPosition(self.at(event.position().x()));event.accept();return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if super().isSliderDown():super().setSliderPosition(self.at(event.position().x()));event.accept();return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button()==Qt.MouseButton.LeftButton and super().isSliderDown():
            super().setSliderPosition(self.at(event.position().x()));super().setSliderDown(False);event.accept();return
        super().mouseReleaseEvent(event)


class PlayerBar(QFrame):
    resized=Signal()

    def __init__(self):
        super().__init__();row=QHBoxLayout(self);row.setContentsMargins(12,8,12,8);row.setSpacing(10)
        self.cover=RoundedCover();self.cover.setFixedSize(PLAYLIST_UI.PLAYER_ART,PLAYLIST_UI.PLAYER_ART)
        self.cover.setObjectName('coverPlaceholder');row.addWidget(self.cover)
        self.info=QWidget();info=QVBoxLayout(self.info);info.setContentsMargins(0,0,0,0);info.setSpacing(2)
        self.title=QLabel();self.title.setTextFormat(Qt.TextFormat.PlainText);self.title.setProperty('role','playerTitle')
        self.artist=QLabel();self.artist.setTextFormat(Qt.TextFormat.PlainText);self.artist.setProperty('role','muted')
        info.addWidget(self.title);info.addWidget(self.artist);self.info.setFixedWidth(180);row.addWidget(self.info)
        self.elapsed=QLabel('0:00');self.elapsed.setFixedWidth(44);self.elapsed.setProperty('role','muted');row.addWidget(self.elapsed)
        self.seek=SeekSlider();self.seek.setMinimumWidth(80);self.seek.setEnabled(False);row.addWidget(self.seek,2)
        self.total=QLabel('0:00');self.total.setFixedWidth(44);self.total.setProperty('role','muted');row.addWidget(self.total)
        self.controls=QWidget();transport=QHBoxLayout(self.controls);transport.setContentsMargins(0,0,0,0);transport.setSpacing(10)
        self.shuffle=QToolButton();self.repeat=QToolButton()
        self.previous=QPushButton();self.next=QPushButton();self.play=QPushButton()
        for button in (self.shuffle,self.repeat):button.setCheckable(True);button.setProperty('role','playerUtility');button.setFixedSize(30,32)
        for button in (self.previous,self.next):button.setProperty('role','skip');button.setFixedSize(30,36);button.setEnabled(False)
        self.play.setProperty('role','playerAction');self.play.setFixedSize(PLAYLIST_UI.PLAYER_BUTTON,PLAYLIST_UI.PLAYER_BUTTON);self.play.setEnabled(False)
        for button in (self.shuffle,self.previous,self.play,self.next,self.repeat):transport.addWidget(button)
        row.addWidget(self.controls)
        self.volume_icon=QLabel();row.addWidget(self.volume_icon)
        self.volume=QSlider(Qt.Orientation.Horizontal);self.volume.setRange(0,100);self.volume.setValue(80)
        self.volume.setMinimumWidth(60);self.volume.setMaximumWidth(110);row.addWidget(self.volume)
        self.volume_value=QLabel('80%');self.volume_value.setFixedWidth(36);row.addWidget(self.volume_value)
        self.volume.valueChanged.connect(lambda value:self.volume_value.setText(f'{value}%'))
        action_icons((self.shuffle,'shuffle'),(self.previous,'previous'),(self.play,'play'),(self.next,'next'),(self.repeat,'repeat'))
        for first,second in zip((self.seek,self.shuffle,self.previous,self.play,self.next,self.repeat),(self.shuffle,self.previous,self.play,self.next,self.repeat,self.volume)):
            QWidget.setTabOrder(first,second)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        width=100 if super().width()<1150 else 180
        if self.info.width()!=width:self.info.setFixedWidth(width)
        self.resized.emit()


class PlaylistPlayer(PlayerBar):
    """A second view of the library transport, sharing ownership and seek state."""
    def __init__(self, preview):
        super().__init__();self.preview=preview;super().setObjectName('libraryPlayer');super().setProperty('uiRole','player')
        self.play.clicked.connect(preview.toggle)
        self.previous.clicked.connect(lambda:preview.adjacent(-1));self.next.clicked.connect(lambda:preview.adjacent(1))
        self.seek.seekRequested.connect(preview.commit_seek)
        self.shuffle.toggled.connect(preview.shuffle.setChecked);self.repeat.toggled.connect(preview.repeat.setChecked)
        self.volume.valueChanged.connect(preview.volume.setValue)
        preview.rendered.connect(self.sync);preview.volume.valueChanged.connect(self.sync)
        self.sync()

    def sync(self, *_):
        source=self.preview.page
        for name in ('title','artist','elapsed','total'):
            label=getattr(self,name);original=getattr(source,name)
            text=original.toolTip() if name in ('title','artist') else original.text()
            label.setText(label.fontMetrics().elidedText(text,Qt.TextElideMode.ElideRight,max(1,label.width())) if name in ('title','artist') else text)
            label.setToolTip(original.toolTip())
        image=source.cover.pixmap()
        if image and not image.isNull():self.cover.setPixmap(image)
        else:self.cover.clear()
        for name in ('play','previous','next','shuffle','repeat'):
            button=getattr(self,name);original=getattr(source,name)
            with QSignalBlocker(button):
                button.setEnabled(original.isEnabled());button.setIcon(original.icon());button.setChecked(original.isChecked())
                button.setToolTip(original.toolTip());button.setAccessibleName(original.accessibleName())
        self.seek.setEnabled(source.seek.isEnabled())
        if not self.seek.user_seeking():self.seek.setValue(source.seek.value())
        with QSignalBlocker(self.volume):self.volume.setValue(source.volume.value())
        self.volume_value.setText(f'{source.volume.value()}%');self.volume_icon.setPixmap(source.volume_icon.pixmap())

    def resizeEvent(self, event):
        super().resizeEvent(event)
        compact=self.width()<850
        for widget in (self.volume,self.volume_icon,self.volume_value,self.shuffle,self.repeat):widget.setVisible(not compact)
        self.info.setFixedWidth(90 if compact else 150)
