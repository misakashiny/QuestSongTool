"""Bounded official playlist discovery and paged map metadata."""
import json
from pathlib import Path
import tempfile
from urllib.parse import urlencode

from ..downloads import BeatSaverClient, DownloadError, check_cancel, safe_web_url
from ..song_search import number, search_map


ORDERS = ('Curated', 'Latest', 'Rating', 'Relevance')
DETAIL_PAGE_SIZE = 500


def plain(value, limit):
    return value[:limit] if isinstance(value, str) else ''


def playlist_record(data):
    if not isinstance(data, dict) or type(data.get('playlistId')) is not int or not 0 < data['playlistId'] < 10**12:
        raise DownloadError('metadata')
    dynamic = data.get('type') == 'Search'
    stats = data.get('stats')
    if stats is None:stats = {}
    owner = data.get('owner') or {}
    if not isinstance(stats, dict) or not isinstance(owner, dict):
        raise DownloadError('metadata')
    total = stats.get('totalMaps')
    if not (dynamic and total is None) and (type(total) is not int or not 0 <= total <= 1000000):
        raise DownloadError('metadata')
    try:
        cover = safe_web_url(plain(data.get('playlistImage'), 1000))
    except (DownloadError, ValueError):
        cover = ''
    return dict(id=data['playlistId'], dynamic=dynamic, title=plain(data.get('name'), 200), author=plain(owner.get('name'), 160),
                description=plain(data.get('description'), 4000), coverURL=cover, total=total,
                duration=number(stats.get('totalDuration')), rating=number(stats.get('avgScore'), 1),
                updated=plain(data.get('updatedAt'), 40), curated=bool(data.get('curatedAt')))


class PlaylistCatalogClient(BeatSaverClient):
    def __init__(self, sandbox):
        super().__init__()
        self.sandbox = Path(sandbox)

    def search(self, request, page, order, cancel):
        if type(page) is not int or not 0 <= page <= 1000:
            raise DownloadError('query')
        kind = request.get('kind')
        if kind == 'list':
            query = request.get('query', '').strip()
            if len(query) > 200 or any(ord(c) < 32 for c in query) or order not in ORDERS:
                raise DownloadError('query')
            url = 'https://api.beatsaver.com/playlists/search/'+str(page)+'?'+urlencode(dict(q=query, order=order, includeEmpty='false'))
        elif kind == 'detail':
            identity = request.get('id')
            if type(identity) is not int or not 0 < identity < 10**12:
                raise DownloadError('query')
            url = f'https://api.beatsaver.com/playlists/id/{identity}/{page}'
        else:
            raise DownloadError('query')
        self.sandbox.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='playlist-catalog-', dir=self.sandbox) as folder:
            path = Path(folder)/'response.json'
            self.fetch(url, path, cancel, lambda _done, _total: None, limit=8*1024*1024)
            check_cancel(cancel)
            try:
                data = json.loads(path.read_text(encoding='utf-8'))
                if kind == 'list':
                    docs = data['docs'];info = data['info']
                    if not isinstance(docs, list) or len(docs) > 100 or not isinstance(info, dict):
                        raise ValueError('list shape')
                    pages = info.get('pages');total = info.get('total')
                    if type(pages) is not int or type(total) is not int or pages < 0 or total < 0:
                        raise ValueError('pagination')
                    rows = []
                    unavailable = 0
                    for doc in docs:
                        try:
                            rows.append(playlist_record(doc))
                        except DownloadError as exc:
                            # Only invalid identities/metadata are skipped.
                            # Search playlists legitimately have no statistics.
                            if exc.code == 'metadata':
                                unavailable += 1
                                continue
                            raise
                    if len({row['id'] for row in rows}) != len(rows):
                        raise ValueError('duplicate identity')
                    result = dict(kind=kind, rows=rows, page=page,
                                  has_next=bool(docs) and page+1 < pages,
                                  total=total, unavailable=unavailable)
                else:
                    playlist = playlist_record(data['playlist']);maps = data['maps']
                    if playlist['id'] != identity or not isinstance(maps, list) or len(maps) > DETAIL_PAGE_SIZE:
                        raise ValueError('detail shape')
                    rows = [search_map(item.get('map')) if isinstance(item, dict) else None for item in maps]
                    # A short first page is a complete current query result. Do
                    # not use config.mapCount: it is a search limit, not a count.
                    if playlist['total'] is None and page == 0 and len(maps) < DETAIL_PAGE_SIZE:
                        playlist['total'] = len(maps)
                    total = playlist['total']
                    result = dict(kind=kind, playlist=playlist, rows=rows, page=page,
                                  has_next=len(maps) == DETAIL_PAGE_SIZE and (total is None or (page+1)*DETAIL_PAGE_SIZE < total),
                                  complete=page == 0 and total is not None and len(maps) == total and all(rows))
            except (ValueError, KeyError, TypeError, AttributeError) as exc:
                raise DownloadError('metadata', str(exc)) from exc
        check_cancel(cancel)
        return result
