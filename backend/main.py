from __future__ import annotations

import mimetypes
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from .service import ConflictError, NotFoundError, ScreeningService, ServiceError


class AnalyzeRequest(BaseModel):
    force: bool = False


class DecisionRequest(BaseModel):
    status: str
    owner: str = ""
    next_action: str = ""
    note: str = ""
    selected_option_id: str | None = None
    judgment_id: int | None = None


class GateRequest(BaseModel):
    action: str
    reason: str


class LinkRequest(BaseModel):
    target_project_id: int
    relation: str
    note: str = ""


class SettingsRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    business: dict[str, Any] | None = None
    ai: dict[str, Any] | None = None
    import_: dict[str, Any] | None = Field(default=None, alias="import")
    version: int | None = None

    def as_payload(self) -> dict[str, Any]:
        value = self.model_dump(by_alias=True, exclude_none=True)
        value.pop("version", None)
        return value


def create_app(service: ScreeningService | None = None) -> FastAPI:
    screening = service or ScreeningService()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.service.start_background()
        try:
            yield
        finally:
            app.state.service.close()

    app = FastAPI(title="销售平台筛选与 AI 方案平台", version="1.0.0", lifespan=lifespan)
    app.state.service = screening

    @app.exception_handler(NotFoundError)
    async def not_found_handler(_: Request, exc: NotFoundError):
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(ConflictError)
    async def conflict_handler(_: Request, exc: ConflictError):
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(ServiceError)
    async def service_error_handler(_: Request, exc: ServiceError):
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        return {"ok": True, "app": "sales-screening"}

    @app.get("/api/dashboard")
    async def dashboard() -> dict[str, Any]:
        return app.state.service.dashboard()

    @app.get("/api/projects")
    async def projects(
        q: str = Query(""), gate: str = Query(""), ai_status: str = Query(""), decision: str = Query(""),
        ai_decision: str = Query(""), deadline_soon: bool = Query(False),
        kind: str = Query(""), source_id: str = Query(""), page: int = Query(1, ge=1),
        page_size: int = Query(30, ge=1, le=200), sort: str = Query("updated"),
    ) -> dict[str, Any]:
        return app.state.service.list_projects(q=q, gate=gate, ai_status=ai_status, decision=decision,
                                               ai_decision=ai_decision, deadline_soon=deadline_soon,
                                               kind=kind, source_id=source_id, page=page, page_size=page_size, sort=sort)

    @app.get("/api/projects/{project_id}")
    async def project(project_id: int) -> dict[str, Any]:
        return app.state.service.project_detail(project_id)

    @app.post("/api/projects/{project_id}/analyze")
    async def analyze(project_id: int, body: AnalyzeRequest | None = Body(default=None)) -> dict[str, Any]:
        body = body or AnalyzeRequest()
        result = app.state.service.queue_project(project_id, force=body.force, schedule=True)
        return result

    @app.post("/api/projects/{project_id}/decision")
    async def decision(project_id: int, body: DecisionRequest) -> dict[str, Any]:
        return app.state.service.save_decision(project_id, body.model_dump())

    @app.post("/api/projects/{project_id}/gate")
    async def gate(project_id: int, body: GateRequest) -> dict[str, Any]:
        return app.state.service.gate_action(project_id, body.action, body.reason)

    @app.post("/api/projects/{project_id}/link")
    async def link(project_id: int, body: LinkRequest) -> dict[str, Any]:
        return app.state.service.link_projects(project_id, body.target_project_id, body.relation, body.note)

    @app.get("/api/settings")
    async def settings() -> dict[str, Any]:
        return app.state.service.get_settings()

    @app.put("/api/settings")
    async def update_settings(body: SettingsRequest) -> dict[str, Any]:
        return app.state.service.update_settings(body.as_payload())

    @app.get("/api/status")
    async def status() -> dict[str, Any]:
        return app.state.service.status()

    @app.post("/api/import")
    async def import_records() -> dict[str, Any]:
        return await app.state.service.import_file()

    @app.post("/api/analyze-pending")
    async def analyze_pending() -> dict[str, Any]:
        if not app.state.service._ai_config()["configured"]:
            return {"queued": 0, "message": "AI 未配置，已保留项目"}
        queued = app.state.service.queue_pending(schedule=True)
        return {"queued": queued, "message": "已排队" if queued else "没有可排队的项目"}

    @app.post("/api/jobs/retry-failed")
    async def retry_failed() -> dict[str, Any]:
        if not app.state.service._ai_config()["configured"]:
            return {"queued": 0, "message": "AI 未配置，无法重试"}
        queued = app.state.service.retry_failed()
        return {"queued": queued, "message": "失败任务已重新排队" if queued else "没有可重试任务"}

    @app.get("/api/records/{record_id}/archive")
    async def archive(record_id: int):
        path = app.state.service.archive_path(record_id)
        media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        return FileResponse(path, media_type=media_type, filename=path.name)

    # Production frontend is served by this same process when a Vite build is
    # present. API routes above are registered first so /api remains authoritative.
    frontend_dist = Path(__file__).resolve().parent.parent / "frontend" / "dist"
    if frontend_dist.is_dir() and (frontend_dist / "index.html").is_file():
        @app.get("/{frontend_path:path}", include_in_schema=False)
        async def frontend(frontend_path: str):
            # BrowserRouter deep links need index.html on refresh. Existing
            # assets are served directly; a missing asset remains a 404.
            if frontend_path.startswith("api"):
                raise HTTPException(status_code=404, detail="not found")
            candidate = (frontend_dist / frontend_path).resolve()
            try:
                candidate.relative_to(frontend_dist.resolve())
            except ValueError:
                raise HTTPException(status_code=404, detail="not found")
            if candidate.is_file():
                media_type = mimetypes.guess_type(candidate.name)[0]
                return FileResponse(candidate, media_type=media_type)
            if frontend_path.startswith("assets/") or "." in Path(frontend_path).name:
                raise HTTPException(status_code=404, detail="not found")
            return FileResponse(frontend_dist / "index.html", media_type="text/html")

    return app


app = create_app()
