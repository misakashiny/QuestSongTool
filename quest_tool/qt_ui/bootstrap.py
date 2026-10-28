"""Qt-only startup, native frame and isolated diagnostics lifecycle."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

from PySide6.QtCore import qVersion
from PySide6.QtWidgets import QApplication, QMessageBox

from ..messages import set_language, tr
from .main_window import MainWindow
from .download_window import DownloadWindow
from .runtime import PreviewRuntime
from . import STAGE


def run(argv=None):
    parser = argparse.ArgumentParser(description=tr("qt.cli"))
    parser.add_argument("--data-dir", type=Path, help=tr("qt.cli_data"))
    parser.add_argument("--lang", choices=("zh", "en"), default="zh")
    parser.add_argument("--theme", choices=("light", "dark"), default="light")
    parser.add_argument("--components", action="store_true", help=tr("qt.dl.components"))
    parser.add_argument("--samples", action="store_true", help=tr("qt.cli_samples"))
    parser.add_argument("--library", action="store_true", help=tr("qt.library.cli"))
    parser.add_argument("--quest-adb", action="store_true", help=tr("qt.device.cli_adb"))
    parser.add_argument("--quest-pull", action="store_true", help=tr("qt.transfer.cli_pull"))
    parser.add_argument("--quest-push", action="store_true", help=tr('qt.transfer.cli_push'))
    parser.add_argument("--adb-path", help=tr("qt.device.cli_adb_path"))
    options = parser.parse_args(argv)
    if (options.quest_pull or options.quest_push) and not (options.library and options.quest_adb):
        parser.error(tr('qt.transfer.cli_conflict'))
    if (options.quest_adb and not options.library) or (options.adb_path is not None and not options.quest_adb):
        parser.error(tr("qt.device.cli_conflict"))
    if options.library and (options.samples or options.components):
        parser.error(tr("qt.library.cli_conflict"))
    set_language(options.lang)
    app = QApplication([sys.argv[0]])
    app.setOrganizationName("QuestSongCompare")
    app.setApplicationName("QuestSongCompare-QtPreview")
    app.setStyle("Fusion")
    runtime = None
    window = None
    navigation = None
    previous_hook = sys.excepthook
    try:
        runtime = PreviewRuntime(options.data_dir)
        runtime.logger.info("Starting %s; Qt=%s; platform=%s", STAGE, qVersion(), app.platformName())

        def report_error(kind, value, traceback):
            runtime.logger.error("Unhandled UI exception", exc_info=(kind, value, traceback))
            QMessageBox.critical(None, tr("qt.error"), tr("qt.error_hint"))

        sys.excepthook = report_error
        if options.library:
            from .library_window import LibraryWindow
            provider = None
            wireless = None;transfer_factory = None
            if options.quest_adb:
                from .adb_provider import AdbReadOnlyProvider
                from .wireless_client import WirelessClient
                from .transfer_pull import AdbPullBackend
                from .transfer_push import AdbPushBackend
                provider = AdbReadOnlyProvider(options.adb_path)
                wireless = WirelessClient(provider)
                transfer_factory = lambda:(AdbPullBackend(provider),AdbPushBackend(provider,runtime.path/'transfers'))
            transfer = None
            if options.quest_pull:
                from .transfer_pull import AdbPullBackend
                transfer = AdbPullBackend(provider)
            push = None
            if options.quest_push:
                from .transfer_push import AdbPushBackend
                push = AdbPushBackend(provider,runtime.path/'transfers')
            window = LibraryWindow(runtime.path, options.lang, options.theme, device_provider=provider, transfer_backend=transfer, push_backend=push, wireless_client=wireless, transfer_factory=transfer_factory)
        else:
            window_class = MainWindow if options.components else DownloadWindow
            window = window_class(options.lang, options.theme)
        if not options.library and not options.components and not options.samples:
            window.browse_sandbox = runtime.path
            window.enable_search()
            window.enable_covers()
            window.enable_preview()
            window.enable_downloads(runtime.path)
            from .download_navigation import DownloadNavigation
            navigation = DownloadNavigation(window,runtime.path)
        screen = app.primaryScreen()
        if screen:
            available = screen.availableGeometry()
            window.resize(min(window.width(), available.width()),
                          min(window.height(), available.height()))
        window.show()
        return app.exec()
    except (OSError, ValueError, RuntimeError) as exc:
        if runtime:
            runtime.logger.exception("Startup failed")
        QMessageBox.critical(None, tr("qt.error"), str(exc))
        return 1
    finally:
        sys.excepthook = previous_hook
        if navigation is not None:navigation.close()
        if isinstance(window, DownloadWindow) and window.download_browser:
            window.download_browser.close()
            window.download_browser.wait_closed()
        if options.library and window is not None:
            window.wait_closed()
        if runtime:
            runtime.logger.info("Stopped %s", STAGE)
            runtime.close()
