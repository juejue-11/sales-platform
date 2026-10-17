"""Serve an isolated platform and real collector against local fixture pages."""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import uvicorn
from fastapi.responses import HTMLResponse, JSONResponse


def main() -> None:
    root = Path(__file__).resolve().parents[2]
    qa_root = Path(os.environ["SCREENING_QA_ROOT"]).resolve()
    production = Path(__file__).resolve().parents[2] / "data"
    if qa_root == production or qa_root.is_relative_to(production):
        raise ValueError("QA requires a directory outside business data")
    qa_root.mkdir(parents=True, exist_ok=True)
    collector_root = qa_root / "collector"
    collector_source = Path(os.environ["SCREENING_QA_COLLECTOR_SOURCE"]).resolve()
    if collector_root.exists():
        raise ValueError("Use a fresh QA directory for each run")
    shutil.copytree(collector_source / "collector", collector_root / "collector",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    port = int(os.environ.get("SCREENING_QA_PORT", "8879"))
    base = f"http://127.0.0.1:{port}"
    streaming = os.environ.get("SCREENING_QA_STREAM") == "1"
    publication_date = datetime.now(timezone(timedelta(hours=8))).date()
    submission_date = publication_date + timedelta(days=7)
    slow_release = asyncio.Event()
    good = {"id": "qa_public", "name": "验收公开采购来源", "category": "procurement",
            "entry_url": f"{base}/fixture/list", "adapter": "html_list",
            "item_selector": "li", "link_selector": "a", "content_selector": "article",
            "allowed_hosts": ["127.0.0.1"], "enabled": True, "access_status": "public",
            "access_note": "本机隔离验收来源", "frequency_hours": 24}
    broken = {**good, "id": "qa_blocked", "name": "验收访问受限来源",
              "entry_url": f"{base}/fixture/blocked", "enabled": False,
              "access_note": "模拟403，用于验证部分失败仍导入"}
    (collector_root / "sources.json").write_text(json.dumps(
        {"timeout_seconds": 60 if streaming else 5, "delay_seconds": 0, "sources": [good, broken]},
        ensure_ascii=False), encoding="utf-8")
    source = collector_root / "data/export/records.jsonl"
    os.environ.update({
        "SCREENING_DB_PATH": str(qa_root / "screening.sqlite3"),
        "SCREENING_IMPORT_PATH": str(source), "SCREENING_ARCHIVE_ROOT": str(collector_root / "data"),
        "SCREENING_COLLECTOR_ROOT": str(collector_root),
        "SCREENING_COLLECTOR_PYTHON": os.environ.get("SCREENING_QA_COLLECTOR_PYTHON", sys.executable),
        "COLLECTOR_SCHEDULER_DISABLED": "1", "OPENAI_API_KEY": "", "OPENAI_MODEL": "",
        "OPENAI_ENABLED": "0", "NO_PROXY": "127.0.0.1,localhost",
    })
    from backend.main import app
    app.state.service.update_settings({"ai": {"enabled": False}, "import": {"auto_import": False}})
    first_fixture_route = len(app.router.routes)

    @app.get("/api/qa/environment")
    async def environment():
        return {"fixture": "collection-streaming" if streaming else "collection-integration", "isolated": True}

    @app.get("/fixture/list")
    async def listing():
        later = '<li><a href="/fixture/slow-notice">矿用破碎设备采购公告</a></li>' if streaming else ''
        return HTMLResponse('<ul><li><a href="/fixture/notice">矿用输送设备采购公告</a></li>' + later + '</ul>')

    @app.get("/fixture/notice")
    async def notice():
        await asyncio.sleep(1.5)
        return HTMLResponse('<article><h1>矿用输送设备采购公告</h1><p>采购单位：验收矿山公司</p>'
                            f'<p>发布日期：{publication_date.isoformat()}</p>'
                            '<p>公告编号：QA-20261016</p><p>采购矿用输送机2台。</p>'
                            f'<p>投标文件递交截止时间：{submission_date.isoformat()} 15:00</p></article>')

    @app.get("/fixture/blocked")
    async def blocked():
        return JSONResponse({"error": "fixture access denied"}, status_code=403)

    @app.get("/fixture/slow-notice")
    async def slow_notice():
        if not streaming:
            return JSONResponse({"error": "stream fixture disabled"}, status_code=404)
        await asyncio.wait_for(slow_release.wait(), timeout=90)
        return HTMLResponse('<article><h1>矿用破碎设备采购公告</h1><p>采购单位：第二验收矿山公司</p>'
                            f'<p>发布日期：{publication_date.isoformat()}</p>'
                            '<p>公告编号：QA-20261016-SECOND</p><p>采购矿用破碎机1台。</p>'
                            f'<p>投标文件递交截止时间：{submission_date.isoformat()} 15:00</p></article>')

    @app.post("/api/qa/release-slow")
    async def release_slow():
        if not streaming:
            return JSONResponse({"error": "stream fixture disabled"}, status_code=404)
        slow_release.set()
        return {"released": True}

    # Register fixture endpoints before the production SPA fallback.
    routes = app.router.routes
    routes[:] = routes[first_fixture_route:] + routes[:first_fixture_route]
    uvicorn.run(app, host="127.0.0.1", port=port)


if __name__ == "__main__":
    main()
