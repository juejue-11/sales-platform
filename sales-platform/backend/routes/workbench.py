"""HTTP inputs for procurement workbench user actions."""
from __future__ import annotations

from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field, StrictInt

from ..application.service import ConflictError, NotFoundError, ServiceError
from ..procurement.supplements import SupplementError
from ..followup import FollowupConflictError, FollowupNotFoundError, FollowupValidationError


router = APIRouter(prefix='/api/projects/{project_id}')


class SupplementRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    record_id: StrictInt = Field(gt=0)
    name: str = Field(min_length=1, max_length=200)
    content_base64: str = Field(min_length=1, max_length=16_777_216)


class CorrectionRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    requirement_id: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=12000)
    reason: str = Field(min_length=1, max_length=2000)


@router.post('/documents/supplement')
async def supplement(project_id: int, body: SupplementRequest, request: Request) -> dict[str, Any]:
    try:
        return await request.app.state.workbench.supplement_document(project_id, body.model_dump())
    except SupplementError as exc:
        raise ServiceError(str(exc)) from exc


@router.get('/documents/{document_id}/uploaded-file')
async def uploaded(project_id: int, document_id: str, request: Request) -> Response:
    value = request.app.state.workbench.uploaded_file(project_id, document_id)
    if value is None:
        raise NotFoundError('未找到该项目的补充文件')
    return Response(value['original_bytes'], media_type='application/octet-stream',
                    headers={'Content-Disposition': f"attachment; filename*=UTF-8''{quote(value['name'])}",
                             'X-Content-Type-Options': 'nosniff'})


@router.get('/reading/corrections')
async def reading_corrections(project_id: int, request: Request) -> dict[str, Any]:
    return {'corrections': request.app.state.workbench.corrections(project_id)}


@router.post('/reading/corrections')
async def correction(project_id: int, body: CorrectionRequest, request: Request) -> dict[str, Any]:
    try:
        return request.app.state.workbench.correct_reading(project_id, body.model_dump())
    except SupplementError as exc:
        raise ServiceError(str(exc)) from exc


def _followup_action(request: Request, action: str, *args: Any) -> dict[str, Any]:
    try:
        return getattr(request.app.state.workbench, action)(*args)
    except FollowupConflictError as exc:
        raise ConflictError(str(exc)) from exc
    except FollowupNotFoundError as exc:
        raise NotFoundError(str(exc)) from exc
    except FollowupValidationError as exc:
        raise ServiceError(str(exc)) from exc


@router.get('/followup')
async def get_followup(project_id: int, request: Request) -> dict[str, Any]:
    return _followup_action(request, 'get_followup', project_id)


@router.put('/followup')
async def save_followup(project_id: int, body: dict[str, Any], request: Request) -> dict[str, Any]:
    allowed = {'stage', 'disposition', 'owner', 'next_action', 'outcome', 'submitted_at',
               'submission_channel', 'receipt', 'reason', 'note', 'active', 'is_following'}
    if set(body) - allowed:
        raise ServiceError('跟进输入包含不支持的字段')
    return _followup_action(request, 'save_followup', project_id, body)


@router.post('/followup/adopt')
async def adopt_plan(project_id: int, body: dict[str, Any], request: Request) -> dict[str, Any]:
    if set(body) - {'plan_id', 'owner'}:
        raise ServiceError('采用计划仅支持 plan_id 和 owner')
    return _followup_action(request, 'adopt_plan', project_id, body)


@router.patch('/followup/tasks/{task_id}')
async def update_task(project_id: int, task_id: str, body: dict[str, Any], request: Request) -> dict[str, Any]:
    return _followup_action(request, 'update_task', project_id, task_id, body)


@router.patch('/followup/materials/{material_id}')
async def update_material(project_id: int, material_id: str, body: dict[str, Any], request: Request) -> dict[str, Any]:
    return _followup_action(request, 'update_material', project_id, material_id, body)
