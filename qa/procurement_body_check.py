"""Verify the real project #166 announcement through an isolated HTTP service.

No production writes, external requests or model calls are made.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import httpx

ROOT = Path(__file__).resolve().parents[1]
PORT = 8894
sys.path.insert(0, str(ROOT))


def serve(output: Path, fixture: Path) -> None:
    import uvicorn
    import backend.application.service as service_module
    from backend.main import create_app
    from backend.application.service import ScreeningService

    async def empty_fetch(record):
        return []

    service_module.fetch_record_documents = empty_fetch
    record = json.loads(fixture.read_text(encoding='utf-8'))['records'][0]
    source = output / 'records.jsonl'
    source.write_text(json.dumps(record, ensure_ascii=False), encoding='utf-8')
    service = ScreeningService(db_path=output / 'screening.sqlite3', source_path=source,
                               archive_root=output, provider=object())
    asyncio.run(service.import_file())
    service.update_settings({'import': {'auto_import': False}})
    try:
        uvicorn.run(create_app(service), host='127.0.0.1', port=PORT)
    finally:
        service.close()


def verify(procurement: dict, body: str) -> None:
    pump = next(item for item in procurement['items'] if item['name'] == '煤矿井下压裂泵')
    assert pump['quantity'] == '2' and pump['unit'] == '套'
    assert pump['source_kind'] == 'announcement'
    assert pump['record_id'] == 166 and pump['page'] == 0
    assert pump['quote'] in body
    assert '30' in procurement['delivery_period']
    assert procurement['delivery_location'] == '甲方指定地点'
    assert pump['delivery_period'] == procurement['delivery_period']
    assert pump['delivery_location'] == procurement['delivery_location']
    assert procurement['attachment_status'] == 'restricted'
    assert procurement['status'] == 'partial'
    assert not pump['specification'] and not pump['material']


def main() -> None:
    fixture = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / 'data/project-166-diagnosis.json'
    output = ROOT / 'data' / f'procurement-body-acceptance-{time.time_ns()}'
    output.mkdir(parents=True)
    env = dict(os.environ, SCREENING_DB_PATH=str(output / 'unused.sqlite3'),
               SCREENING_IMPORT_PATH=str(output / 'records.jsonl'), OPENAI_API_KEY='',
               OPENAI_MODEL='', PYTHONUTF8='1')
    with (output / 'server.log').open('w', encoding='utf-8') as log:
        process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), '--serve', str(output), str(fixture)],
                                   cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT,
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
        try:
            with httpx.Client(base_url=f'http://127.0.0.1:{PORT}', timeout=10, trust_env=False) as client:
                deadline = time.monotonic() + 40
                while True:
                    try:
                        if client.get('/api/health').status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    if process.poll() is not None or time.monotonic() > deadline:
                        raise AssertionError(f'QA service failed: {output / "server.log"}')
                    time.sleep(.2)
                before = client.get('/api/projects/166')
                before.raise_for_status()
                detail = before.json()
                body = detail['records'][0]['body_text']
                verify(detail['procurement'], body)
                refresh = client.post('/api/projects/166/documents/refresh')
                refresh.raise_for_status()
                verify(refresh.json()['procurement'], body)
                detail = client.get('/api/projects/166').json()
                verify(detail['procurement'], body)
                (output / 'detail.json').write_text(json.dumps(detail, ensure_ascii=False, indent=2), encoding='utf-8')
                assert not client.get('/api/status').json()['jobs']
            print(json.dumps({'passed': True, 'before_and_after_empty_refresh': True,
                              'production_model_calls': 0, 'output': str(output)}, ensure_ascii=False))
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)


if __name__ == '__main__':
    serve(Path(sys.argv[2]), Path(sys.argv[3])) if '--serve' in sys.argv else main()
