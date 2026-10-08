"""Isolated HTTP and browser acceptance; never writes collector or production DB."""
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


class Provider(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_POST(self):
        request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        context = json.loads(request['messages'][-1]['content'])
        project = context['project']
        record = project['records'][0]
        news = project['kind'] == 'news'
        result = {
            'decision': 'verify' if news else 'consider',
            'summary': '验收测试：先确认需求。' if news else '验收测试：方向吻合，需核对产品适配。',
            'demand_summary': record['body_text'][:100],
            'reasons': [{'text': '原文描述了需要核实的业务信息。', 'evidence': [{'record_id': record['record_id'], 'quote': record['body_text'][:45]}]}],
            'unknowns': ['具体供货能力待核对'],
            'next_actions': ['取得技术清单'],
            'options': [] if news else [
                {'id': 'direct', 'title': '验收方案：按需求核对', 'description': '先按技术清单核对供货能力。', 'suitable_for': '对应产品能力已确认时', 'tradeoffs': ['需核对具体规格'], 'unknowns': ['最终交期'], 'next_action': '取得技术清单并核对件号。'},
                {'id': 'alternative', 'title': '验收方案：条件性替代', 'description': '确认接受替代后，核对兼容关系。', 'suitable_for': '采购方接受替代时', 'tradeoffs': ['需要技术确认'], 'unknowns': ['替代是否可接受'], 'next_action': '确认替代条件。'},
            ],
        }
        payload = json.dumps({'choices': [{'message': {'content': json.dumps(result, ensure_ascii=False)}}]}, ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def wait_for(check, timeout=30):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            value = check()
            if value:
                return value
        except (httpx.HTTPError, ConnectionError):
            pass
        time.sleep(.2)
    raise AssertionError('Acceptance timed out')


def main():
    test_data = ROOT / 'data' / f'acceptance-{time.time_ns()}'
    test_data.mkdir(parents=True)
    source = test_data / 'records.jsonl'
    now = datetime.now(timezone.utc)
    common = dict(source_id='qa', source_name='独立验收数据', buyer='验收采购方', published_at=now.isoformat(),
                  deadline_at=(now + timedelta(days=30)).isoformat(), fetched_at=now.isoformat(), current_version=1,
                  parse_status='ok', equipment=['破碎机'], facts=[], inferences=[], canonical_url='https://example.com/qa')
    records = [dict(common, id=9001, kind='tender', title='验收项目：破碎机备件采购', body_text='验收公告：本项目采购破碎机备件，需要核对具体件号及技术清单。', content_sha256='qa-v1'),
               dict(common, id=9002, kind='news', title='验收行业线索', body_text='验收新闻：矿区项目有新的行业动态，尚未发布具体设备采购计划。', deadline_at=None, content_sha256='qa-news')]

    def write_records():
        source.write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in records), encoding='utf-8')

    write_records()
    provider = ThreadingHTTPServer(('127.0.0.1', 8770), Provider)
    threading.Thread(target=provider.serve_forever, daemon=True).start()
    env = dict(os.environ, SCREENING_DB_PATH=str(test_data / 'screening.sqlite3'), SCREENING_IMPORT_PATH=str(source), PYTHONUTF8='1')
    env.pop('OPENAI_API_KEY', None)
    log = (test_data / 'server.log').open('w', encoding='utf-8')
    proc = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'backend.main:app', '--host', '127.0.0.1', '--port', '8767'], cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    checks = []
    try:
        with httpx.Client(base_url='http://127.0.0.1:8767', timeout=15) as client:
            wait_for(lambda: client.get('/api/health').status_code == 200)
            wait_for(lambda: client.get('/api/projects').json().get('total') == 2)
            assert client.get('/settings').status_code == 200, 'SPA route refresh failed'
            before = client.get('/api/projects/9001').json()
            assert before['judgment'] is None and before['ai_status'] == 'not_configured'
            repeat = client.post('/api/import').json()
            assert repeat['inserted'] == 0 and repeat['unchanged'] == 2
            checks += ['真实HTTP导入及重复导入', '未配置模型不伪造方案', 'SPA深链接刷新']
            settings = client.get('/api/settings').json()
            settings['business'].update(company_name='验收测试公司', scope_description='仅用于验收的备件业务', supported_products='破碎机备件', capabilities='技术清单确认后方可供货')
            settings['ai'].update(base_url='http://127.0.0.1:8770/v1', model='isolated-acceptance-provider', api_key='qa-not-a-real-secret', enabled=True)
            response = client.put('/api/settings', json=settings)
            response.raise_for_status()
            assert 'qa-not-a-real-secret' not in response.text and 'api_key"' not in response.text.replace('has_api_key"', '')
            wait_for(lambda: client.get('/api/projects/9001').json().get('judgment'))
            wait_for(lambda: client.get('/api/projects/9002').json().get('judgment'))
            first = client.get('/api/projects/9001').json()
            assert len(first['judgment']['options']) == 2
            assert client.get('/api/projects/9002').json()['judgment']['options'] == []
            old_id = first['judgment']['id']
            choice = {'status': 'following', 'owner': '验收同事', 'next_action': '核对原需求', 'note': '隔离测试', 'selected_option_id': 'direct', 'judgment_id': old_id}
            client.post('/api/projects/9001/decision', json=choice).raise_for_status()
            records[0]['body_text'] += ' 本次公告补充：请再次核对交付要求。'
            records[0]['current_version'] = 2
            records[0]['content_sha256'] = 'qa-v2'
            write_records()
            client.post('/api/import').raise_for_status()
            latest = wait_for(lambda: (p if (p := client.get('/api/projects/9001').json())['judgment']['id'] != old_id and not p['judgment']['stale'] else None))
            assert latest['decision']['owner'] == '验收同事' and latest['needs_review']
            assert client.post('/api/projects/9001/decision', json=choice).status_code == 409
            checks += ['兼容接口真实HTTP调用与证据校验', '新闻无供货方案', '变更重判保留人工选择', '旧客户端方案版本拒绝', '密钥不回传']
            node = os.getenv('SCREENING_NODE', str(Path.home() / '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe'))
            subprocess.run([node, str(ROOT / 'qa' / 'browser_check.cjs'), 'http://127.0.0.1:8767', str(test_data)], cwd=ROOT, check=True)
            checks += ['浏览器方案选择/人工决定持久化', '表单不被刷新覆盖', '配置关键词换行保存', '手机布局']
            print(json.dumps({'passed': checks, 'test_data': str(test_data), 'production_data_untouched': True}, ensure_ascii=False))
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        provider.shutdown()
        log.close()


if __name__ == '__main__':
    main()
