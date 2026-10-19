"""Database-backed record identity and relationship matching.

This module contains the matching rules used by the collector while keeping
them independent from fetching, parsing, and network adapters.
"""

import re
from difflib import SequenceMatcher


def equivalent_record(db, source_id, canonical, extracted, title, kind):
    record = db.execute('SELECT * FROM records WHERE source_id=? AND canonical_url=?',
                        (source_id, canonical)).fetchone()
    if record or not extracted['notice_number']:
        return record
    # A number can appear in separate lifecycle notices; exact title and type are required.
    return db.execute('''SELECT * FROM records WHERE source_id=? AND notice_number=?
                         AND title=? AND kind=? LIMIT 1''',
                      (source_id, extracted['notice_number'], title, kind)).fetchone()


def related_project(db, source_id, fields, key):
    if not (key or fields['notice_number']):
        return None
    row = db.execute('''SELECT id,related_record_id FROM records WHERE source_id=?
                        AND ((? IS NOT NULL AND notice_number=?) OR
                             (? IS NOT NULL AND buyer=? AND project_key=?))
                        ORDER BY id LIMIT 1''',
                     (source_id, fields['notice_number'], fields['notice_number'],
                      key, fields['buyer'], key)).fetchone()
    return (row['related_record_id'] or row['id']) if row else None


def relink_projects(db):
    db.execute('UPDATE records SET related_record_id=NULL')
    groups = db.execute('''SELECT source_id,notice_number FROM records
                           WHERE notice_number IS NOT NULL
                           GROUP BY source_id,notice_number HAVING COUNT(*)>1''').fetchall()
    for group in groups:
        records = db.execute('''SELECT id,kind FROM records
                                WHERE source_id=? AND notice_number=?
                                ORDER BY CASE WHEN kind IN ('tender','inquiry') THEN 0 ELSE 1 END,id''',
                             (group['source_id'], group['notice_number'])).fetchall()
        root = records[0]['id']
        for record in records:
            db.execute('UPDATE records SET related_record_id=? WHERE id=?',
                       (None if record['id'] == root else root, record['id']))
    groups = db.execute('''SELECT source_id,buyer,project_key FROM records
                           WHERE buyer IS NOT NULL AND project_key IS NOT NULL
                           GROUP BY source_id,buyer,project_key HAVING COUNT(*)>1''').fetchall()
    for group in groups:
        records = db.execute('''SELECT id,kind FROM records
                                WHERE source_id=? AND buyer=? AND project_key=?
                                ORDER BY CASE WHEN kind IN ('tender','inquiry') THEN 0 ELSE 1 END,id''',
                             (group['source_id'], group['buyer'], group['project_key'])).fetchall()
        root = records[0]['id']
        for record in records[1:]:
            db.execute('UPDATE records SET related_record_id=COALESCE(related_record_id,?) WHERE id=?',
                       (root, record['id']))


def normalized_match_text(value):
    return re.sub(r'\W+', '', value or '').lower()


def duplicate_candidates(db, source_id, extracted, title, kind, canonical, body):
    matches = db.execute('''SELECT r.id,r.buyer,r.title,r.kind,r.notice_number,r.canonical_url,
                           v.body_text FROM records r JOIN versions v
                           ON v.record_id=r.id AND v.version=r.current_version
                           WHERE r.source_id<>? AND (r.canonical_url=? OR
                           (? IS NOT NULL AND r.notice_number=?) OR
                           (? IS NOT NULL AND r.buyer=?)) ORDER BY r.id LIMIT 500''',
                         (source_id, canonical, extracted['notice_number'], extracted['notice_number'],
                          extracted['buyer'], extracted['buyer'])).fetchall()
    title_norm = normalized_match_text(title)
    body_norm = normalized_match_text(body)
    for row in matches:
        if row['canonical_url'] == canonical:
            return row['id'], None
        same_buyer = bool(extracted['buyer'] and extracted['buyer'] == row['buyer'])
        same_kind = kind == row['kind']
        same_number = bool(extracted['notice_number'] and extracted['notice_number'] == row['notice_number'])
        other_title = normalized_match_text(row['title'])
        other_body = normalized_match_text(row['body_text'])
        same_title = title_norm == other_title
        same_body = len(body_norm) >= 100 and body_norm == other_body
        if same_kind and same_buyer and ((same_number and same_title) or (same_title and same_body)):
            return row['id'], None
    for row in matches:
        same_buyer = bool(extracted['buyer'] and extracted['buyer'] == row['buyer'])
        same_number = bool(extracted['notice_number'] and extracted['notice_number'] == row['notice_number'])
        title_similarity = SequenceMatcher(None, title_norm, normalized_match_text(row['title'])).ratio()
        body_similarity = SequenceMatcher(None, body_norm[:4000],
                                          normalized_match_text(row['body_text'])[:4000]).ratio()
        if same_number or (same_buyer and kind == row['kind'] and
                           (title_similarity >= 0.85 or body_similarity >= 0.80)):
            return None, row['id']
    return None, None
