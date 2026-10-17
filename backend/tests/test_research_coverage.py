from __future__ import annotations

import asyncio
import json

import httpx

from backend.storage.db import connect
from backend.research.project_research import ResearchSession


def test_empty_search_records_actual_portals_and_uncovered_project_origin(tmp_path):
    db = connect(tmp_path / 'research.sqlite3')
    db.execute("INSERT INTO projects(id,created_at,updated_at) VALUES (1,'x','x')")
    db.execute("INSERT INTO jobs(id,project_id,status,created_at,updated_at) VALUES (1,1,'running','x','x')")
    db.commit()
    path = tmp_path / 'sources.json'
    path.write_text(json.dumps({'sources': [{
        'id': 'another', 'name': '其他平台', 'base_url': 'https://other.test',
        'search': {'adapter': 'post_json_list', 'url': 'https://other.test/search', 'items_path': 'items'},
    }]}), encoding='utf-8')
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json={'items': []})))
    session = ResearchSession(db, project_id=1, job_id=1,
                              records=[{'original_url': 'https://cg.ccteg.cn/cms/channel/ywgg1hw/89035.htm'}],
                              sources_path=path, client=client)
    try:
        result = asyncio.run(session.execute('search_project_sources', {'query': 'GHY-26697', 'purpose': '补查规格'}))
        assert result['status'] == 'no_results'
        assert result['coverage']['source_ids'] == ['another']
        assert result['coverage']['uncovered_origins'] == ['cg.ccteg.cn']
        event = session.investigation()['events'][-1]
        assert '其他平台' in event['message']
        assert 'cg.ccteg.cn' in event['message']
        assert '仅限' in event['message']
    finally:
        asyncio.run(client.aclose())
        db.close()


def test_ccteg_official_html_search_uses_get_and_maps_result_offset_to_page(tmp_path):
    db = connect(tmp_path / 'research.sqlite3')
    db.execute("INSERT INTO projects(id,created_at,updated_at) VALUES (1,'x','x')")
    db.execute("INSERT INTO jobs(id,project_id,status,created_at,updated_at) VALUES (1,1,'running','x','x')")
    db.commit()
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.path.endswith('/89035.htm'):
            return httpx.Response(200, headers={'content-type': 'text/html'},
                                  text='<html><body>GHY-26697 pump tender'
                                       '<a href="/company">上海汇招信息技术有限公司</a>'
                                       '<a href="/cms/channel/ywgg1hw/other.htm">另一设备采购项目公告</a>'
                                       '</body></html>')
        return httpx.Response(200, headers={'content-type': 'text/html;charset=UTF-8'},
                              text='<html><body>' + ''.join(f'<a href="/nav/{n}">Navigation</a>' for n in range(25))
                                   + '<a href="/cms/channel/ywgg1hw/89035.htm" '
                                   'title="GHY-26697 pump tender">GHY-26697</a></body></html>')

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    session = ResearchSession(db, project_id=1, job_id=1,
                              records=[{'original_url': 'https://cg.ccteg.cn/cms/channel/ywgg1hw/89035.htm'}],
                              client=client)
    try:
        result = asyncio.run(session.execute('search_project_sources', {
            'query': 'GHY-26697', 'purpose': '补查规格', 'source_ids': ['ccteg_materials'],
            'limit': 10, 'offset': 10,
        }))
        assert result['status'] == 'success'
        assert result['coverage']['uncovered_origins'] == []
        assert result['coverage']['list_scan'] is False
        assert result['sources'][0]['url'] == 'https://cg.ccteg.cn/cms/channel/ywgg1hw/89035.htm'
        assert result['sources'][0]['title'] == 'GHY-26697 pump tender'
        assert result['sources'][0]['relation_status'] == 'unconfirmed'
        request = requests[0]
        assert request.method == 'GET'
        assert request.url.params['kwd'] == 'GHY-26697'
        assert request.url.params['pageSize'] == '10'
        assert request.url.params['pageNo'] == '2'
        assert '241' in request.url.params['channelIds']
        assert session.evidence_sources() == []
        read = asyncio.run(session.execute('read_research_source', {
            'source_ref': result['sources'][0]['source_ref'], 'purpose': '核对公开正文',
        }))
        assert read['status'] == 'read'
        discovered = session.investigation()['sources']
        assert not any(source['url'].endswith('/company') for source in discovered)
        related = next(source for source in discovered if source['url'].endswith('/other.htm'))
        assert related['relation_status'] == 'unconfirmed'
    finally:
        asyncio.run(client.aclose())
        db.close()
