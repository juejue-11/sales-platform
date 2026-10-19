"""HTTP contract for collector management."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from ..collection.manager import (
    CollectionBusy,
    CollectionError,
    CollectionInvalid,
    CollectionUnavailable,
)


router = APIRouter(prefix="/api/collection", tags=["collection"])


class CollectionRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source_id: str | None = None


class CollectionSourceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: StrictBool


class CollectionScheduleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: StrictBool
    at: str = Field(pattern=r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$")


def _raise_collection_error(exc: CollectionError) -> None:
    status = 409 if isinstance(exc, CollectionBusy) else 400 if isinstance(exc, CollectionInvalid) else 503
    raise HTTPException(status_code=status, detail=str(exc)) from exc


@router.get("")
async def collection_status(request: Request) -> dict[str, Any]:
    return await request.app.state.collection.status()


@router.post("/run")
async def collection_run(
    request: Request,
    body: CollectionRunRequest | None = Body(default=None),
) -> dict[str, Any]:
    source_id = body.source_id if body is not None else None
    try:
        task = await request.app.state.collection.start(source_id)
    except CollectionError as exc:
        _raise_collection_error(exc)
    return {"message": "采集已启动", "task": task}


@router.patch("/sources/{source_id}")
async def update_collection_source(
    source_id: str,
    body: CollectionSourceRequest,
    request: Request,
) -> dict[str, str]:
    try:
        return await request.app.state.collection.set_source(source_id, body.enabled)
    except CollectionError as exc:
        _raise_collection_error(exc)
    raise AssertionError("unreachable")


@router.put("/schedule")
async def update_collection_schedule(
    body: CollectionScheduleRequest,
    request: Request,
) -> dict[str, str]:
    try:
        return await request.app.state.collection.set_schedule(body.enabled, body.at)
    except CollectionError as exc:
        _raise_collection_error(exc)
    raise AssertionError("unreachable")
