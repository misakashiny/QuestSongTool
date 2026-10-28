"""Search shape errors, unavailable versions and direct map lookups; no network."""
import copy
import json
from pathlib import Path
import sys
import threading
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT),str(ROOT/'tests')]


def main():
    from quest_tool.downloads import DownloadError
    from quest_tool.song_search import SearchClient, search_map
    from smoke_search import sample
    record = sample()
    class Client(SearchClient):
        response = None
        def __init__(self):self.urls = []
        def fetch(self,url,destination,*_args,**_kwargs):
            self.urls.append(url);destination.write_text(json.dumps(self.response),encoding='utf-8')
    client = Client();cancel = threading.Event()
    client.response = dict(docs=[record])
    result = client.search('normal name',0,'Relevance',cancel,filters={},page_size=48)
    assert len(result['rows']) == 1 and 'pageSize=48' in client.urls[-1]
    for query in ('#123','!bsr 123','https://beatsaver.com/maps/123'):
        client.response = dict(record,id='123')
        assert client.search(query,0,'Relevance',cancel)['rows'][0]['id'] == '123'
        assert client.urls[-1] == 'https://api.beatsaver.com/maps/id/123'
        before = len(client.urls)
        assert not client.search(query,1,'Relevance',cancel)['rows'] and len(client.urls) == before
    original = copy.deepcopy(record)
    record['versions'].append(dict(state='Published',createdAt='9999',hash='bad',downloadURL='https://evil.example/x.zip'))
    assert search_map(record) == search_map(original)
    client.response = record
    with tempfile.TemporaryDirectory() as folder:
        resolved = client.resolve(record['id'],Path(folder),cancel)
        assert resolved['hash'] == search_map(record)['hash'] and resolved['url'] == original['versions'][0]['downloadURL']
    client.response = dict(docs=[None,{},record])
    result = client.search('normal name',0,'Relevance',cancel)
    assert len(result['rows']) == 1 and result['unavailable'] == 2
    client.response = dict(docs=[])
    assert not client.search('1990',0,'Relevance',cancel)['rows'] and '/search/text/' in client.urls[-1]
    assert not client.search('no match',0,'Relevance',cancel)['rows']
    for bad in ([],{},dict(docs=None),dict(docs='bad')):
        client.response = bad
        try:client.search('query',0,'Relevance',cancel)
        except DownloadError as exc:assert exc.code == 'search_response'
        else:raise AssertionError('malformed response accepted')
    print('PASS search recovery: ordinary names, map IDs/links, usable published version fallback, bad-row isolation, empty results and distinct response errors')


if __name__ == '__main__':main()
