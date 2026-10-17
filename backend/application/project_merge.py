"""Preserve workbench data during the importer's existing project merge.

The caller owns the transaction and has moved collector record membership.
No model request, network access or transaction commit happens here.
"""
from __future__ import annotations

import sqlite3

from ..core.utils import now_iso
from ..followup import merge_followup
from ..procurement.supplements import merge_project as merge_supplements


def _has_table(db: sqlite3.Connection, name: str) -> bool:
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def merge_workbench_data(db: sqlite3.Connection, source_id: int, target_id: int) -> None:
    if source_id == target_id:
        return
    if _has_table(db, 'followups'):
        merge_followup(db, source_id, target_id)
    if _has_table(db, 'procurement_uploads') and _has_table(db, 'procurement_corrections'):
        merge_supplements(db, source_id, target_id)
    if _has_table(db, 'procurement_readings'):
        rows = db.execute('SELECT id,material_version FROM procurement_readings WHERE project_id=? ORDER BY id',
                          (source_id,)).fetchall()
        for row in rows:
            revision = db.execute('SELECT COALESCE(MAX(revision),0)+1 FROM procurement_readings '
                                  'WHERE project_id=? AND material_version=?', (target_id, row['material_version'])).fetchone()[0]
            db.execute('UPDATE procurement_readings SET project_id=?,revision=?,stale=1 WHERE id=?',
                       (target_id, revision, row['id']))
        db.execute('UPDATE procurement_readings SET stale=1 WHERE project_id=?', (target_id,))
    if _has_table(db, 'procurement_reading_jobs'):
        # An in-flight request owns its old source snapshot. Keep that attempt
        # visible; it cannot become the current explanation of the merged set.
        db.execute("UPDATE procurement_reading_jobs SET status='failed',error=?,finished_at=?,updated_at=? "
                   "WHERE project_id=? AND status='running'",
                   ('项目已归并，需按合并后的资料重新阅读', now_iso(), now_iso(), source_id))
        db.execute('UPDATE procurement_reading_jobs SET project_id=? WHERE project_id=?', (target_id, source_id))
