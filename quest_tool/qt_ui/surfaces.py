"""Shared native page surfaces and wrapping actions; no business side effects."""
from PySide6.QtCore import QRect, QSize, Qt
from PySide6.QtGui import QIcon, QPalette
from PySide6.QtWidgets import QApplication, QFrame, QLabel, QLayout

from ..messages import tr
from ..theme import APP_UI, SPACE, get_palette
from .icons import line_icon


def action_icons(*pairs):
    """Register explicit icon semantics once, without deriving actions from text."""
    app = QApplication.instance()
    if app is None:return
    mode = 'dark' if app.palette().color(QPalette.ColorRole.Window).lightness() < 128 else 'light'
    p = get_palette(mode)
    for button, kind in pairs:
        button.setProperty('uiIcon', kind)
        color = p.ACCENT_TEXT if button.property('role') in ('primary','playerAction') else p.DANGER if button.property('role') == 'danger' else p.TEXT_PRIMARY
        icon = QIcon(line_icon(kind,color))
        icon.addPixmap(line_icon(kind,p.TEXT_MUTED).pixmap(24,24),QIcon.Mode.Disabled)
        button.setIcon(icon);button.setIconSize(QSize(20,20))


def refresh_action_icons(app):
    pairs = [(widget,widget.property('uiIcon')) for widget in app.allWidgets() if widget.property('uiIcon')]
    action_icons(*pairs)


def surface(widget, kind='card'):
    widget.setProperty('uiSurface', kind)
    widget.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
    return widget


def heading(key):
    label = QLabel(tr(key))
    label.setProperty('uiRole', 'heading')
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setWordWrap(True)
    return label


def style_table(table):
    table.setProperty('uiTable', True)
    table.setAlternatingRowColors(True)
    table.setFrameShape(QFrame.Shape.NoFrame)
    # QTreeWidget uses its own header/row sizing and must keep those semantics.
    if hasattr(table, 'verticalHeader'):
        table.setShowGrid(False)
        table.verticalHeader().hide()
        table.verticalHeader().setDefaultSectionSize(APP_UI.ROW_H)
        table.horizontalHeader().setMinimumHeight(APP_UI.HEADER_H)


class WrapLayout(QLayout):
    """Lay out existing actions at native size, wrapping without rebuilding them."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.items = []
        super().setContentsMargins(SPACE.M, SPACE.S, SPACE.M, SPACE.S)
        super().setSpacing(SPACE.M)

    def addItem(self, item):
        self.items.append(item)

    def count(self):
        return len(self.items)

    def itemAt(self, index):
        return self.items[index] if 0 <= index < len(self.items) else None

    def takeAt(self, index):
        return self.items.pop(index) if 0 <= index < len(self.items) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self.arrange(QRect(0, 0, width, 0), True)

    def minimumSize(self):
        size = QSize()
        for item in self.items:
            if not item.isEmpty():size = size.expandedTo(item.minimumSize())
        margins = super().contentsMargins()
        return size + QSize(margins.left()+margins.right(), margins.top()+margins.bottom())

    def sizeHint(self):
        return self.minimumSize()

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self.arrange(rect, False)

    def arrange(self, rect, measure):
        margins = super().contentsMargins()
        area = rect.adjusted(margins.left(), margins.top(), -margins.right(), -margins.bottom())
        x, y, height = area.x(), area.y(), 0
        for item in self.items:
            if item.isEmpty():continue
            size = item.sizeHint().expandedTo(item.minimumSize())
            if x > area.x() and x+size.width() > area.right()+1:
                x = area.x();y += height+super().spacing();height = 0
            if not measure:item.setGeometry(QRect(x, y, size.width(), size.height()))
            x += size.width()+super().spacing();height = max(height, size.height())
        return y-rect.y()+height+margins.bottom()


def action_strip(*widgets):
    frame = surface(QFrame(), 'toolbar')
    layout = WrapLayout(frame)
    for widget in widgets:layout.addWidget(widget)
    return frame
