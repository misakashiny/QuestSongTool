"""Qt rendering adapter for the shared semantic theme; no Tk imports."""
from pathlib import Path
from PySide6.QtCore import QDir, QTemporaryDir
from PySide6.QtGui import QColor, QFont, QPalette

from ..theme import APP_UI, DESKTOP, DOWNLOAD, DOWNLOAD_UI, PAGE_UI, PLAYLIST_UI, FONT, FORM, LIGHT, DARK, RADIUS, SIZE, SPACE
from .icons import line_icon
from .surfaces import refresh_action_icons


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
    download_background = DOWNLOAD_UI.BACKGROUND_LIGHT if mode == 'light' else p.BG_APP
    download_border = DOWNLOAD_UI.BORDER_LIGHT if mode == 'light' else p.BORDER
    ink = '#102451' if mode == 'light' else p.TEXT_PRIMARY
    soft_blue = '#e7f1ff' if mode == 'light' else p.ACCENT_SOFT
    app.setStyleSheet(f"""
        QWidget {{ color: {p.TEXT_PRIMARY}; font-size: {DESKTOP.BODY_PX}px; }}
        QMainWindow, QScrollArea, QWidget#page {{ background: {p.BG_APP}; }}
        QFrame#card {{ background: {p.BG_CARD}; border: 1px solid {p.BORDER};
                       border-radius: {DESKTOP.PANEL_RADIUS}px; }}
        QFrame#card QSplitter::handle {{ background: {p.BG_APP}; width: {SPACE.M}px; }}
        QLabel#playlistDetailCover {{ background: {p.BG_INPUT}; border: 1px solid {p.BORDER};
                                      border-radius: {RADIUS.CONTROL}px; }}
        QLabel#playlistBadge {{ background: {p.ACCENT}; color: {p.ACCENT_TEXT};
                                border-radius: {RADIUS.PILL}px; padding: 0 {SPACE.M}px;
                                font-weight: 600; }}
        QLabel#playlistMeta {{ color: {p.TEXT_SECONDARY};
                               padding: {SPACE.XS}px 0; }}
        QLabel#playlistAuthorAvatar {{ background: {p.ACCENT_SOFT}; color: {p.ACCENT};
                                       border: 1px solid {p.BORDER}; border-radius: 22px; }}
        QFrame#playlistDetailActions {{ background: transparent; border: none; }}
        QFrame#playlistDetailActions QPushButton {{ min-height: 38px; padding: 0 {SPACE.L}px;
                                                    background: {p.BG_CARD}; }}
        QFrame#playlistDetailActions QPushButton:hover {{ background: {p.BG_HOVER}; }}
        QFrame#playlistDetailActions QPushButton[role="primary"] {{ background: {p.ACCENT};
                                                                      color: {p.ACCENT_TEXT}; }}
        QFrame#playlistDetailActions QPushButton[role="danger"] {{ color: {p.DANGER}; }}
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
            border: 1px solid {download_border}; border-radius: {RADIUS.CONTROL}px;
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
        QMainWindow#downloadPreview QTabBar#downloadNavigation::tab {{ min-height: 38px; padding: 0 {SPACE.L}px;
            border: 1px solid transparent; border-radius: {RADIUS.CONTROL}px; }}
        QMainWindow#downloadPreview QTabBar#downloadNavigation::tab:selected {{ color: {p.ACCENT};
            background: {p.ACCENT_SOFT}; border-color: {p.ACCENT_SOFT}; border-bottom: 1px solid {p.ACCENT_SOFT}; }}
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
        QToolButton[role="playerUtility"] {{ min-width: 30px; min-height: 30px; padding: 0;
            background: transparent; border: none; border-radius: {RADIUS.PILL}px; }}
        QToolButton[role="playerUtility"]:hover {{ background: {p.BG_HOVER}; }}
        QToolButton[role="playerUtility"]:checked {{ background: {p.ACCENT_SOFT}; }}
        QLabel[role="playerTitle"] {{ font-weight: 600; color: {p.TEXT_PRIMARY}; }}
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
        QMainWindow#downloadPreview QWidget {{ font-size: {DOWNLOAD_UI.BODY_PX}px; }}
        QMainWindow#downloadPreview QLabel[role="title"] {{ font-size: 20px; font-weight: 600; }}
        QMainWindow#downloadPreview, QMainWindow#downloadPreview QWidget#page {{ background: {download_background}; }}
        QMainWindow#downloadPreview QFrame#downloadHeader,
        QMainWindow#downloadPreview QFrame#downloadFooter,
        QMainWindow#downloadPreview QFrame#downloadPlayer {{ background: {download_background}; border: none;
            border-bottom: 1px solid {download_border}; }}
        QMainWindow#downloadPreview QFrame#downloadPlayer {{ background: {p.BG_CARD};
            border: 1px solid {p.BORDER}; border-radius: {RADIUS.PANEL}px; min-height: 64px; }}
        QMainWindow#downloadPreview QFrame#downloadBrowse,
        QMainWindow#downloadPreview QFrame#downloadDetail {{ background: {p.BG_CARD};
            border: 1px solid {download_border}; border-radius: {RADIUS.CONTROL}px; }}
        QMainWindow#downloadPreview QListView#songResults,
        QMainWindow#downloadPreview QScrollArea {{ background: {p.BG_CARD}; }}
        QMainWindow#downloadPreview QPushButton, QMainWindow#downloadPreview QComboBox,
        QMainWindow#downloadPreview QLineEdit {{ min-height: 34px; border-color: {download_border}; }}
        QMainWindow#downloadPreview QPushButton[role="primary"] {{ border-color: {p.ACCENT}; }}
        QMainWindow#downloadPreview QPushButton[role="danger"] {{ border-color: {download_border}; color: {p.DANGER}; }}
        QMainWindow#downloadPreview QPushButton[role="category"] {{ background: {p.BG_INPUT};
            border: 1px solid {download_border}; border-radius: {RADIUS.CONTROL}px; min-height: 34px; }}
        QMainWindow#downloadPreview QPushButton[role="category"]:hover {{ background: {p.BG_HOVER}; }}
        QMainWindow#downloadPreview QPushButton[role="category"]:checked {{ background: {p.ACCENT}; color: {p.ACCENT_TEXT}; border-color: {p.ACCENT}; }}
        QMainWindow#downloadPreview QTabBar#downloadNavigation::tab {{ background: {download_background}; padding: 0 {SPACE.XL}px;
            min-height: 42px; font-size: 15px; border-bottom-color: {download_border}; }}
        QMainWindow#downloadPreview QTabBar#downloadNavigation::tab:selected {{ color: {p.ACCENT}; font-weight: 600; border-bottom: 3px solid {p.ACCENT}; }}
        QMainWindow#downloadPreview QPushButton[role="skip"] {{ padding: 0; border: none; background: transparent; }}
        QMainWindow#downloadPreview QPushButton[role="skip"]:hover {{ background: {p.BG_HOVER}; }}
        QMainWindow#downloadPreview QLabel#downloadSongTitle {{ font-size: 22px; font-weight: 600; }}
        QMainWindow#downloadPreview QFrame#downloadMetadata {{ border-top: 1px solid {download_border}; border-bottom: 1px solid {download_border}; }}
        QMainWindow#downloadPreview QLabel#difficultyTags {{ color: {p.ACCENT}; background: {p.ACCENT_SOFT}; padding: {SPACE.M}px; border-radius: {RADIUS.CHIP}px; }}
        QMainWindow#downloadPreview QLabel[role="status"] {{ background: {p.BG_INPUT}; color: {p.TEXT_SECONDARY}; padding: {SPACE.S}px; border-radius: {RADIUS.CHIP}px; }}
        QMainWindow#downloadPreview QLabel#currentDownloadPage {{ background: {p.ACCENT}; color: {p.ACCENT_TEXT};
            padding: {SPACE.S}px {SPACE.M}px; border-radius: {RADIUS.CHIP}px; }}
        QMainWindow#downloadPreview QLabel#previewLength {{ border: 1px solid {download_border}; border-radius: {RADIUS.CONTROL}px; padding: {SPACE.M}px; }}
        QMainWindow#downloadPreview QLabel#downloadTaskBadge {{ color: {p.ACCENT_TEXT}; background: {p.DANGER};
            border-radius: 10px; min-width: 20px; max-height: 20px; qproperty-alignment: AlignCenter; }}
        QMainWindow#libraryWindow, QMainWindow#libraryWindow QWidget#page,
        QDialog {{ background: {PAGE_UI.BACKGROUND_LIGHT if mode == 'light' else p.BG_APP}; }}
        QWidget[uiSurface="card"] {{ background: {p.BG_CARD}; border: 1px solid {download_border};
            border-radius: {RADIUS.CONTROL}px; }}
        QWidget[uiSurface="toolbar"] {{ background: {p.BG_CARD}; border: none;
            border-bottom: 1px solid {download_border}; border-radius: {RADIUS.CONTROL}px; }}
        QWidget[uiSurface="footer"] {{ background: {p.BG_CARD}; border: 1px solid {download_border};
            border-radius: {RADIUS.CONTROL}px; }}
        QLabel[uiRole="heading"] {{ font-size: {PAGE_UI.HEADING_PX}px; font-weight: 600; }}
        QMainWindow#downloadPreview QLabel[uiRole="heading"] {{ font-size: {PAGE_UI.HEADING_PX}px; font-weight: 600; }}
        QLabel[uiRole="note"] {{ color: {p.TEXT_SECONDARY}; background: {p.BG_INPUT};
            padding: {SPACE.M}px; border-radius: {RADIUS.CHIP}px; }}
        QLabel[uiRole="section"] {{ color: {p.ACCENT}; font-weight: 600; }}
        QMainWindow#libraryWindow QFrame#card {{ border-color: {download_border}; }}
        QMainWindow#libraryWindow QFrame#libraryPlayer {{ background: {p.BG_CARD};
            border: none; border-top: 1px solid {download_border}; }}
        QMainWindow#libraryWindow QLabel[role="title"] {{ font-size: {PAGE_UI.HEADING_PX}px; }}
        QMainWindow#libraryWindow QLabel[role="subtitle"] {{ color: {p.ACCENT}; }}
        QMainWindow#libraryWindow QPushButton[role="inlineAction"] {{ min-height: {SPACE.XL}px;
            padding: {SPACE.XS}px {SPACE.S}px; color: {p.ACCENT}; background: transparent; border-color: transparent; }}
        QMainWindow#libraryWindow QPushButton[role="inlineAction"]:hover {{ background: {p.BG_HOVER}; }}
        QMainWindow#libraryWindow QPushButton[role="inlineAction"]:focus {{ border-color: {p.ACCENT}; }}
        QMainWindow#libraryWindow QPushButton[role="inlineAction"]:disabled {{ color: {p.TEXT_MUTED}; }}
        QMainWindow#libraryWindow QLabel#librarySongTitle {{ font-size: {PAGE_UI.HEADING_PX}px; color: {p.TEXT_PRIMARY}; }}
        QMainWindow#libraryWindow QFrame#libraryDetailMetadata {{ background: {p.BG_INPUT}; border: none; }}
        QTabWidget::pane {{ background: {p.BG_CARD}; border: 1px solid {download_border};
            border-radius: {RADIUS.CONTROL}px; }}
        QTableView[uiTable="true"], QTreeView[uiTable="true"], QListWidget[uiTable="true"] {{
            background: {p.BG_CARD}; alternate-background-color: {p.BG_ZEBRA};
            border: 1px solid {download_border}; border-radius: {RADIUS.CONTROL}px;
            selection-background-color: {p.BG_SELECTED}; selection-color: {p.TEXT_PRIMARY}; outline: none; }}
        QTableView[uiTable="true"]::item, QTreeView[uiTable="true"]::item {{ padding: {SPACE.S}px; border: none; }}
        QListWidget[uiTable="true"]::item {{ padding: {SPACE.L}px; border-bottom: 1px solid {download_border}; }}
        QTreeView[uiTable="true"]::item {{ min-height: {SIZE.BUTTON_H}px; }}
        QTableView[uiTable="true"]::item:hover, QTreeView[uiTable="true"]::item:hover,
        QListWidget[uiTable="true"]::item:hover {{ background: {p.BG_HOVER}; }}
        QTableView[uiTable="true"]::item:selected, QTreeView[uiTable="true"]::item:selected,
        QListWidget[uiTable="true"]::item:selected {{ background: {p.BG_SELECTED}; color: {p.TEXT_PRIMARY}; }}
        QTableView[uiTable="true"] QHeaderView::section, QTreeView[uiTable="true"] QHeaderView::section {{
            background: {p.BG_INPUT}; color: {p.TEXT_SECONDARY}; padding: {SPACE.M}px;
            border: none; border-bottom: 1px solid {download_border}; }}
        QPlainTextEdit, QTextEdit {{ background: {p.BG_CARD}; color: {p.TEXT_PRIMARY};
            border: 1px solid {download_border}; border-radius: {RADIUS.CONTROL}px;
            padding: {SPACE.S}px; selection-background-color: {p.ACCENT}; selection-color: {p.ACCENT_TEXT}; }}
        QPlainTextEdit:focus, QTextEdit:focus {{ border-color: {p.ACCENT}; }}
        QCheckBox::indicator, QTableView[uiTable="true"]::indicator {{
            width: {FORM.CHECK_SIZE}px; height: {FORM.CHECK_SIZE}px; border: 1px solid {p.BORDER_STRONG};
            border-radius: {RADIUS.CHIP}px; background: {p.BG_CARD}; }}
        QCheckBox::indicator:hover, QTableView[uiTable="true"]::indicator:hover {{ border-color: {p.ACCENT}; }}
        QCheckBox::indicator:checked, QTableView[uiTable="true"]::indicator:checked {{
            background: {p.ACCENT}; border-color: {p.ACCENT}; image: url("{style_image('check',p.ACCENT_TEXT)}"); }}
        QCheckBox::indicator:disabled {{ background: {p.BG_INPUT}; border-color: {p.BORDER}; }}
        QCheckBox::indicator:checked:disabled {{ background: {p.BORDER_STRONG}; image: url("{style_image('check',p.BG_CARD)}"); }}
        QProgressBar {{ min-height: 16px; border: 1px solid {download_border}; border-radius: {RADIUS.CHIP}px;
            background: {p.BG_INPUT}; text-align: center; color: {p.TEXT_PRIMARY}; }}
        QProgressBar::chunk {{ background: {p.ACCENT_SOFT}; border-radius: {RADIUS.CHIP}px; }}
        /* Application surfaces use the same reference tokens as playlists. */
        QMainWindow#libraryWindow QWidget, QDialog QWidget {{ font-size: {APP_UI.BODY_PX}px; }}
        QFrame#card, QWidget[uiSurface="card"], QWidget[uiSurface="footer"] {{
            background: {p.BG_CARD}; border: 1px solid {download_border}; border-radius: {APP_UI.CARD_RADIUS}px; }}
        QWidget[uiSurface="toolbar"] {{ background: transparent; border: none; }}
        QMainWindow#libraryWindow QLabel[role="title"], QDialog QLabel[uiRole="heading"] {{
            color: {ink}; font-size: {APP_UI.TITLE_PX}px; font-weight: 600; }}
        QMainWindow#libraryWindow QLabel[role="subtitle"], QDialog QLabel[role="subtitle"] {{
            color: {ink}; font-size: 18px; font-weight: 600; }}
        QLabel[role="muted"] {{ color: {DOWNLOAD_UI.SECONDARY_LIGHT if mode == 'light' else p.TEXT_SECONDARY}; }}
        QPushButton, QLineEdit, QComboBox {{ min-height: {APP_UI.CONTROL_H}px; border-color: {download_border}; }}
        QMainWindow#libraryWindow QPushButton, QMainWindow#libraryWindow QLineEdit,
        QMainWindow#libraryWindow QComboBox, QDialog QPushButton, QDialog QLineEdit, QDialog QComboBox {{
            min-height: {APP_UI.CONTROL_H}px; border-radius: 8px; border: 1px solid {download_border};
            color: {ink}; background: {p.BG_CARD}; padding-left: 12px; padding-right: 12px; }}
        QMainWindow#libraryWindow QPushButton:hover, QDialog QPushButton:hover {{ background: {soft_blue}; border-color: {p.ACCENT}; }}
        QMainWindow#libraryWindow QPushButton:pressed, QDialog QPushButton:pressed {{ background: {p.ACCENT_SOFT}; }}
        QMainWindow#libraryWindow QPushButton[role="primary"], QDialog QPushButton[role="primary"] {{
            color: {p.ACCENT_TEXT}; border-color: {p.ACCENT};
            background: qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 {p.ACCENT},stop:1 {p.ACCENT_HOVER}); }}
        QMainWindow#libraryWindow QPushButton[role="primary"]:hover, QDialog QPushButton[role="primary"]:hover {{ background: {p.ACCENT_HOVER}; }}
        QMainWindow#libraryWindow QPushButton[role="danger"], QDialog QPushButton[role="danger"] {{ color: {p.DANGER}; }}
        QMainWindow#libraryWindow QPushButton[role="danger"]:hover, QDialog QPushButton[role="danger"]:hover {{ background: {p.DANGER_SOFT}; border-color: {p.DANGER}; }}
        QDialog QPushButton[role="danger"]:focus {{ border-color: {p.DANGER}; }}
        QMainWindow#libraryWindow QPushButton:disabled, QDialog QPushButton:disabled,
        QMainWindow#libraryWindow QComboBox:disabled, QDialog QComboBox:disabled {{
            background: {p.BG_INPUT}; border-color: {download_border}; color: {p.TEXT_MUTED}; }}
        QMainWindow#libraryWindow QPushButton:focus, QDialog QPushButton:focus,
        QMainWindow#libraryWindow QLineEdit:focus, QDialog QLineEdit:focus,
        QMainWindow#libraryWindow QComboBox:focus, QDialog QComboBox:focus {{ border: 1px solid {p.ACCENT}; }}
        QMainWindow#libraryWindow QPushButton[windowControl="true"],
        QMainWindow#downloadPreview QPushButton[windowControl="true"],
        QDialog QPushButton[windowControl="true"] {{
            min-height: 38px; max-height: 38px; min-width: 36px; max-width: 36px;
            padding: 0; border: 1px solid transparent; border-radius: 6px; background: transparent; }}
        QMainWindow#libraryWindow QPushButton[windowControl="true"]:hover,
        QMainWindow#downloadPreview QPushButton[windowControl="true"]:hover,
        QDialog QPushButton[windowControl="true"]:hover {{ background: {soft_blue}; }}
        QMainWindow#libraryWindow QPushButton#windowClose:hover,
        QMainWindow#downloadPreview QPushButton#windowClose:hover, QDialog QPushButton#windowClose:hover {{ background: {p.DANGER_SOFT}; border-color: {p.DANGER}; }}
        QMainWindow#libraryWindow QWidget#headerTransfers, QMainWindow#libraryWindow QWidget#headerTransferActions {{
            background: transparent; border: none; }}
        QMainWindow#libraryWindow QPushButton[role="inlineAction"] {{ min-height: 24px; padding: 3px 7px;
            border: 1px solid transparent; background: transparent; color: {p.ACCENT}; }}
        QMainWindow#libraryWindow QTableView, QTableView[uiTable="true"], QTreeView[uiTable="true"], QListWidget[uiTable="true"] {{
            border: 1px solid {download_border}; border-radius: 10px; background: {p.BG_CARD}; alternate-background-color: {p.BG_CARD};
            selection-background-color: {p.ROW_SELECTED}; selection-color: {ink}; outline: none; }}
        QMainWindow#libraryWindow QTableView::item:selected, QTableView[uiTable="true"]::item:selected,
        QTreeView[uiTable="true"]::item:selected, QListWidget[uiTable="true"]::item:selected {{ background: {p.ROW_SELECTED}; color: {ink}; }}
        QMainWindow#libraryWindow QHeaderView::section, QTableView[uiTable="true"] QHeaderView::section,
        QTreeView[uiTable="true"] QHeaderView::section {{ background: {download_background}; color: {ink};
            border: none; border-bottom: 1px solid {download_border}; padding: 7px 8px; }}
        QMainWindow#libraryWindow QHeaderView::section {{ font-size: 14px; padding: 7px 3px; }}
        QDialog QTabBar::tab {{ min-height: 40px; padding: 0 14px; margin: 0 5px 6px 0;
            background: transparent; border: 1px solid transparent; border-radius: 9px; color: {p.TEXT_SECONDARY}; }}
        QDialog QTabBar::tab:selected {{ color: {p.ACCENT}; background: {p.ACCENT_SOFT}; border: 1px solid {p.ACCENT_SOFT}; }}
        QDialog QTabBar::tab:hover {{ background: {soft_blue}; }}
        QDialog#filterDialog QTabWidget::pane, QFrame#filterFooter {{ border-radius: {APP_UI.CARD_RADIUS}px; border-color: {download_border}; }}
        QMainWindow#libraryWindow QLabel#questBrandTitle {{ font-size: {PLAYLIST_UI.BRAND_PX}px; color: {ink}; font-weight: 700; }}
        QMainWindow#libraryWindow QLabel#questBrandIcon {{ border-radius: 12px;
            background: qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 {p.ACCENT},stop:1 {p.ACCENT_HOVER}); }}
        QMainWindow#libraryWindow QFrame#libraryPlayer {{ background: {p.BG_CARD}; border: 1px solid {download_border}; border-radius: 14px; min-height: 74px; }}
        QMainWindow#libraryWindow QFrame#libraryPlayer QPushButton[role="playerAction"] {{
            min-height: 52px; min-width: 52px; padding: 0; border-radius: 27px; background: {p.ACCENT}; border-color: {p.ACCENT}; color: {p.ACCENT_TEXT}; }}
        QMainWindow#libraryWindow QFrame#libraryPlayer QPushButton[role="playerAction"]:disabled {{ background: {p.BG_INPUT}; border-color: {download_border}; }}
        QMainWindow#libraryWindow QLabel[role="playerTitle"] {{ color: {ink}; font-size: 16px; font-weight: 600; }}
        QMainWindow#downloadPreview QFrame#downloadBrowse, QMainWindow#downloadPreview QFrame#downloadDetail {{ border-radius: 14px; }}
        QMainWindow#downloadPreview QFrame#downloadBrowse QPushButton {{ min-height: {APP_UI.CONTROL_H}px; }}
        QMainWindow#downloadPreview QPushButton[role="category"]:checked,
        QMainWindow#downloadPreview QPushButton[role="segment"]:checked {{ color: {p.ACCENT}; background: {p.ACCENT_SOFT}; border-color: {p.ACCENT_SOFT}; }}
        QMainWindow#downloadPreview QWidget#downloadTasks QPushButton,
        QMainWindow#downloadPreview QWidget#downloadImport QPushButton {{ min-height: {APP_UI.CONTROL_H}px; }}
        QMainWindow#downloadPreview QLabel#downloadTaskStat {{ border-radius: 12px; background: {download_background};
            border: 1px solid {download_border}; font-size: 17px; font-weight: 600; padding: 10px; }}
        QMainWindow#downloadPreview QLabel#downloadTaskStat[statKind="done"] {{ background: {p.SUCCESS_SOFT}; color: {p.SUCCESS_TEXT}; border-color: {p.SUCCESS_SOFT}; }}
        QMainWindow#downloadPreview QLabel#downloadTaskStat[statKind="attention"] {{ background: {p.WARNING_SOFT}; color: {p.WARNING_TEXT}; border-color: {p.WARNING_SOFT}; }}
        QMainWindow#downloadPreview QLabel#downloadTaskStat[statKind="active"] {{ background: {p.ACCENT_SOFT}; color: {p.ACCENT}; border-color: {p.ACCENT_SOFT}; }}
        QDialog#localPlaylists QListWidget::item {{ min-height: 60px; padding: 8px; border: 1px solid transparent; border-radius: 9px; }}
        QDialog#localPlaylists QListWidget::item:selected {{ background: {p.ACCENT_SOFT}; border-color: {p.ACCENT}; }}
        QProgressBar::chunk {{ background: {p.ACCENT}; border-radius: 5px; }}
        /* Opt-in playlist surfaces follow the approved reference. */
        QMainWindow#downloadPreview QFrame#downloadHeader {{ background: transparent;
            border: none; border-bottom: 1px solid {download_border}; min-height: 60px; }}
        QMainWindow#downloadPreview QLabel#questBrandTitle {{ font-size: {PLAYLIST_UI.BRAND_PX}px;
            color: {ink}; font-weight: 700; }}
        QMainWindow#downloadPreview QLabel#questBrandIcon {{ border-radius: 12px;
            background: qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 {p.ACCENT},stop:1 {p.ACCENT_HOVER}); }}
        QMainWindow#downloadPreview QTabBar#downloadNavigation::tab {{ min-height: 44px; padding: 0 18px;
            color: {ink}; background: transparent; border: 1px solid transparent; border-radius: 10px;
            margin: 0 3px; font-size: 16px; }}
        QMainWindow#downloadPreview QTabBar#downloadNavigation::tab:selected {{ color: {p.ACCENT};
            background: {p.ACCENT_SOFT}; border: 1px solid {p.ACCENT_SOFT}; font-weight: 600; }}
        QMainWindow#downloadPreview QTabBar#downloadNavigation::tab:hover {{ background: {soft_blue}; }}
        QMainWindow#downloadPreview QWidget#playlistPage {{ background: transparent; border: none; }}
        QMainWindow#downloadPreview QSplitter#playlistSplit::handle {{ background: {download_background}; border: none; }}
        QMainWindow#downloadPreview QFrame#playlistInfo,
        QMainWindow#downloadPreview QFrame#playlistDetail {{ background: {p.BG_CARD};
            border: 1px solid {download_border}; border-radius: {PLAYLIST_UI.PANEL_RADIUS}px; }}
        QMainWindow#downloadPreview QWidget#playlistInfoContent,
        QMainWindow#downloadPreview QScrollArea#playlistInfoScroll {{ background: {p.BG_CARD}; border: none; }}
        QMainWindow#downloadPreview QLabel#playlistTitle {{ font-size: 22px; color: {ink}; font-weight: 600; }}
        QMainWindow#downloadPreview QLabel#playlistBadge {{ border-radius: 15px; background: {p.ACCENT};
            color: {p.ACCENT_TEXT}; font-size: 14px; padding: 0 8px; }}
        QMainWindow#downloadPreview QPlainTextEdit#playlistDescription {{ border: none; padding: 0;
            background: {p.BG_CARD}; color: {p.TEXT_SECONDARY}; font-size: 16px; }}
        QMainWindow#downloadPreview QWidget#playlistMeta QLabel {{ font-size: 16px; }}
        QMainWindow#downloadPreview QLabel[role="statValue"] {{ color: {ink}; font-weight: 600; }}
        QMainWindow#downloadPreview QFrame#playlistDetailActions {{ border: none; background: transparent; }}
        QMainWindow#downloadPreview QFrame#playlistDetailActions QPushButton,
        QMainWindow#downloadPreview QFrame#playlistInfo QPushButton {{ min-height: {PLAYLIST_UI.ACTION_H}px;
            padding: 0 13px; border-radius: 9px; border: 1px solid {download_border}; font-size: 16px;
            color: {ink}; background: {p.BG_CARD}; }}
        QMainWindow#downloadPreview QFrame#playlistDetailActions QPushButton:hover {{ background: {soft_blue}; border-color: {p.ACCENT}; }}
        QMainWindow#downloadPreview QFrame#playlistDetailActions QPushButton:pressed {{ background: {p.ACCENT_SOFT}; }}
        QMainWindow#downloadPreview QFrame#playlistDetailActions QPushButton:focus {{ border: 1px solid {p.ACCENT}; }}
        QMainWindow#downloadPreview QFrame#playlistDetailActions QPushButton[role="clearSelection"]:hover {{ color: {p.DANGER}; background: {p.DANGER_SOFT}; border-color: {p.DANGER}; }}
        QMainWindow#downloadPreview QFrame#playlistDetailActions QPushButton[role="primary"],
        QMainWindow#downloadPreview QFrame#playlistInfo QPushButton[role="primary"] {{ color: {p.ACCENT_TEXT};
            border-color: {p.ACCENT}; background: qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 {p.ACCENT},stop:1 {p.ACCENT_HOVER}); }}
        QMainWindow#downloadPreview QFrame#playlistDetailActions QPushButton[role="primary"]:hover,
        QMainWindow#downloadPreview QFrame#playlistInfo QPushButton[role="primary"]:hover {{ background: {p.ACCENT_HOVER}; }}
        QMainWindow#downloadPreview QFrame#playlistDetailActions QPushButton:disabled,
        QMainWindow#downloadPreview QFrame#playlistInfo QPushButton:disabled {{ color: {p.TEXT_MUTED};
            background: {p.BG_INPUT}; border-color: {download_border}; }}
        QMainWindow#downloadPreview QTableView#playlistSongs {{ border: 1px solid {download_border};
            border-radius: 10px; color: {ink}; background: {p.BG_CARD}; font-size: 16px; }}
        QMainWindow#downloadPreview QTableView#playlistSongs QHeaderView::section {{ background: {download_background};
            color: {ink}; border: none; border-bottom: 1px solid {download_border}; padding: 0 8px; font-size: 15px; }}
        QMainWindow#downloadPreview QWidget#playlistPager QPushButton {{ min-height: 38px; padding: 0 10px; }}
        QMainWindow#downloadPreview QLabel#playlistPageNumber {{ background: {p.ACCENT}; color: {p.ACCENT_TEXT};
            border-radius: 7px; padding: 10px 14px; }}
        QMainWindow#downloadPreview QFrame#downloadPlayer {{ min-height: 74px; border-radius: 14px;
            background: {p.BG_CARD}; border: 1px solid {download_border}; }}
        QMainWindow#downloadPreview QFrame#downloadPlayer QPushButton[role="playerAction"] {{
            min-height: {PLAYLIST_UI.PLAYER_BUTTON-2}px; min-width: {PLAYLIST_UI.PLAYER_BUTTON-2}px;
            border-radius: {PLAYLIST_UI.PLAYER_BUTTON//2}px; padding: 0; background: {p.ACCENT}; border: 1px solid {p.ACCENT}; }}
        QMainWindow#downloadPreview QFrame#downloadPlayer QPushButton[role="playerAction"]:hover {{ background: {p.ACCENT_HOVER}; }}
        QMainWindow#downloadPreview QFrame#downloadPlayer QPushButton[role="playerAction"]:disabled {{ background: {p.BG_INPUT}; border-color: {download_border}; }}
        QMainWindow#downloadPreview QFrame#downloadPlayer QLabel[role="playerTitle"] {{ font-size: 16px; font-weight: 600; color: {ink}; }}
        QMainWindow#downloadPreview QLabel#playerVolumeValue {{ color: {p.TEXT_SECONDARY}; font-size: 13px; }}
        QDialog#localPlaylists QPushButton[role="skip"], QDialog#localPlaylists QToolButton[role="playerUtility"], QFrame#libraryPlayer QPushButton[role="skip"], QFrame#libraryPlayer QToolButton[role="playerUtility"] {{
            min-width: 0; min-height: 0; padding: 0; border: none; background: transparent; }}
        QFrame#libraryPlayer QToolButton[role="playerUtility"]:hover,
        QFrame#libraryPlayer QToolButton[role="playerUtility"]:checked {{ background: {soft_blue}; }}
        QDialog#localPlaylists QFrame#libraryPlayer QPushButton[role="playerAction"] {{ min-height: 52px; max-height: 52px; min-width: 52px; max-width: 52px; padding: 0; border-radius: 26px; background: {p.ACCENT}; border: none; }}
        QDialog#localPlaylists QFrame#libraryPlayer QPushButton[role="playerAction"]:disabled {{ background: {p.BG_INPUT}; border: 1px solid {download_border}; }}
        QDialog#localPlaylists QLabel#playlistManagerTitle, QDialog#wirelessManager QLabel#deviceManagerTitle {{ font-size: 24px; font-weight: 600; color: {ink}; }}
        QDialog#localPlaylists QLabel#questBrandIcon, QDialog#wirelessManager QLabel#questBrandIcon {{ border-radius: 10px; background: {p.ACCENT}; }}
        QDialog#localPlaylists QFrame[uiRole="player"] {{ background: {p.BG_CARD}; border: 1px solid {download_border}; border-radius: 12px; }}
        QLabel#historyBadge {{ background: {soft_blue}; color: {ink}; border-radius: 6px; padding: 6px 10px; }}
        QMainWindow#libraryWindow QPushButton#diagnosticLogs[compactIcon="true"] {{
            min-width: 20px; max-width: 20px; padding-left: 6px; padding-right: 6px; }}
    """)
    refresh_action_icons(app)
    from .window_appearance import install_appearance
    install_appearance(app,mode,download_border)
