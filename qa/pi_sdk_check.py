"""Full-stack embedded Pi SDK acceptance using a local SSE provider and isolated DB."""
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
BODY = '隔离验收公告：采购破碎机备件，需核对件号及技术清单。'
RELATED = '隔离验收补充公告：采购方接受技术确认后的替代方案。'
SECRET = 'pi-sdk-isolated-test-secret'
REQUESTS: list[dict] = []


class Provider(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_POST(self):
        request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        assert self.path.endswith('/chat/completions')
        assert request.get('stream') is True, 'SDK must exercise real SSE transport'
        assert self.headers.get('Authorization') == f'Bearer {SECRET}'
        tool_names = [tool['function']['name'] for tool in request.get('tools', [])]
        assert set(tool_names) == {'read_project_records', 'read_company_capability', 'read_related_records', 'fetch_project_documents'}, tool_names
        messages = request['messages']
        REQUESTS.append({'tool_names': tool_names, 'tool_results': sum(m['role'] == 'tool' for m in messages)})
        content = None
        calls = None
        if not any(m['role'] == 'tool' for m in messages):
            # The SDK, not a replacement fake Agent, must execute these calls.
            calls = [{'index': index, 'id': f'call_{index}', 'type': 'function', 'function': {'name': name, 'arguments': '{}'}}
                     for index, name in enumerate(name for name in tool_names if name != 'fetch_project_documents')]
        else:
            assert any(RELATED in json.dumps(m, ensure_ascii=False) for m in messages if m['role'] == 'tool'), 'Related source never reached model'
            context = {}
            for m in messages:
                if m['role'] != 'user':
                    continue
                raw = m.get('content', '')
                if isinstance(raw, list):
                    raw = '\n'.join(part.get('text','') for part in raw if isinstance(part,dict))
                if isinstance(raw,str) and raw.startswith('{'):
                    try:
                        context = json.JSONDecoder().raw_decode(raw)[0]
                        break
                    except ValueError:
                        pass
            is_news = context.get('project', {}).get('kind') == 'news'
            content = json.dumps({
                'decision': 'verify' if is_news else 'consider', 'summary': '隔离验收：先核对适配，再比较推进方向。',
                'demand_summary': '采购破碎机备件，待核对具体件号。',
                'reasons': [{'text': '公告写明采购备件，公司能力仍需具体适配确认。',
                             'evidence': [{'record_id': 9102 if is_news else 9101, 'quote': RELATED if is_news else BODY}]}],
                'unknowns': ['具体件号与交期'], 'next_actions': ['取得技术清单'],
                'options': [] if is_news else [
                    {'id': 'direct', 'title': '按原需求核对', 'description': '取得件号后核对供货能力。',
                     'suitable_for': '规格与公司能力吻合时', 'tradeoffs': ['需要规格确认'],
                     'unknowns': ['交期'], 'next_action': '联系采购方取得清单。'},
                    {'id': 'alternative', 'title': '确认条件后替代', 'description': '先确认替代是否适用，再开展技术核对。',
                     'suitable_for': '替代条件得到采购方确认时', 'tradeoffs': ['需要技术审核'],
                     'unknowns': ['适配关系'], 'next_action': '确认替代条件。'},
                ],
            }, ensure_ascii=False)
        events = [dict(choices=[dict(index=0, delta={'role': 'assistant'}, finish_reason=None)])]
        if calls:
            events.append(dict(choices=[dict(index=0, delta={'tool_calls': calls}, finish_reason=None)]))
        else:
            events.append(dict(choices=[dict(index=0, delta={'content': content}, finish_reason=None)]))
        events.append(dict(choices=[dict(index=0, delta={}, finish_reason='tool_calls' if calls else 'stop')]))
        events.append(dict(choices=[], usage={'prompt_tokens': 110, 'completion_tokens': 80, 'total_tokens': 190}))
        payload = ''.join('data: ' + json.dumps({'id': 'qa-pi', 'object': 'chat.completion.chunk', 'created': int(time.time()),
                                             'model': 'qa-pi-model', **event}, ensure_ascii=False) + '\n\n' for event in events)
        payload += 'data: [DONE]\n\n'
        encoded = payload.encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream; charset=utf-8')
        self.send_header('Content-Length', str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


def wait_for(check, timeout=45):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            value = check()
            if value:
                return value
        except httpx.HTTPError:
            pass
        time.sleep(.2)
    raise AssertionError('Pi SDK acceptance timed out')


def main():
    test_data = ROOT / 'data' / f'pi-acceptance-{time.time_ns()}'
    test_data.mkdir(parents=True)
    now = datetime.now(timezone.utc)
    common = dict(source_id='qa', source_name='Pi独立验收', buyer='隔离采购方', published_at=now.isoformat(),
                  deadline_at=(now + timedelta(days=30)).isoformat(), fetched_at=now.isoformat(), current_version=1,
                  parse_status='ok', equipment=['破碎机'], facts=[], inferences=[], canonical_url='https://example.com/pi-qa')
    source = test_data / 'records.jsonl'
    records = [dict(common, id=9101, kind='tender', title='Pi隔离验收：破碎机备件采购', body_text=BODY, content_sha256='pi-v1'),
               dict(common, id=9102, kind='news', title='Pi隔离验收：相关资料', body_text=RELATED, related_record_id=9101, content_sha256='pi-related')]
    source.write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in records), encoding='utf-8')
    env = dict(os.environ, SCREENING_DB_PATH=str(test_data / 'screening.sqlite3'), SCREENING_IMPORT_PATH=str(source),
               SCREENING_AI_ENGINE='pi_sdk', PYTHONUTF8='1')
    for name in ('OPENAI_API_KEY', 'OPENAI_MODEL', 'OPENAI_BASE_URL', 'OPENAI_ENABLED'):
        env.pop(name, None)
    provider = ThreadingHTTPServer(('127.0.0.1', 8870), Provider)
    threading.Thread(target=provider.serve_forever, daemon=True).start()
    log = (test_data / 'server.log').open('w', encoding='utf-8')
    proc = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'backend.main:app', '--host', '127.0.0.1', '--port', '8876'],
                            cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT,
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    try:
        with httpx.Client(base_url='http://127.0.0.1:8876', timeout=15) as client:
            wait_for(lambda: client.get('/api/projects').json().get('total') == 2)
            settings = client.get('/api/settings').json()
            assert settings['ai']['engine'] == 'pi_sdk' and settings['ai']['sdk_ready']
            client.post('/api/projects/9101/link', json={'target_project_id': 9102, 'relation': 'same_project', 'note': '独立验收关联资料'}).raise_for_status()
            settings['business'].update(company_name='独立验收公司', supported_products='破碎机备件', capabilities='按技术清单核对适配')
            settings['ai'].update(base_url='http://127.0.0.1:8870/v1', model='qa-pi-model', api_key=SECRET, enabled=True)
            client.put('/api/settings', json=settings).raise_for_status()
            project = wait_for(lambda: (p if (p := client.get('/api/projects/9101').json()).get('judgment') else None))
            run = project['agent_run']
            assert run['engine'] == 'pi_sdk' and run['status'] == 'succeeded', run
            assert run['usage']['model_calls'] >= 2 and run['usage']['tool_calls'] >= 3, run
            assert {s.get('tool') for s in run['steps'] if s['stage'] == 'tool_end'} >= {'read_project_records', 'read_company_capability', 'read_related_records'}
            assert len(project['judgment']['options']) == 2
            news = wait_for(lambda: (p if (p := client.get('/api/projects/9102').json()).get('judgment') else None))
            assert news['judgment']['decision'] == 'verify'
            assert SECRET not in json.dumps(project) and SECRET not in client.get('/api/settings').text
            choice = dict(status='following', owner='Pi验收负责人', next_action='取得技术清单', note='独立SDK验收',
                          selected_option_id='direct', judgment_id=project['judgment']['id'])
            client.post('/api/projects/9101/decision', json=choice).raise_for_status()
            assert client.get('/api/projects/9101').json()['decision']['selected_option_id'] == 'direct'
            node = os.getenv('SCREENING_NODE_PATH') or str(Path.home() / '.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe')
            subprocess.run([node, str(ROOT / 'qa/pi_sdk_browser.cjs'), 'http://127.0.0.1:8876', str(test_data)],
                           cwd=ROOT, env=env, check=True, timeout=60)
            print(json.dumps({'passed': True, 'engine': run['engine'], 'model_calls': run['usage']['model_calls'],
                              'tool_calls': run['usage']['tool_calls'], 'tool_results_observed': max(r['tool_results'] for r in REQUESTS),
                              'test_data': str(test_data), 'production_data_untouched': True}, ensure_ascii=False))
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        provider.shutdown()
        provider.server_close()
        log.close()
        assert SECRET not in (test_data / 'server.log').read_text(encoding='utf-8')


if __name__ == '__main__':
    main()
