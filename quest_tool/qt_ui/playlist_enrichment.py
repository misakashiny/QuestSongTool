"""Lazy playlist display metadata and hash-only matching against a Quest snapshot."""
from collections import OrderedDict
import json
from pathlib import Path
import re
import tempfile

from PySide6.QtCore import QObject, Qt, QTimer, QSize
from PySide6.QtGui import QIcon

from ..downloads import BeatSaverClient, DownloadError, check_cancel
from ..song_search import SearchSession, search_map
from .cover_browser import CoverCache
from .cover_session import CoverSession


class PlaylistMetadataClient(BeatSaverClient):
    def search(self, request, _page, _order, cancel):
        hashes=request['hashes']
        if not isinstance(hashes,list) or not 1<=len(hashes)<=40 or any(not re.fullmatch('[0-9a-f]{40}',v) for v in hashes):
            raise DownloadError('query')
        with tempfile.TemporaryDirectory(prefix='playlist-metadata-') as temp:
            path=Path(temp)/'metadata.json'
            self.fetch('https://api.beatsaver.com/maps/hash/'+','.join(hashes),path,cancel,lambda *_:None,limit=4*1024*1024)
            data=json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(data,dict):raise DownloadError('metadata')
        if len(hashes)==1 and 'versions' in data:data={hashes[0]:data}
        found={}
        for digest in hashes:
            value=data.get(digest)
            if not isinstance(value,dict) or not isinstance(value.get('versions'),list):continue
            # A hash lookup can return a map with a newer version. Keep the exact
            # requested version's artwork and identity, never silently substitute.
            versions=[v for v in value['versions'] if isinstance(v,dict) and str(v.get('hash','')).lower()==digest]
            row=search_map(dict(value,versions=versions)) if versions else None
            if row and row['hash']==digest:found[digest]=row
        check_cancel(cancel);return found


class QuestPlaylistIndexClient:
    def __init__(self, provider):self.provider=provider
    def search(self, request, _page, _order, cancel):
        return self.provider.read_playlist_index(request['device'],request['items'],cancel)


class PlaylistEnrichment(QObject):
    def __init__(self, controller, provider):
        super().__init__(controller);self.c=controller;self.window=controller.window;self.closed=False
        self.metadata=OrderedDict();self.attempted=set();self.images=CoverCache();self.lookup_failed=False
        self.lookup=SearchSession(PlaylistMetadataClient());self.lookup_generation=None
        self.index=SearchSession(QuestPlaylistIndexClient(provider)) if callable(getattr(provider,'read_playlist_index',None)) else None
        self.index_generation=None;self.index_context=None;self.quest_matches={};self.index_attempted=False;self.index_failed=False
        self.covers=CoverSession();self.covers.configure_disk(self.window.sandbox);self.cover_urls=();self.cover_epoch=0
        self.timer=QTimer(self);self.timer.setInterval(80);self.timer.timeout.connect(self.pump)
        self.window.quest_model.modelReset.connect(self.snapshot_changed)

    def context(self):
        w=self.window;selected=w.device_browser.selected
        return (w.quest_identity,w.device_browser.read_epoch) if w.quest_ready and selected and selected['identity']==w.quest_identity else None

    def snapshot_changed(self):
        if self.closed:return
        if self.context()!=self.index_context:
            if self.index:self.index.cancel()
            self.index_generation=None;self.index_context=None;self.quest_matches={};self.index_attempted=False;self.index_failed=False
        if self.c.dialog and self.c.dialog.isVisible():self.schedule()

    def schedule(self, *_):
        if not self.closed:self.timer.start()

    def refresh(self):
        self.attempted.clear();self.lookup_failed=False;self.cover_urls=();self.covers.cancel()
        self.index_context=None;self.quest_matches={};self.index_attempted=False;self.index_failed=False;self.schedule()

    def installed(self, digest):
        c=self.c
        if c.context==c.local_context() and digest in c.matches:
            return 'local',c.matches[digest]
        if self.context()==self.index_context and digest in self.quest_matches:
            return 'quest',self.quest_matches[digest]
        return None

    def display(self, song):
        row=dict(self.metadata.get(song['hash'],{}));match=self.installed(song['hash'])
        if match:
            model=self.window.model if match[0]=='local' else self.window.quest_model
            installed=next((v for v in model.rows if v['folder']==match[1]),{})
            row.update({key:installed[key] for key in ('title','author','bpm','duration') if installed.get(key)})
        row['title']=song['songName'] or row.get('title') or (song['hash'][:12]+'…' if song['hash'] else song['key'])
        row['mapper']=song['levelAuthorName'] or row.get('mapper','')
        row['id']=song['key'] or row.get('id','')
        return row

    def icon(self, song):
        match=self.installed(song['hash'])
        if match:
            cache=self.window.covers.cache if match[0]=='local' else self.window.quest_media.cache
            image=cache.image(match[1],QSize(44,44),self.c.dialog.devicePixelRatioF())
            if image:return QIcon(image)
        url=self.metadata.get(song['hash'],{}).get('coverURL','')
        image=self.images.image(url,QSize(44,44),self.c.dialog.devicePixelRatioF()) if url else None
        return QIcon(image) if image else None

    def artwork(self, folder, size, dpr):
        digest=next((k for k,v in self.quest_matches.items() if v==folder),None)
        url=self.metadata.get(digest,{}).get('coverURL','')
        return self.images.image(url,size,dpr) if url else None

    def visible_songs(self):
        d=self.c.dialog;record=self.c.current()
        if not record:return []
        first=max(0,d.table.rowAt(0));out=[]
        for i in range(first,min(d.proxy.rowCount(),first+40)):
            cell=d.proxy.index(i,0)
            if not d.table.visualRect(cell).intersects(d.table.viewport().rect()) and out:break
            out.append(record['songs'][cell.data(Qt.ItemDataRole.UserRole)])
        for i in self.c.selected():out.insert(0,record['songs'][i])
        # A .bplist often has no embedded image. Its first song supplies the
        # sidebar thumbnail, with a bounded eight visible playlists per pass.
        start=max(0,d.lists.indexAt(d.lists.viewport().rect().topLeft()).row())
        for i in range(start,min(start+8,d.lists.count())):
            item=d.lists.item(i)
            if d.lists.visualItemRect(item).intersects(d.lists.viewport().rect()):
                songs=self.c.records[i]['songs']
                if songs:out.insert(0,songs[0])
        return out[:40]

    def pump(self):
        d=self.c.dialog
        if self.closed or not d or not d.isVisible():self.timer.stop();return
        context=self.context()
        if context!=self.index_context or (context and self.index_generation is None and not self.quest_matches and not self.index_attempted and self.c.current()):
            if self.index:self.index.cancel()
            self.index_generation=None;self.quest_matches={};self.index_context=context;self.index_failed=False
            if context and self.index and self.c.current():
                checks=getattr(self.window,'identity_checks',None)
                known=checks.values['quest'] if checks else {}
                self.quest_matches={digest:folder for folder,digest in known.items()}
                items=[dict(v) for v in self.window.quest_model.rows if not v['error'] and v.get('info_hash') and v['folder'] not in known]
                if items:self.index_generation=self.index.request(dict(device=dict(self.window.device_browser.selected),items=items),0,'')
                else:self.index_attempted=True
                self.c.render()
        if self.index:
            for event in self.index.tasks.drain():
                value=event.payload
                if not isinstance(value,dict) or value.get('generation')!=self.index_generation:continue
                self.index_generation=None
                if event.kind=='search' and self.context()==self.index_context and value['result']['identity']==self.index_context[0]:
                    self.quest_matches.update(value['result']['matches']);self.index_attempted=True;self.index_failed=bool(value['result']['unknown'])
                elif event.kind=='search_error':self.index_attempted=True;self.index_failed=True
                d.model.changed();self.c.render()
        for event in self.lookup.tasks.drain():
            value=event.payload
            if not isinstance(value,dict) or value.get('generation')!=self.lookup_generation:continue
            self.lookup_generation=None
            if event.kind=='search':
                self.lookup_failed=False
                self.metadata.update(value['result']);self.attempted.difference_update(value['result'])
                while len(self.metadata)>256:self.metadata.popitem(last=False)
            elif event.kind=='search_error':
                self.lookup_failed=True
                # Wait for explicit refresh after a rejected/offline request.
                self.attempted.update(s['hash'] for s in (self.c.current() or {}).get('songs',[]))
            d.model.changed();self.c.render()
        visible=self.visible_songs()
        hashes=list(dict.fromkeys(s['hash'] for s in visible if s['hash'] and s['hash'] not in self.metadata and s['hash'] not in self.attempted))
        if hashes and self.lookup_generation is None:
            self.attempted.update(hashes);self.lookup_generation=self.lookup.request(dict(hashes=hashes[:40]),0,'')
        urls=tuple(dict.fromkeys(self.metadata.get(s['hash'],{}).get('coverURL') for s in visible))
        urls=tuple(u for u in urls if u and u not in self.images.sources)
        if urls!=self.cover_urls:
            self.cover_urls=urls;self.cover_epoch+=1;self.covers.request([dict(coverURL=u) for u in urls],self.cover_epoch,'')
        self.covers.pump()
        for event in self.covers.tasks.drain():
            v=event.payload
            if event.kind=='cover' and isinstance(v,dict) and v.get('generation')==self.cover_epoch and v.get('data'):
                self.images.put(v['url'],v['data']);d.model.changed();self.window.preview.render()
                for i,record in enumerate(self.c.records):
                    if record['cover'] or not record['songs']:continue
                    url=self.metadata.get(record['songs'][0]['hash'],{}).get('coverURL','')
                    if url!=v['url']:continue
                    image=self.images.image(url,QSize(64,64),d.devicePixelRatioF())
                    if image and d.lists.item(i):d.lists.item(i).setIcon(QIcon(image))

    def cancel(self):
        self.timer.stop();self.lookup.cancel();self.lookup_generation=None;self.covers.cancel();self.cover_urls=()
        if self.index and self.index_generation is not None:
            self.index.cancel();self.index_generation=None;self.index_context=None;self.index_attempted=False

    def close(self):
        self.closed=True;self.cancel();self.lookup.close();self.covers.close()
        if self.index:self.index.close()

    def wait_closed(self):
        for session in (self.lookup,self.index):
            if session:
                for thread in tuple(session.tasks._threads):thread.join()
        self.covers.wait_closed()
