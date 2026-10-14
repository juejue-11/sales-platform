"""Isolated HTTP/browser acceptance for market default; no production model calls."""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]


def serve():
    sys.path.insert(0, str(ROOT))
    from backend.main import create_app
    from backend.application.service import ScreeningService
    import uvicorn

    class LocalProvider:
        async def complete(self, **kwargs):
            context = json.loads(kwargs['messages'][1]['content'])
            record = context['project']['records'][0]
            result = {
                'decision': 'consider', 'summary': '正文明确采购破碎机备件，值得关注；型号和参与条件仍需核实。',
                'demand_summary': '当前采购破碎机备件，具体件号尚待取得技术清单。',
                'reasons': [{'text': '正文说明了备件需求。', 'evidence': [{'record_id': record['record_id'], 'quote': '采购破碎机备件'}]}],
                'unknowns': ['具体件号和参与条件'], 'next_actions': ['取得技术清单'],
                'options': [{'id': 'verify', 'title': '核实技术清单与参与条件', 'description': '先确认件号和授权要求，再决定参与方式。',
                             'suitable_for': '希望调查该项目的设备经销商或生产商', 'tradeoffs': ['需要采购方确认'],
                             'unknowns': ['具体型号'], 'next_action': '联系采购方取得技术清单'}],
            }
            return {'choices': [{'message': {'content': json.dumps(result, ensure_ascii=False)}}]}

    provider = LocalProvider()
    service = ScreeningService(provider=provider)
    service.update_settings({'import': {'auto_import': False}})
    asyncio.run(service.import_file())
    service.update_settings({'ai': {'enabled': True, 'model': 'isolated-qa', 'base_url': 'https://mock.local/v1', 'api_key': 'local-fixture-only'}})
    for project_id in (9101, 9102):
        asyncio.run(service.run_job(service.queue_project(project_id)['job_id']))
    historical = service.project_detail(9102)['judgment']
    service.save_decision(9102, {'status': 'following', 'owner': '历史负责人', 'selected_option_id': 'verify', 'judgment_id': historical['id']})
    with service.db:
        service.db.execute('UPDATE judgments SET analysis_meta_json=NULL WHERE project_id=9102')
    service.close()
    service = ScreeningService(provider=provider)
    assert service.project_detail(9102)['judgment']['stale']
    assert service.db.execute("SELECT count(*) FROM jobs WHERE status IN ('queued','running')").fetchone()[0] == 0
    uvicorn.run(create_app(service), host='127.0.0.1', port=8892)


def main():
    output = ROOT / 'data' / f'market-analysis-acceptance-{time.time_ns()}'
    output.mkdir(parents=True)
    source = output / 'records.jsonl'
    records = [dict(id=i, source_id='qa', kind='tender', title=title, project_name=title, buyer='隔离验收采购方',
                    body_text='本项目采购破碎机备件，具体件号需核对技术清单。', equipment=['破碎机'],
                    deadline_at='2099-12-31 09:00', published_at='2026-10-12', current_version=1,
                    content_sha256=f'qa-{i}', canonical_url=f'https://example.com/qa/{i}')
               for i, title in ((9101, '空资料市场分析验收'), (9102, '旧判断兼容验收'))]
    source.write_text(''.join(json.dumps(record, ensure_ascii=False) + '\n' for record in records), encoding='utf-8')
    env = dict(os.environ, SCREENING_DB_PATH=str(output / 'screening.sqlite3'), SCREENING_IMPORT_PATH=str(source),
               SCREENING_AI_ENGINE='single_call', OPENAI_API_KEY='', OPENAI_MODEL='', PYTHONUTF8='1')
    with (output / 'server.log').open('w', encoding='utf-8') as log:
        process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--serve'], cwd=ROOT, env=env,
                                   stdout=log, stderr=subprocess.STDOUT,
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        try:
            with httpx.Client(base_url='http://127.0.0.1:8892', timeout=15) as client:
                deadline = time.monotonic() + 30
                while True:
                    try:
                        if client.get('/api/health').status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    if process.poll() is not None or time.monotonic() > deadline:
                        raise AssertionError('QA server did not start; see ' + str(output / 'server.log'))
                    time.sleep(.2)
                current = client.get('/api/projects/9101').json()
                assert current['judgment']['decision'] == 'consider' and current['judgment']['options']
                assert current['analysis_meta']['company_match'] == 'not_evaluated'
                assert client.get('/api/settings').json()['analysis']['company_profile_present'] is False
                legacy = client.get('/api/projects/9102').json()
                assert legacy['judgment']['stale'] and legacy['decision']['owner'] == '历史负责人'
                rejected = client.post('/api/projects/9102/decision', json={'status': 'following', 'selected_option_id': 'verify', 'judgment_id': legacy['judgment']['id']})
                assert rejected.status_code == 409
            node = os.environ['SCREENING_NODE_PATH']
            subprocess.run([node, str(ROOT / 'qa/market_analysis_browser.cjs'), 'http://127.0.0.1:8892', str(output)], cwd=ROOT, env=env, check=True)
            print(json.dumps({'http': 'passed', 'output': str(output), 'production_model_calls': 0}, ensure_ascii=False))
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)


if __name__ == '__main__':
    serve() if '--serve' in sys.argv else main()
