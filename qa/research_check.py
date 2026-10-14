"""Real HTTP/browser acceptance with isolated storage and mocked model/network."""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
PORT = 8893


def serve(output: Path) -> None:
    sys.path.insert(0, str(ROOT))
    import uvicorn
    from backend.main import create_app
    from backend.tests.test_backend import Provider
    from backend.tests.test_research_integration import make_research_service
    from backend.core.utils import now_iso

    records = [dict(id=project_id, source_id="qa", kind="tender", title=title,
                    project_name=title, buyer="隔离验收采购方", notice_number="P-100",
                    body_text="本项目采购破碎机，采购编号：P-100。", equipment=["破碎机"],
                    deadline_at="2099-12-31 09:00", published_at="2026-10-12", current_version=1,
                    canonical_url=f"https://official.test/records/{project_id}")
               for project_id, title in ((9101, "项目调查闭环验收"), (9102, "旧报告兼容验收"),
                                         (9103, "项目调查重试验收"))]
    service, client, _ = make_research_service(output, records)
    service.update_settings({"import": {"auto_import": False}})
    for project_id in (9101, 9103):
        asyncio.run(service.run_job(service.queue_project(project_id)["job_id"]))
        assert service.project_detail(project_id)["judgment"] is not None

    # A legacy report has record evidence, no bound investigation, and remains readable.
    service.provider = Provider()
    service.provider.payload["reasons"][0]["evidence"][0]["record_id"] = 9102
    service.provider.payload["reasons"][0]["evidence"][0]["quote"] = "采购破碎机"
    asyncio.run(service.run_job(service.queue_project(9102)["job_id"]))
    with service.db:
        service.db.execute("UPDATE judgments SET agent_run_id=NULL,research_policy_version=NULL WHERE project_id=9102")
        service.db.execute("DELETE FROM agent_runs WHERE project_id=9102")

    # Keep the previous successful report while a later investigation is limited.
    job_id = service.queue_project(9103, force=True)["job_id"]
    run_id = service._new_agent_run(job_id, 9103)
    source = service.project_detail(9103)["judgment"]["investigation"]["sources"][0]
    investigation = {
        "policy_version": "project_research_v1", "status": "limited", "stop_reason": "来源读取受限，已保留已有结论",
        "unresolved": ["变更通知中的交期尚未确认"], "sources": [source],
        "events": [{"at": now_iso(), "tool": "read_research_source", "purpose": "复查变更后的交付要求",
                    "status": "auth_required", "sources": [source["source_ref"]], "message": "公开来源要求登录"}],
    }
    with service.db:
        service.db.execute("UPDATE jobs SET status='failed',error='isolated fixture',finished_at=? WHERE id=?", (now_iso(), job_id))
        service.db.execute("UPDATE agent_runs SET status='failed',investigation_json=?,usage_json=?,finished_at=? WHERE id=?",
                           (json.dumps(investigation, ensure_ascii=False), json.dumps({"model_calls": 7, "tool_calls": 3}), now_iso(), run_id))
    assert service.project_detail(9103)["judgment"]["agent_run_id"] != run_id
    try:
        uvicorn.run(create_app(service), host="127.0.0.1", port=PORT)
    finally:
        asyncio.run(client.aclose())
        service.close()


def main() -> None:
    output = ROOT / "data" / f"research-acceptance-{time.time_ns()}"
    output.mkdir(parents=True)
    env = dict(os.environ, SCREENING_DB_PATH=str(output / "unused.sqlite3"),
               SCREENING_IMPORT_PATH=str(output / "records.jsonl"), SCREENING_AI_ENGINE="pi_sdk",
               OPENAI_API_KEY="", OPENAI_MODEL="", PYTHONUTF8="1")
    node = shutil.which("node") or os.getenv("SCREENING_NODE_PATH")
    if not node:
        raise RuntimeError("Node is required for browser acceptance")
    with (output / "server.log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--serve", str(output)],
                                   cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT,
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        try:
            with httpx.Client(base_url=f"http://127.0.0.1:{PORT}", timeout=10, trust_env=False) as client:
                deadline = time.monotonic() + 40
                while True:
                    try:
                        if client.get("/api/health").status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    if process.poll() is not None or time.monotonic() > deadline:
                        raise AssertionError(f"QA server did not start: {output / 'server.log'}")
                    time.sleep(.2)
                normal = client.get("/api/projects/9101").json()
                assert not normal["judgment"]["stale"]
                assert normal["judgment"]["investigation"]["status"] == "completed"
                evidence = normal["judgment"]["reasons"][0]["evidence"][0]
                assert client.get(evidence["snapshot_url"]).status_code == 200
                forged = evidence["snapshot_url"].replace("/projects/9101/", "/projects/9102/")
                assert client.get(forged).status_code == 404
            result = subprocess.run([node, str(ROOT / "qa/research_browser.cjs"),
                                     f"http://127.0.0.1:{PORT}", str(output)], cwd=ROOT, env=env,
                                    text=True, encoding="utf-8", capture_output=True)
            (output / "browser.json").write_text(result.stdout + result.stderr, encoding="utf-8")
            print(result.stdout + result.stderr)
            result.check_returncode()
            print(json.dumps({"http": "passed", "output": str(output), "production_model_calls": 0}, ensure_ascii=False))
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)


if __name__ == "__main__":
    serve(Path(sys.argv[2])) if "--serve" in sys.argv else main()
