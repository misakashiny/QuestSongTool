"""Qt rendering adapter for the shared semantic theme; no Tk imports."""
from pathlib import Path
from PySide6.QtCore import QDir, QTemporaryDir
from PySide6.QtGui import QColor, QFont, QPalette

from ..theme import DESKTOP, DOWNLOAD, FONT, FORM, LIGHT, DARK, RADIUS, SIZE, SPACE
from .icons import line_icon


_style_assets = None


def arrow_image(color):
    """QSS needs an image URL: render the existing cached icon once per color."""
    return style_image('chevron',color)


def style_image(kind,color):
    """Cache small QSS assets for this process, outside project/user data."""
    global _style_assets
    if _style_assets is None:_style_assets = QTemporaryDir(QDir.tempPath()+"/quest-ui-XXXXXX")
    path = Path(_style_assets.filePath(kind+"-"+color.lstrip('#')+".png"))
    if not path.is_file():line_icon(kind,color).pixmap(24,24).save(str(path))
    return path.as_posix()


def apply_theme(app, mode="light"):
    p = DARK if mode == "dark" else LIGHT
    palette = QPalette()
    roles = {
        QPalette.ColorRole.Window: p.BG_APP,
        QPalette.ColorRole.WindowText: p.TEXT_PRIMARY,
        QPalette.ColorRole.Base: p.BG_CARD,
        QPalette.ColorRole.AlternateBase: p.BG_ZEBRA,
        QPalette.ColorRole.Text: p.TEXT_PRIMARY,
        QPalette.ColorRole.Button: p.BG_CARD,
        QPalette.ColorRole.ButtonText: p.TEXT_PRIMARY,
        QPalette.ColorRole.Highlight: p.ACCENT,
        QPalette.ColorRole.HighlightedText: p.ACCENT_TEXT,
        QPalette.ColorRole.ToolTipBase: p.BG_ELEVATED,
        QPalette.ColorRole.ToolTipText: p.TEXT_PRIMARY,
    }
    for role, value in roles.items():
        palette.setColor(role, QColor(value))
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText,
                 QPalette.ColorRole.WindowText):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor(p.TEXT_MUTED))
    app.setPalette(palette)
    app.setFont(QFont(FONT.FAMILY, FONT.SMALL))
    arrow = arrow_image(p.TEXT_SECONDARY)
    app.setStyleSheet(f"""
        QWidget {{ color: {p.TEXT_PRIMARY}; font-size: {DESKTOP.BODY_PX}px; }}
        QMainWindow, QScrollArea, QWidget#page {{ background: {p.BG_APP}; }}
        QFrame#card {{ background: {p.BG_CARD}; border: 1px solid {p.BORDER};
                       border-radius: {DESKTOP.PANEL_RADIUS}px; }}
        QLabel {{ background: transparent; }}
        QWidget#detailContent, QWidget#detailViewport, QScrollArea#detailScroll {{ background: {p.BG_CARD}; }}
        QLabel[role="title"] {{ font-size: {DESKTOP.TITLE_PX}px; font-weight: 600; }}
        QLabel[role="subtitle"] {{ font-size: {FONT.TITLE}px; font-weight: 600; }}
        QLabel[role="muted"] {{ color: {p.TEXT_SECONDARY}; }}
        QLabel#libraryDetailCover {{ background: {p.BG_INPUT}; border-radius: {RADIUS.CONTROL}px; }}
        QFrame#libraryDetailMetadata {{ background: {p.BG_CARD}; border: 1px solid {p.BORDER};
                                     border-radius: {RADIUS.CONTROL}px; }}
        QLabel#libraryDetailState[status="error"] {{ color: {p.DANGER}; }}
        QLabel#libraryDetailState[status="warning"] {{ color: {p.WARNING_TEXT}; }}
        QPushButton, QLineEdit, QComboBox {{ min-height: {SIZE.BUTTON_H}px;
            border: 1px solid {p.BORDER_STRONG}; border-radius: {RADIUS.CONTROL}px;
            padding: 0 {DESKTOP.CONTROL_PADDING}px; background: {p.BG_CARD}; }}
        QLineEdit {{ selection-background-color: {p.ACCENT}; selection-color: {p.ACCENT_TEXT}; }}
        QComboBox {{ padding-right: {DESKTOP.COMBO_ARROW_W + SPACE.M}px; }}
        QComboBox::drop-down {{ subcontrol-origin: padding; subcontrol-position: top right;
            width: {DESKTOP.COMBO_ARROW_W}px; border: none; background: transparent; }}
        QComboBox::down-arrow {{ image: url("{arrow}"); width: 12px; height: 12px; }}
        QPushButton:hover, QComboBox:hover {{ background: {p.BG_HOVER}; }}
        QPushButton:pressed {{ background: {p.BG_SELECTED}; }}
        QPushButton[role="primary"] {{ background: {p.ACCENT}; color: {p.ACCENT_TEXT};
                                       border-color: {p.ACCENT}; }}
        QPushButton[role="primary"]:hover {{ background: {p.ACCENT_HOVER}; }}
        QPushButton[role="danger"] {{ color: {p.DANGER}; border-color: {p.DANGER}; }}
        QPushButton[role="danger"]:hover {{ background: {p.DANGER_SOFT}; }}
        QPushButton:focus, QLineEdit:focus, QComboBox:focus {{ border-color: {p.ACCENT}; }}
        QPushButton:focus {{ background: {p.ACCENT_SOFT}; }}
        QPushButton[role="primary"]:focus {{ background: {p.ACCENT_HOVER}; color: {p.ACCENT_TEXT}; }}
        QCheckBox {{ spacing: {SPACE.M}px; padding: {SPACE.XS}px; }}
        QCheckBox:hover {{ background: {p.BG_HOVER}; border-radius: {RADIUS.CHIP}px; }}
        QCheckBox:focus {{ outline: 2px solid {p.ACCENT}; }}
        QPushButton:disabled, QLineEdit:disabled, QComboBox:disabled {{
            color: {p.TEXT_MUTED}; background: {p.BG_INPUT}; border-color: {p.BORDER}; }}
        QComboBox QAbstractItemView {{ background: {p.BG_ELEVATED};
            color: {p.TEXT_PRIMARY}; selection-background-color: {p.ACCENT};
            selection-color: {p.ACCENT_TEXT}; padding: {SPACE.XS}px; }}
        QStatusBar {{ background: {p.BG_CARD}; color: {p.TEXT_SECONDARY}; }}
        QStatusBar::item {{ border: none; }}
        QListView#songResults {{ background: {p.BG_APP}; border: none; outline: none; }}
        QScrollArea {{ border: none; }}
        QScrollBar:vertical {{ background: {p.BG_APP}; width: {DESKTOP.SCROLLBAR_W}px; margin: 0; }}
        QScrollBar::handle:vertical {{ background: {p.BORDER_STRONG};
            min-height: {DESKTOP.SCROLLBAR_MIN}px; border-radius: {DESKTOP.SCROLLBAR_W//2}px; }}
        QScrollBar::handle:vertical:hover {{ background: {p.TEXT_MUTED}; }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; border: none; }}
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}
        QFrame#downloadPanel {{ background: {p.BG_CARD}; border: 1px solid {p.BORDER};
                                border-radius: {RADIUS.CONTROL}px; }}
        QLabel#coverPlaceholder {{ background: {p.BG_INPUT}; color: {p.TEXT_MUTED};
                                   border-radius: {RADIUS.CONTROL}px; }}
        QPushButton[role="category"], QPushButton[role="segment"] {{
            min-height: {DOWNLOAD.CONTROL - 4}px; padding: 0 {SPACE.M}px;
            font-size: {FONT.BODY}px; border-color: {p.BORDER}; }}
        QPushButton[role="category"]:checked, QPushButton[role="segment"]:checked {{
            color: {p.ACCENT_TEXT}; background: {p.ACCENT}; border-color: {p.ACCENT}; }}
        QPushButton[role="category"] {{ background: transparent; border-color: transparent;
            border-radius: {DESKTOP.CATEGORY_RADIUS}px; }}
        QPushButton[role="category"]:hover {{ background: {p.BG_HOVER}; }}
        QPushButton[role="category"]:checked {{ background: {p.ACCENT}; border-color: {p.ACCENT}; }}
        QPushButton[role="favorite"]:checked {{ background: {p.ACCENT_SOFT}; color: {p.ACCENT}; border-color: {p.ACCENT}; }}
        QTabBar::tab {{ background: {p.BG_CARD}; color: {p.TEXT_SECONDARY};
            min-height: {DESKTOP.TAB_H}px; padding: 0 {SPACE.L}px;
            border: none; border-bottom: 2px solid {p.BORDER}; }}
        QTabBar::tab:hover {{ background: {p.BG_HOVER}; }}
        QTabBar::tab:selected {{ color: {p.ACCENT}; background: {p.BG_CARD};
                               border-bottom: 2px solid {p.ACCENT}; }}
        QTabBar::tab:!selected:hover {{ color: {p.ACCENT}; background: {p.ACCENT_SOFT}; }}
        QToolButton {{ background: {p.BG_CARD}; border: 1px solid {p.BORDER};
                       border-radius: {RADIUS.CONTROL}px; }}
        QToolButton:hover {{ background: {p.BG_HOVER}; }}
        QToolButton:focus {{ border: 2px solid {p.ACCENT}; }}
        QToolButton::menu-indicator {{ image: none; }}
        QMenu {{ background: {p.BG_ELEVATED}; color: {p.TEXT_PRIMARY};
                 border: 1px solid {p.BORDER}; padding: {SPACE.XS}px; }}
        QMenu::item {{ padding: {SPACE.S}px {SPACE.XL}px; }}
        QMenu::item:selected {{ background: {p.BG_SELECTED}; }}
        QSlider::groove:horizontal {{ height: 4px; background: {p.BORDER}; border-radius: 2px; }}
        QSlider::sub-page:horizontal {{ background: {p.ACCENT}; border-radius: 2px; }}
        QSlider::handle:horizontal {{ background: {p.ACCENT}; width: 12px; margin: -4px 0;
                                    border-radius: 6px; }}
        QSlider::handle:horizontal:disabled {{ background: {p.BORDER_STRONG}; }}
        QSplitter::handle {{ background: {p.BG_APP}; }}
        QTableView[libraryTable="true"] {{ background: {p.BG_CARD};
            alternate-background-color: {p.BG_ZEBRA}; border: none;
            selection-background-color: {p.BG_SELECTED}; selection-color: {p.TEXT_PRIMARY}; }}
        QTableView[libraryTable="true"]::item {{ padding: {SPACE.XS}px; border: none; }}
        QTableView[libraryTable="true"]::item:hover {{ background: {p.BG_HOVER}; }}
        QTableView[libraryTable="true"]::item:selected {{ background: {p.BG_SELECTED}; color: {p.TEXT_PRIMARY}; }}
        QTableView[libraryTable="true"] QHeaderView::section {{ background: {p.BG_INPUT};
            color: {p.TEXT_SECONDARY}; padding: {SPACE.XS}px; border: none;
            border-bottom: 1px solid {p.BORDER}; }}
        QTableView[libraryTable="true"]::indicator {{ width: {FORM.CHECK_SIZE}px; height: {FORM.CHECK_SIZE}px;
            border: 1px solid {p.BORDER_STRONG}; border-radius: {RADIUS.CHIP}px; background: {p.BG_CARD}; }}
        QTableView[libraryTable="true"]::indicator:hover {{ border-color: {p.ACCENT}; }}
        QTableView[libraryTable="true"]::indicator:checked {{ border-color: {p.ACCENT};
            background: {p.ACCENT}; image: url("{style_image('check',p.ACCENT_TEXT)}"); }}
        QPushButton[role="play"] {{ border-radius: {RADIUS.PANEL + SPACE.M}px; }}
        QPushButton[role="playerAction"] {{ min-height: {DESKTOP.PLAYER_ART-2}px;
            padding: 0; background: {p.ACCENT}; border-color: {p.ACCENT};
            border-radius: {DESKTOP.PLAYER_ART//2}px; }}
        QPushButton[role="playerAction"]:hover, QPushButton[role="playerAction"]:focus {{ background: {p.ACCENT_HOVER}; }}
        QPushButton[role="playerAction"]:disabled {{ background: {p.BG_INPUT}; border-color: {p.BORDER}; }}
        QDialog#filterDialog {{ background: {p.BG_APP}; }}
        QLabel#deviceState[status="partial"] {{ background: {p.WARNING_SOFT}; color: {p.WARNING_TEXT};
            padding: {SPACE.S}px; border-radius: {RADIUS.CHIP}px; }}
        QWidget#filterContent {{ background: {p.BG_CARD}; }}
        QDialog#filterDialog QTabWidget::pane, QFrame#filterFooter {{
            background: {p.BG_CARD}; border: 1px solid {p.BORDER};
            border-radius: {RADIUS.CONTROL}px; }}
        QDialog#filterDialog QLabel[role="error"] {{ color: {p.DANGER}; }}
        QDialog#filterDialog QCheckBox::indicator {{
            width: {FORM.CHECK_SIZE}px; height: {FORM.CHECK_SIZE}px; }}
    """)
