"""Atomic JSONL export for downstream consumers."""

import json
import tempfile
import time
from pathlib import Path


_REPLACE_RETRIES = 20
_REPLACE_RETRY_DELAY = 0.1


def _replace_with_retry(pending, path):
    for attempt in range(_REPLACE_RETRIES):
        try:
            pending.replace(path)
            return
        except PermissionError:
            if attempt + 1 == _REPLACE_RETRIES:
                raise
            time.sleep(_REPLACE_RETRY_DELAY)


def export_jsonl(db, path):
    rows = db.execute('''SELECT r.*,s.name AS source_name,v.original_url,v.archive_path,
                         v.content_sha256,v.body_text,v.parse_status,v.fetched_at
                         FROM records r JOIN sources s ON s.id=r.source_id
                         JOIN versions v ON v.record_id=r.id AND v.version=r.current_version
                         ORDER BY r.id''').fetchall()
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = None
    try:
        with tempfile.NamedTemporaryFile('w', encoding='utf-8', newline='\n',
                                         dir=path.parent, prefix='.records-', suffix='.tmp',
                                         delete=False) as stream:
            pending = Path(stream.name)
            for row in rows:
                item = dict(row)
                attached = db.execute('SELECT attachments_json FROM version_attachments WHERE record_id=? AND version=?',
                                      (row['id'],row['current_version'])).fetchone()
                item['attachments'] = json.loads(attached['attachments_json']) if attached else []
                for key in ('equipment_json', 'facts_json', 'inferences_json'):
                    item[key.removesuffix('_json')] = json.loads(item.pop(key))
                stream.write(json.dumps(item, ensure_ascii=False) + '\n')
    except Exception:
        if pending is not None:
            pending.unlink(missing_ok=True)
        raise
    try:
        _replace_with_retry(pending, path)
    finally:
        pending.unlink(missing_ok=True)
    return len(rows)
