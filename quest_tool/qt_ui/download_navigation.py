"""Lazy same-process library navigation; never launch another data owner."""


class DownloadNavigation:
    def __init__(self, download, sandbox, factory=None):
        self.download = download
        self.sandbox = sandbox
        self.factory = factory or self.create_library
        self.library = None
        download.libraryRequested.connect(self.open)

    def create_library(self):
        from .library_window import LibraryWindow
        from .adb_provider import AdbReadOnlyProvider
        from .wireless_client import WirelessClient
        from .transfer_pull import AdbPullBackend
        from .transfer_push import AdbPushBackend
        from ..messages import current_language
        provider = AdbReadOnlyProvider()
        return LibraryWindow(self.sandbox, current_language(), self.download.mode,
            device_provider=provider,wireless_client=WirelessClient(provider),
            transfer_factory=lambda:(AdbPullBackend(provider),AdbPushBackend(provider,self.sandbox/'transfers')))

    def open(self):
        if self.library is None:
            library = self.factory()
            library.attach_downloads(self.download)
            if self.download.download_browser is not None and library.target != self.download.download_browser.target:
                library.set_target(self.download.download_browser.target)
            self.library = library
        self.library.show_from_downloads()

    def close(self):
        if self.library is not None:
            self.library.close()
            self.library.wait_closed()
