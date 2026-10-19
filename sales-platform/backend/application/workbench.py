"""Application interface for follow-up and user supplied procurement evidence.

The host supplies project loading and queueing callbacks. This module owns user
mutations; it has no dependency on the screening service or model provider.
"""
from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any

from ..core.utils import now_iso
from ..procurement import supplements
from .. import followup


def followup_summary(db: sqlite3.Connection, project_id: int, decision: dict[str, Any]) -> dict[str, Any]:
    """Read list metadata without creating a proposal or joining follow-up."""
    has_table = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='followups'").fetchone()
    row = db.execute(
        '''SELECT is_following,stage,disposition,owner,next_action,outcome,submitted_at,updated_at
           FROM followups WHERE project_id=?''', (project_id,),
    ).fetchone() if has_table else None
    is_following = bool(row['is_following']) if row else decision.get('status') == 'following'
    return {'is_following': is_following,
            'followup_stage': (row['stage'] if row else 'evaluating') if is_following else None,
            'followup_summary': {key: row[key] for key in (
                'stage', 'disposition', 'owner', 'next_action', 'outcome', 'submitted_at', 'updated_at',
            )} if row else None}


class WorkbenchApplication:
    def __init__(self, *, db: sqlite3.Connection, lock: AbstractContextManager,
                 read_project: Callable[[int], dict[str, Any]], request_reading: Callable[[int], Any]):
        self.db = db
        self.lock = lock
        self.read_project = read_project
        self.request_reading = request_reading
        supplements.ensure_schema(db)
        followup.ensure_schema(db)

    def _reading(self, project_id: int) -> dict[str, Any]:
        project = self.read_project(project_id)
        return project.get('procurement_reading') or {}

    def get_followup(self, project_id: int) -> dict[str, Any]:
        project = self.read_project(project_id)
        reading = project.get('procurement_reading') or {}
        with self.lock, self.db:
            existing = self.db.execute('SELECT 1 FROM followups WHERE project_id=?', (project_id,)).fetchone()
            decision = project.get('decision') or {}
            if existing is None and decision.get('status') == 'following':
                # Preserve the user's existing choice and ownership. An old
                # following label does not prove submission or task completion.
                followup.save_followup(self.db, project_id, {
                    'is_following': True, 'owner': decision.get('owner') or '',
                    'next_action': decision.get('next_action') or '', 'note': decision.get('note') or '',
                })
            return followup.get_followup(self.db, project_id, reading=reading)

    def save_followup(self, project_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        reading = self._reading(project_id)
        with self.lock, self.db:
            return followup.save_followup(self.db, project_id, payload, procurement=reading)

    def adopt_plan(self, project_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        reading = self._reading(project_id)
        if reading.get('stale') or reading.get('status') not in {'complete', 'partial'}:
            raise followup.FollowupValidationError('采购说明尚需核对，可先手工加入跟进')
        with self.lock, self.db:
            return followup.adopt_plan(self.db, project_id, plan_id=payload.get('plan_id'),
                                       owner=payload.get('owner'), procurement=reading)

    def update_task(self, project_id: int, task_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.read_project(project_id)
        with self.lock, self.db:
            return followup.update_task(self.db, project_id, task_id, payload)

    def update_material(self, project_id: int, material_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.read_project(project_id)
        with self.lock, self.db:
            return followup.update_material(self.db, project_id, material_id, payload)

    def corrections(self, project_id: int) -> list[dict[str, Any]]:
        self.read_project(project_id)
        with self.lock:
            return supplements.corrections(self.db, project_id)

    def uploaded_file(self, project_id: int, document_id: str) -> dict[str, Any] | None:
        self.read_project(project_id)
        with self.lock:
            return supplements.uploaded_file(self.db, project_id, document_id)

    async def supplement_document(self, project_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        project = self.read_project(project_id)
        if payload.get('record_id') not in project['record_ids']:
            raise supplements.SupplementError('资料记录不属于本次采购')
        document, raw = await asyncio.to_thread(supplements.prepare_document, payload)
        with self.lock, self.db:
            existing = self.db.execute('SELECT is_current FROM procurement_documents WHERE id=?',
                                       (document['id'],)).fetchone()
            supplements.save_document(self.db, project_id, document, raw)
            changed = existing is None or not existing['is_current']
            if changed:
                self._invalidate(project_id)
        if changed:
            self.request_reading(project_id)
        return {'document': {key: document[key] for key in
                             ('id', 'record_id', 'name', 'status', 'sha256', 'pages')},
                'message': '补充文件已保存，采购说明将根据新资料重新核对' if changed else '相同文件已保存，保留原阅读结果'}

    def correct_reading(self, project_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        reading = self._reading(project_id)
        requirement_ids = {item['id'] for section in reading.get('sections', [])
                           for item in section.get('requirements', []) if isinstance(item, dict) and item.get('id')}
        with self.lock, self.db:
            values = supplements.save_correction(self.db, project_id, payload,
                                                  material_version=reading.get('material_version') or '',
                                                  requirement_ids=requirement_ids)
            self._invalidate(project_id)
        self.request_reading(project_id)
        return {'corrections': values, 'message': '纠正已留存；重新阅读时会核对与原始资料的差异'}

    def _invalidate(self, project_id: int) -> None:
        self.db.execute('UPDATE judgments SET stale=1 WHERE project_id=?', (project_id,))
        self.db.execute('UPDATE projects SET updated_at=? WHERE id=?', (now_iso(), project_id))
