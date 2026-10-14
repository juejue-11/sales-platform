"""Real Pi SDK + SSE + duplex attachment tool, with isolated data and no paid model."""
from __future__ import annotations
import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
SECRET = 'isolated-procurement-test-only'
REQUESTS = []


class Provider(BaseHTTPRequestHandler):
    def log_message(self, *_): pass

    def do_POST(self):
        request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        assert self.headers.get('Authorization') == 'Bearer ' + SECRET
        assert request.get('stream') is True
        names = {t['function']['name'] for t in request['tools']}
        assert 'fetch_project_documents' in names, names
        messages = request['messages']
        documents = []
        for message in messages:
            if message['role'] == 'tool':
                try:
                    value = json.loads(message['content'])
                    documents += value.get('documents', [])
                except (ValueError, AttributeError): pass
        REQUESTS.append({'tools':sorted(names),'documents_seen':len(documents)})
        calls = None
        content = None
        if not documents:
            calls = [{'index':0,'id':'material-fetch','type':'function',
                      'function':{'name':'fetch_project_documents','arguments':'{"record_id":9101}'}}]
        else:
            document = next(d for d in documents if d.get('items'))
            item = document['items'][0]
            assert item['name'] == '普碳中板'
            assert '三日内供完' in item['delivery_period']
            content = json.dumps({
                'decision':'consider','summary':'清单已核对，按钢材规格与交付条件比较方案。',
                'demand_summary':'143项钢材采购；交货地本溪市，按通知量三日内供完。',
                'reasons':[{'text':'PDF列出普碳中板4mm、Q235B、5.0t，并写明交付要求。',
                            'evidence':[{'record_id':9101,'document_id':document['id'],'quote':item['quote']}]}],
                'unknowns':['采购方案.doc尚未解析'], 'next_actions':['核对供货能力和采购方案原文件'],
                'options':[{'id':'stock','title':'库存供货','description':'核对材料清单与库存后安排供货。',
                            'suitable_for':'库存和交货能力符合要求','tradeoffs':['需核对全部规格'],
                            'unknowns':['库存'],'next_action':'确认库存与运输'},
                           {'id':'supply','title':'组织货源','description':'逐项组织供应并确认三日内交付能力。',
                            'suitable_for':'供货计划得到确认','tradeoffs':['需要协调货源'],
                            'unknowns':['备货周期'],'next_action':'确认供应及交货计划'}],
            }, ensure_ascii=False)
        events = [{'choices':[{'index':0,'delta':{'role':'assistant'},'finish_reason':None}]},
                  {'choices':[{'index':0,'delta':{'tool_calls':calls} if calls else {'content':content},'finish_reason':None}]},
                  {'choices':[{'index':0,'delta':{},'finish_reason':'tool_calls' if calls else 'stop'}]},
                  {'choices':[],'usage':{'prompt_tokens':100,'completion_tokens':100,'total_tokens':200}}]
        encoded = (''.join('data: '+json.dumps({'id':'qa-procurement','object':'chat.completion.chunk',
                                              'created':int(time.time()),'model':'qa-model',**event},ensure_ascii=False)+'\n\n'
                           for event in events)+'data: [DONE]\n\n').encode()
        self.send_response(200)
        self.send_header('Content-Type','text/event-stream; charset=utf-8')
        self.send_header('Content-Length',str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


def wait_for(check, timeout=45):
    until = time.monotonic()+timeout
    while time.monotonic()<until:
        try:
            value = check()
            if value: return value
        except httpx.HTTPError: pass
        time.sleep(.2)
    raise AssertionError('procurement SDK acceptance timed out')


def main():
    fixture = ROOT/'data/procurement-qa/174/material-list.pdf'
    assert fixture.exists(), 'Use the publicly downloaded record 174 PDF fixture.'
    folder = ROOT/'data'/f'procurement-sdk-{time.time_ns()}'
    folder.mkdir(parents=True)
    now = datetime.now(timezone.utc)
    record = dict(id=9101,source_id='qa',source_name='隔离采购验收',kind='tender',title='隔离验收：钢材采购招标公告',
                  buyer='隔离采购方',published_at=now.isoformat(),deadline_at=(now+timedelta(days=30)).isoformat(),
                  fetched_at=now.isoformat(),current_version=1,parse_status='ok',equipment=[],facts=[],inferences=[],
                  canonical_url='https://example.com/notice.html',body_text='采购范围详见附件《物料清单附件.pdf》。',
                  content_sha256='notice-v1')
    source = folder/'records.jsonl'
    source.write_text(json.dumps(record,ensure_ascii=False)+'\n',encoding='utf-8')
    env = dict(os.environ,SCREENING_DB_PATH=str(folder/'screening.sqlite3'),SCREENING_IMPORT_PATH=str(source),PYTHONUTF8='1')
    for name in ('OPENAI_API_KEY','OPENAI_MODEL','OPENAI_BASE_URL','OPENAI_ENABLED'): env.pop(name,None)
    # Patch only the public-download seam in this child process. Parser, DB, API, bridge, Agent and SSE are real.
    launcher = '''import os
from pathlib import Path
from backend.documents.public_documents import parse_document
import backend.application.service as service
import backend.documents.public_documents as documents
async def fixture_fetch(record):
    return [parse_document(Path(os.environ['PROCUREMENT_QA_FIXTURE']).read_bytes(),record_id=int(record.get('id') or record.get('record_id')),url='https://example.com/material.pdf',name='物料清单附件.pdf')]
service.fetch_record_documents = fixture_fetch
documents.fetch_record_documents = fixture_fetch
import uvicorn
uvicorn.run('backend.main:app',host='127.0.0.1',port=8886)
'''
    env['PROCUREMENT_QA_FIXTURE'] = str(fixture)
    provider = ThreadingHTTPServer(('127.0.0.1',8880),Provider)
    threading.Thread(target=provider.serve_forever,daemon=True).start()
    log = (folder/'server.log').open('w',encoding='utf-8')
    process = subprocess.Popen([sys.executable,'-c',launcher],cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT,
                               creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    try:
        with httpx.Client(base_url='http://127.0.0.1:8886',timeout=25) as client:
            wait_for(lambda:client.get('/api/projects').json().get('total')==1)
            before = client.get('/api/projects/9101').json()
            assert not (before.get('procurement') or {}).get('items')
            settings = client.get('/api/settings').json()
            settings['business'].update(company_name='隔离钢材供货方',supported_products='钢材',capabilities='按清单核对材料与交付')
            settings['ai'].update(base_url='http://127.0.0.1:8880/v1',model='qa-model',api_key=SECRET,enabled=True)
            client.put('/api/settings',json=settings).raise_for_status()
            project = wait_for(lambda:(p if (p:=client.get('/api/projects/9101').json()).get('judgment') else None))
            assert project['agent_run']['status']=='succeeded',project['agent_run']
            assert any(s.get('tool')=='fetch_project_documents' and s['stage']=='tool_end' for s in project['agent_run']['steps'])
            assert len(project['procurement']['items'])==143
            assert '三日内供完' in project['procurement']['delivery_period']
            evidence = project['judgment']['reasons'][0]['evidence'][0]
            assert evidence.get('document_id'),evidence
            assert '普碳中板' in evidence['quote']
            assert evidence['document_name']=='物料清单附件.pdf'
            assert evidence['page']==1
            assert evidence['source_url']=='https://example.com/material.pdf'
            assert evidence['document_sha256']
            assert project['records'][0]['body_text']==record['body_text']
            assert SECRET not in json.dumps(project)
            print(json.dumps({'passed':True,'real_sdk':True,'duplex_tool':True,'material_items':143,
                              'citation_verified':True,'original_notice_preserved':True,'test_data':str(folder)},ensure_ascii=False))
    finally:
        process.terminate()
        try: process.wait(timeout=10)
        except subprocess.TimeoutExpired: process.kill(); process.wait()
        provider.shutdown();provider.server_close();log.close()
        assert SECRET not in (folder/'server.log').read_text(encoding='utf-8')


if __name__=='__main__': main()
