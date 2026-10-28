"""At most two Quest audio files, owned by this process in the PC preview area."""
from collections import OrderedDict
from pathlib import Path
import tempfile
import threading

from .. import audio
from ..downloads import DownloadError, check_cancel


class QuestAudioCache:
    def __init__(self, provider, sandbox):
        self.provider=provider;self.directory=None;self.sandbox=Path(sandbox);self.entries=OrderedDict();self.lock=threading.Lock()

    def load(self, request, cancel):
        while not self.lock.acquire(timeout=.05):check_cancel(cancel)
        try:
            check_cancel(cancel);return self._load(request,cancel)
        finally:self.lock.release()

    def _load(self, request, cancel):
        key=(request['device']['identity'],request['epoch'],request['folder'],request['item']['info_hash'])
        if key in self.entries:
            path=self.entries.pop(key);self.entries[key]=path;return path
        if not callable(getattr(self.provider,'read_audio',None)):raise DownloadError('local_audio')
        data,name=self.provider.read_audio(request['device'],request['item'],cancel);check_cancel(cancel)
        if not isinstance(data,bytes) or not 0<len(data)<=64*1024*1024:raise DownloadError('limit')
        if not self.directory:
            self.sandbox.mkdir(parents=True,exist_ok=True)
            self.directory=tempfile.TemporaryDirectory(prefix='quest-audio-',dir=self.sandbox)
        suffix=Path(name).suffix.lower()
        if suffix not in ('.ogg','.egg','.wav','.mp3','.flac','.opus'):suffix='.ogg'
        # Evict before writing, keeping the cache below 128 MiB.
        while len(self.entries)>=2:
            _key,path=self.entries.popitem(last=False);path.unlink(missing_ok=True)
        path=Path(self.directory.name)/('track-'+str(request['request_id'])+suffix)
        try:path.write_bytes(data);check_cancel(cancel)
        except Exception:path.unlink(missing_ok=True);raise
        self.entries[key]=path
        return path

    def close(self):
        self.entries.clear()
        if self.directory:self.directory.cleanup();self.directory=None


def playback_client(base):
    class LibraryPlaybackClient(base):
        def __init__(self, player, provider, sandbox):
            super().__init__(player);self.cache=QuestAudioCache(provider,sandbox)

        def search(self, request, page, order, cancel):
            if request['key'][0]=='local':return super().search(request,page,order,cancel)
            path=self.cache.load(request,cancel);check_cancel(cancel)
            total=audio.probe_duration(path) or request['item'].get('duration',0);check_cancel(cancel)
            if not self.player.play_file(path,seconds=0,start=request['start'],request_id=request['request_id']):
                raise DownloadError('local_audio')
            check_cancel(cancel)
            return dict(key=request['key'],request_id=request['request_id'],total=total)
    return LibraryPlaybackClient
