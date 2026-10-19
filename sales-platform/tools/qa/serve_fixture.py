"""Serve the real API against public #224 fixture data, with model calls off.

Run from the repository root. Requires SCREENING_DB_PATH to point outside the
production data directory, so this smoke server cannot alter user progress.
"""
from __future__ import annotations

import asyncio
import json
import os
import runpy
from pathlib import Path

import uvicorn

from backend.application.service import ScreeningService
from backend.procurement.reading import normalize_reading


class FixtureProvider:
    supports_procurement_reading = True


def main() -> None:
    root = Path(__file__).resolve().parents[2]
    db_path = Path(os.environ['SCREENING_DB_PATH']).resolve()
    if db_path.is_relative_to(root / 'data'):
        raise ValueError('QA requires an isolated database outside production data')
    db_path.parent.mkdir(parents=True, exist_ok=True)
    example = runpy.run_path(str(root / 'backend/tests/test_notice_224_workbench.py'))['example']
    notice, payload = example()
    source = db_path.parent / 'records.jsonl'
    source.write_text(json.dumps(notice, ensure_ascii=False), encoding='utf-8')
    service = ScreeningService(db_path=db_path, source_path=source,
                               archive_root=db_path.parent, provider=FixtureProvider())
    service.update_settings({'ai': {'enabled': False}, 'import': {'auto_import': False}})
    asyncio.run(service.import_file())
    material = service.reading_store.reading_material(224)
    reading = normalize_reading(payload, material)
    service.reading_store.save_procurement_reading(224, reading, material_version=material['material_version'], config={})
    from backend.main import create_app
    app = create_app(service)

    @app.get('/api/qa/environment')
    def qa_environment():
        return {'fixture': 'notice-224', 'isolated': True}

    # The production app already registered its SPA catch-all route.
    app.router.routes.insert(0, app.router.routes.pop())
    uvicorn.run(app, host='127.0.0.1', port=int(os.environ.get('SCREENING_QA_PORT', '8877')))


if __name__ == '__main__':
    main()
