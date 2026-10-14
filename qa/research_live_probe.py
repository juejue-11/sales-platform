"""Bounded public-source probe; uses a new database and never invokes a model."""
from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.storage.db import connect
from backend.research.project_research import ResearchSession, read_saved_source


async def main() -> None:
    output = ROOT / "data" / f"research-live-probe-{time.time_ns()}"
    output.mkdir(parents=True)
    db = connect(output / "probe.sqlite3")
    with db:
        db.execute("INSERT INTO projects(id,created_at,updated_at) VALUES (1,'probe','probe')")
        db.execute("INSERT INTO jobs(id,project_id,status,created_at,updated_at) VALUES (1,1,'running','probe','probe')")
    session = ResearchSession(db, project_id=1, job_id=1, records=[],
                              sources_path=ROOT / "backend/research_sources.json")
    result = {"production_model_calls": 0, "output": str(output), "sources": []}
    try:
        for source_id in (sys.argv[1:] or ("angang", "jisco", "ccteg_materials")):
            found = await session.execute("search_project_sources", {
                "purpose": "验证已登记公开来源的发现能力",
                "query": "GHY-26697" if source_id == "ccteg_materials" else "破碎机",
                "source_ids": [source_id], "limit": 2,
            })
            item = {"source_id": source_id, "status": found["status"], "count": len(found.get("sources", [])),
                    "coverage": found.get("coverage"), "errors": found.get("errors", [])}
            if source_id == "ccteg_materials":
                assert found["status"] == "success" and found.get("sources"), found
            if found.get("sources"):
                candidate = found["sources"][0]
                if source_id == "ccteg_materials":
                    assert candidate["url"] == "https://cg.ccteg.cn/cms/channel/ywgg1hw/89035.htm"
                read = await session.execute("read_research_source", {
                    "source_ref": candidate["source_ref"], "purpose": "验证原文读取与保存版本", "limit": 4000,
                })
                item["read"] = {key: read.get(key) for key in ("status", "source_ref", "source_version", "title", "url", "error")}
                item["read"]["chars"] = len(read.get("text", ""))
                if read.get("text"):
                    saved = read_saved_source(db, 1, read["source_ref"], read["source_version"])
                    assert saved["text"] == read["text"]
                    item["snapshot"] = "passed"
            result["sources"].append(item)
        result["investigation"] = session.investigation(status="completed")
        (output / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False))
    finally:
        await session.close()
        db.close()


if __name__ == "__main__":
    asyncio.run(main())
