"""Explicit, paid-model evaluation against selected local projects.

Stop the platform first to avoid competing queue consumers. Pass --call-model
and one or more --project IDs; nothing runs merely by importing this module.
Artifacts contain public project/model output, never model configuration.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.ai import PiSDKProvider
from backend.service import ScreeningService


class RecordingProvider(PiSDKProvider):
    def __init__(self, output: Path) -> None:
        super().__init__()
        self.output = output
        self.project_id = 0
        self.responses = 0

    async def analyze(self, **kwargs):
        result = await super().analyze(**kwargs)
        self.responses += 1
        # The bridge already rejects a credential echoed in model content.
        (self.output / f"response-{self.project_id}-{self.responses}.txt").write_text(
            result.content, encoding="utf-8")
        return result


async def evaluate(ids: list[int], output: Path) -> None:
    record = ROOT / "data/service.json"
    try:
        port = int(json.loads(record.read_text(encoding="utf-8-sig")).get("port", 8766))
    except (OSError, ValueError, TypeError):
        port = 8766
    try:
        connection = socket.create_connection(("127.0.0.1", port), timeout=0.5)
    except OSError:
        pass
    else:
        connection.close()
        raise RuntimeError("Stop the platform before running paid evaluation; two queue consumers would duplicate calls")
    output.mkdir(parents=True, exist_ok=True)
    provider = RecordingProvider(output)
    service = ScreeningService(provider=provider)
    try:
        if service.db.execute("SELECT 1 FROM jobs WHERE status='running'").fetchone():
            raise RuntimeError("Stop the platform and finish active jobs first")
        for project_id in ids:
            provider.project_id, provider.responses = project_id, 0
            job = service.queue_project(project_id, force=True)
            if job.get("job_id") is None:
                print(json.dumps({"project": project_id, **job}, ensure_ascii=False), flush=True)
                continue
            print(json.dumps({"project": project_id, "job": job["job_id"], "status": "started"}), flush=True)
            await service.run_job(job["job_id"])
            detail = service.project_detail(project_id)
            (output / f"after-{project_id}.json").write_text(
                json.dumps(detail, ensure_ascii=False, indent=2), encoding="utf-8")
            run = detail.get("agent_run") or {}
            print(json.dumps({"project": project_id, "status": run.get("status"),
                              "error": run.get("error"), "usage": run.get("usage"),
                              "summary": detail.get("ai_summary")}, ensure_ascii=False), flush=True)
    finally:
        await service.aclose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--call-model", action="store_true", required=True)
    parser.add_argument("--project", type=int, action="append", required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "data/live-framework-evaluation")
    args = parser.parse_args()
    asyncio.run(evaluate(args.project, args.output))
