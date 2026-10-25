"""Four bounded, independently cached cover lanes; UI owns dispatch and epochs."""
import threading
import time

from ..downloads import DownloadError, check_cancel
from ..workers import TaskRunner


def create_media():
    from ..search_media import MediaClient

    class LaneMedia(MediaClient):
        disk_cache = None

        def load(self, url, kind, cancel):
            disk = self.disk_cache if kind == 'cover' else None
            epoch = disk.epoch if disk is not None else None
            check_cancel(cancel)
            data = disk.get(url) if disk is not None else None
            check_cancel(cancel)
            if data is not None:
                self.covers[url] = data
                self.covers.move_to_end(url)
            else:
                data = super().load(url, kind, cancel)
                check_cancel(cancel)
                if disk is not None:disk.put(url, data, epoch)
            # Four independent lanes retain at most the original 64 encoded covers.
            while len(self.covers) > 16:
                self.covers.popitem(last=False)
            return data
    return LaneMedia()


def load_cover(tasks, media, ticket, url, cancel):
    """Worker receives no UI object; always acknowledges its occupied lane."""
    data, error = None, ''
    try:
        check_cancel(cancel)
        data = media.load(url, 'cover', cancel)
        check_cancel(cancel)
    except Exception as exc:
        data = None
        error = exc.code if isinstance(exc, DownloadError) else 'cover'
    finally:
        tasks.emit('cover_job', payload={'ticket': ticket, 'data': data, 'error': error})


class CoverSession:
    """SearchSession-compatible output; pump/request/prioritize run on UI only."""
    def __init__(self, media_factory=create_media, clock=time.monotonic):
        self.tasks = TaskRunner()
        self.workers = TaskRunner()
        self.media = [media_factory() for _ in range(4)]
        self.clock = clock
        self.generation = 0
        self.epoch = 0
        self.ticket = 0
        self.active = {}
        self.pending = []
        self.priorities = ()
        self.cancel_event = threading.Event()
        self.closed = False
        self.completed = True
        self.not_before = 0
        self.blocked_code = ''
        self.reaper = None
        self.disk_cache = None

    def configure_disk(self, sandbox):
        from .cover_disk import CoverDiskCache

        if self.disk_cache is None:
            self.disk_cache = CoverDiskCache(sandbox)
            for media in self.media:media.disk_cache = self.disk_cache
        return self.disk_cache

    def request(self, items, epoch, _order):
        self.cancel()
        if self.closed:
            raise DownloadError('closed')
        self.generation += 1
        self.epoch = epoch
        self.cancel_event = threading.Event()
        self.completed = False
        self.pending = list(dict.fromkeys(item['coverURL'] for item in items if item.get('coverURL')))
        self.prioritize(self.priorities)
        if self.clock() < self.not_before:
            self.pending.clear()
            self.completed = True
            self.tasks.emit('search_error', payload={'generation': self.generation, 'code': self.blocked_code})
        else:
            self.pump()
        return self.generation

    def prioritize(self, urls):
        self.priorities = tuple(dict.fromkeys(urls))
        positions = {url: index for index, url in enumerate(self.priorities)}
        self.pending.sort(key=lambda url: positions.get(url, len(positions)))

    def pump(self):
        if self.closed:
            return
        for message in self.workers.drain():
            if message.kind != 'cover_job':
                continue
            data = message.payload
            job = self.active.pop(data['ticket'], None)
            if job is None or job['cancel'].is_set() or job['generation'] != self.generation:
                continue
            error = data['error']
            if error in ('rate_limit', 'auth', 'access'):
                # Existing fetch honors Retry-After before emitting its final error.
                # Do not let a page change immediately restart a rejected batch.
                self.not_before = self.clock()+30
                self.blocked_code = error
                self.cancel()
                self.tasks.emit('search_error', payload={'generation': self.generation, 'code': error})
            else:
                self.tasks.emit('cover', payload={'generation': job['epoch'], 'url': job['url'], 'data': data['data']})
        occupied = {job['lane'] for job in self.active.values()}
        if not self.completed:
            for lane, media in enumerate(self.media):
                if lane in occupied or not self.pending:
                    continue
                url = self.pending.pop(0)
                self.ticket += 1
                ticket, cancel = self.ticket, self.cancel_event
                self.active[ticket] = dict(lane=lane, url=url, epoch=self.epoch,
                                           generation=self.generation, cancel=cancel)
                tasks = self.workers
                tasks.submit(lambda _token, media=media, ticket=ticket, url=url, cancel=cancel:
                             load_cover(tasks, media, ticket, url, cancel), name='qt-cover')
            if not self.pending and not any(job['generation'] == self.generation for job in self.active.values()):
                self.completed = True
                self.tasks.emit('search', payload={'generation': self.generation, 'result': {}})

    def cancel(self):
        self.cancel_event.set()
        self.pending.clear()
        self.completed = True

    def close(self):
        if self.closed:
            return
        self.cancel()
        self.closed = True
        threads = tuple(self.workers._threads)
        if threads:
            self.reaper = threading.Thread(target=lambda: [thread.join() for thread in threads],
                                           name='cover-shutdown', daemon=False)
            self.reaper.start()

    def wait_closed(self):
        if self.reaper is not None:self.reaper.join()
