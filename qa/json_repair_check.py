"""Explicit paid formatting repair of a captured draft in a temporary database.

No production job is queued, retried, or changed. Investigation is replayed
from the captured draft; exactly the new isolated formatting call is live.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sqlite3
import sys
import tempfile
import urllib.request
from pathlib import Path
from unittest.mock import patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.analysis.ai import PiSDKProvider, PiSDKResult
from backend.application.service import ScreeningService


async def run(root: Path, draft: Path, output: Path, project_id: int) -> None:
    connection = sqlite3.connect((root / 'data/screening.sqlite3').resolve().as_uri() + '?mode=ro', uri=True)
    try:
        sections = {key: json.loads(value) for key, value in connection.execute(
            "SELECT key,value FROM settings WHERE key IN ('ai','business')")}
    finally:
        connection.close()
    with urllib.request.urlopen(f'http://127.0.0.1:8766/api/projects/{project_id}', timeout=10) as response:
        original = json.load(response)
    data = json.loads(draft.read_text(encoding='utf-8'))
    content = data['content']
    calls = 0
    formatter_finishes = []
    original_post = httpx.AsyncClient.post

    async def capture_post(client, *args, **kwargs):
        response = await original_post(client, *args, **kwargs)
        text = response.text.replace(sections['ai']['api_key'], '<REDACTED>')
        (output / 'formatter-http-response.json').write_text(text, encoding='utf-8')
        try:
            formatter_finishes.append(response.json()['choices'][0].get('finish_reason'))
        except (ValueError, KeyError, IndexError, TypeError):
            formatter_finishes.append(None)
        return response

    async def replay(self, **kwargs):
        nonlocal calls
        calls += 1
        if calls != 1:
            raise AssertionError('The investigation must not be rerun to fix serialization')
        return PiSDKResult(content=content, usage={
            'model_calls': 1, 'tool_calls': 0, 'input_tokens': 0, 'output_tokens': 0})

    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='screening-json-repair-') as directory:
        temporary = Path(directory)
        source = temporary / 'records.jsonl'
        source.write_text(''.join(json.dumps(record, ensure_ascii=False) + '\n'
                                  for record in original['records']), encoding='utf-8')
        previous_trace_dir = os.environ.get('SCREENING_AI_TRACE_DIR')
        os.environ['SCREENING_AI_TRACE_DIR'] = str(output)
        service = ScreeningService(db_path=temporary / 'isolated.sqlite3', source_path=source,
                                   archive_root=temporary)
        try:
            await service.import_file()
            config = {key: value for key, value in sections['ai'].items()
                      if key in {'base_url', 'model', 'api_key'}}
            service.update_settings({'ai': {**config, 'enabled': True, 'engine': 'pi_sdk'},
                                     'business': sections.get('business', {}),
                                     'import': {'auto_import': False}})
            fresh_id = service.db.execute('SELECT id FROM projects ORDER BY id LIMIT 1').fetchone()[0]
            job_id = service.queue_project(fresh_id)['job_id']
            with patch.object(PiSDKProvider, 'analyze', replay), patch.object(httpx.AsyncClient, 'post', capture_post):
                await service.run_job(job_id)
            job = dict(service.db.execute('SELECT status,attempts,error FROM jobs WHERE id=?', (job_id,)).fetchone())
            detail = service.project_detail(fresh_id)
            judgment = detail.get('judgment')
            summary = {'source_project': project_id, 'isolated_job': job,
                       'investigation_calls': calls, 'judgment_saved': judgment is not None,
                       'formatter_calls': len(formatter_finishes), 'formatter_finish_reasons': formatter_finishes,
                       'decision': (judgment or {}).get('decision')}
            (output / 'repair-summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
            if judgment:
                (output / 'validated-report.json').write_text(json.dumps(judgment, ensure_ascii=False, indent=2), encoding='utf-8')
            print(json.dumps(summary, ensure_ascii=False), flush=True)
            if job['status'] != 'succeeded' or not judgment:
                raise RuntimeError('The captured draft was not repaired and accepted')
        finally:
            await service.aclose()
            if previous_trace_dir is None:
                os.environ.pop('SCREENING_AI_TRACE_DIR', None)
            else:
                os.environ['SCREENING_AI_TRACE_DIR'] = previous_trace_dir


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--call-model', action='store_true', required=True)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--draft', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--project', type=int, default=216)
    args = parser.parse_args()
    asyncio.run(run(args.root, args.draft, args.output, args.project))
