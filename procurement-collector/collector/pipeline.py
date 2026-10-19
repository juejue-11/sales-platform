import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from bs4 import BeautifulSoup

from . import ai_extract, attachments
from .db import json_value, sync_sources
from .eligibility import notice_rejection, publication_rejection
from .extract import canonical_url, clean_title, extract, kind_from_title, project_key, sha256
from .fetch import Fetcher, detail, listing
from .identity import (duplicate_candidates, equivalent_record, normalized_match_text,
                        related_project, relink_projects)


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def _validate_max_age_days(value):
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError('max_age_days must be a positive integer')
    return value


def _record_filtered(db, run_id, source, entry, reason, *, at, title=None, published_at=None):
    """Persist an eligibility decision without touching business records."""
    publication = published_at if published_at is not None else entry.get('published_at')
    if isinstance(publication, datetime):
        publication = publication.isoformat(timespec='seconds')
    db.execute('''INSERT INTO filtered_entries
                  (run_id,source_id,title,url,published_at,reason,filtered_at)
                  VALUES (?,?,?,?,?,?,?)''',
               (run_id, source['id'], clean_title(title if title is not None else entry.get('title')),
                entry['url'], publication,
                reason, at.isoformat(timespec='seconds')))
    db.commit()


def _listing_publication_rejection(entry, *, at, max_age_days):
    """Return a decisive listing rejection.

    A missing or malformed listing date is deliberately deferred to the
    detail page, where explicit publication metadata may still be available.
    """
    value = entry.get('published_at')
    if value in (None, ''):
        return None
    reason = publication_rejection(value, now=at, max_age_days=max_age_days)
    if reason in ('missing_published_at', 'invalid_published_at'):
        return None
    return reason


def _notice_publication(entry, fields, *, at, max_age_days):
    """Keep a valid listing timestamp intact for policy date/time checks."""
    listing_value = entry.get('published_at')
    if listing_value not in (None, ''):
        listing_reason = publication_rejection(listing_value, now=at,
                                               max_age_days=max_age_days)
        if listing_reason not in ('missing_published_at', 'invalid_published_at'):
            return listing_value
    return fields['published_at'] or listing_value


def archive(root, record_id, version, raw, mime):
    ext = '.pdf' if mime == 'application/pdf' else '.html' if 'html' in mime else '.bin'
    rel = Path('archive') / str(record_id) / f'v{version}{ext}'
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return rel.as_posix()


def ingest(db, data_root, run_id, source, entry, response, *, max_age_days=None, at=None):
    """Ingest one fetched notice.

    Direct callers retain the historical/archive primitive semantics when no
    policy window is supplied.  Formal runs always pass ``max_age_days`` and
    one run timestamp so eligibility is decided before any expensive work or
    business-record mutation.
    """
    if max_age_days is not None:
        max_age_days = _validate_max_age_days(max_age_days)
        at = at or datetime.now(timezone.utc)
        if not isinstance(at, datetime):
            raise TypeError('at must be a datetime')
    fetched_at = now()
    body, parse_status, parse_error, mime = detail(response, source)
    title = clean_title(entry['title'])
    if source.get('title_selector') and mime != 'application/pdf':
        full_title = BeautifulSoup(response.text, 'html.parser').select_one(source['title_selector'])
        if full_title:
            title = clean_title(full_title.get_text(' ', strip=True)) or title
    if not title:
        raise ValueError('Missing title')
    category = source['category']
    kind = kind_from_title(title, category)
    fields = extract(title, body, category, entry.get('published_at'))
    if category == 'procurement':
        fields['notice_number'] = fields['notice_number'] or entry.get('notice_number')
        fields['deadline_at'] = fields['deadline_at'] or entry.get('deadline_at')
        fields['buyer'] = fields['buyer'] or entry.get('buyer_hint')
    if category == 'news' and source.get('company_name') and not fields['company']:
        fields['company'] = source['company_name']
    if max_age_days is not None:
        detail_keywords = source.get('include_detail_keywords', [])
        if detail_keywords and not any(word in response.text for word in detail_keywords):
            _record_filtered(db, run_id, source, entry, 'detail_keyword_miss',
                             at=at, title=title,
                             published_at=fields['published_at'] or entry.get('published_at'))
            return 'filtered'
        publication = _notice_publication(entry, fields, at=at,
                                          max_age_days=max_age_days)
        reason = notice_rejection(title, body, category, publication,
                                  now=at, max_age_days=max_age_days)
        if reason:
            _record_filtered(db, run_id, source, entry, reason, at=at, title=title,
                             published_at=publication)
            return 'filtered'
    attached = attachments.collect(response) if category == 'procurement' else []
    canonical = canonical_url(entry['url'])
    digest = sha256(response.content)
    key = project_key(fields['buyer'], fields['project_name'], source['id'])
    record = equivalent_record(db, source['id'], canonical, fields, title, kind)
    if record:
        latest = db.execute('''SELECT content_sha256,body_text FROM versions
                               WHERE record_id=? ORDER BY version DESC LIMIT 1''',
                            (record['id'],)).fetchone()
        db.execute('UPDATE records SET last_seen_at=? WHERE id=?', (fetched_at, record['id']))
        previous_attachments = attachments.saved(db, record['id'], record['current_version'])
        if (latest and latest['content_sha256'] == digest and
                attachments.signature(attached) == attachments.signature(previous_attachments)):
            equipment = fields['equipment'] if not ai_extract.configured() else sorted(
                set(fields['equipment']) | set(json.loads(record['equipment_json'])))
            facts = fields['facts'] if not ai_extract.configured() else json.loads(record['facts_json'])
            inferences = fields['inferences'] if not ai_extract.configured() else json.loads(record['inferences_json'])
            db.execute('''UPDATE records SET title=?,project_name=?,project_key=COALESCE(?,project_key),
                published_at=COALESCE(?,published_at),buyer=?,
                notice_number=?,deadline_at=?,company=?,equipment_json=?,facts_json=?,inferences_json=?
                WHERE id=?''',
                (title, title, key, fields['published_at'], fields['buyer'], fields['notice_number'],
                 fields['deadline_at'], fields['company'], json_value(equipment),
                 json_value(facts), json_value(inferences), record['id']))
            attachments.save(db, data_root, record['id'], record['current_version'], attached)
            db.commit()
            return 'unchanged'
    if parse_status == 'ok' and ai_extract.configured():
        try:
            fields = ai_extract.merge(fields, ai_extract.request_extraction(body, category))
        except Exception as exc:
            parse_status, parse_error = 'ai_fallback', f'AI extraction failed: {type(exc).__name__}: {exc}'[:500]
    if record:
        record_id = record['id']
        version = record['current_version'] + 1
        event_type = ('markup_changed' if parse_status == 'ok' and
                      body == latest['body_text'] and title == record['title'] and
                      attachments.signature(attached) == attachments.signature(previous_attachments) else 'changed')
    else:
        duplicate_of, possible_duplicate_of = duplicate_candidates(
            db, source['id'], fields, title, kind, canonical, body)
        related_id = related_project(db, source['id'], fields, key)
        cur = db.execute('''INSERT INTO records
            (source_id,canonical_url,kind,title,published_at,buyer,project_name,
             notice_number,deadline_at,company,equipment_json,facts_json,inferences_json,
             project_key,possible_duplicate_of,duplicate_of,related_record_id,first_seen_at,last_seen_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (source['id'], canonical, kind, title, fields['published_at'], fields['buyer'],
             fields['project_name'], fields['notice_number'], fields['deadline_at'], fields['company'],
             json_value(fields['equipment']), json_value(fields['facts']),
             json_value(fields['inferences']), key, possible_duplicate_of, duplicate_of, related_id,
             fetched_at, fetched_at))
        record_id = cur.lastrowid
        version = 1
        event_type = 'new'
    rel = archive(data_root, record_id, version, response.content, mime)
    db.execute('''INSERT INTO versions
        (record_id,version,fetched_at,original_url,archive_path,content_sha256,
         mime_type,body_text,parse_status,parse_error) VALUES (?,?,?,?,?,?,?,?,?,?)''',
        (record_id, version, fetched_at, response.url, rel, digest, mime,
         body, parse_status, parse_error))
    attachments.save(db, data_root, record_id, version, attached)
    if event_type in ('changed', 'markup_changed'):
        db.execute('''UPDATE records SET current_version=?,title=?,kind=?,
            published_at=COALESCE(?,published_at),buyer=COALESCE(?,buyer),
            project_name=COALESCE(?,project_name),notice_number=COALESCE(?,notice_number),
            deadline_at=COALESCE(?,deadline_at),company=COALESCE(?,company),
            equipment_json=?,facts_json=?,inferences_json=?,
            project_key=COALESCE(?,project_key) WHERE id=?''',
            (version, title, kind, fields['published_at'], fields['buyer'], fields['project_name'],
             fields['notice_number'], fields['deadline_at'], fields['company'],
             json_value(fields['equipment']), json_value(fields['facts']),
             json_value(fields['inferences']), key, record_id))
    db.execute('''INSERT INTO events(record_id,version,run_id,event_type,occurred_at,details)
                  VALUES (?,?,?,?,?,?)''', (record_id, version, run_id, event_type,
                                          fetched_at, parse_status))
    db.commit()
    return event_type


def _save_progress(db, run_id, source_run, found, fetched, stats):
    """Commit progress visible to the read-only control/status process."""
    db.execute('''UPDATE source_runs SET found_count=?,fetched_count=? WHERE id=?''',
               (found, fetched, source_run))
    db.execute('UPDATE runs SET stats_json=? WHERE id=?',
               (json.dumps(stats, ensure_ascii=False), run_id))
    db.commit()


class _PublicationFailure(Exception):
    """Keep publication failures outside the source/item error accounting."""

    def __init__(self, cause):
        super().__init__(str(cause))
        self.cause = cause


def run(db, config, data_root, fetcher=None, only=None, on_record=None, *, at=None):
    sources = config['sources']
    max_age_days = _validate_max_age_days(config.get('max_age_days', 30))
    run_at = at or datetime.now(timezone.utc)
    if not isinstance(run_at, datetime):
        raise TypeError('at must be a datetime')
    sync_sources(db, sources)
    fetcher = fetcher or Fetcher(config.get('timeout_seconds', 20), config.get('delay_seconds', 1))
    started = run_at.isoformat(timespec='seconds')
    run_id = db.execute('INSERT INTO runs(started_at,status) VALUES (?,?)',
                        (started, 'running')).lastrowid
    db.commit()
    stats = {'new': 0, 'changed': 0, 'markup_changed': 0, 'unchanged': 0,
             'filtered': 0, 'source_failed': 0, 'item_failed': 0}
    for source in sources:
        if only and source['id'] not in only:
            continue
        if not source.get('enabled'):
            continue
        source_run = db.execute('''INSERT INTO source_runs(run_id,source_id,started_at,status)
                                   VALUES (?,?,?,?)''',
                                (run_id, source['id'], now(), 'running')).lastrowid
        db.commit()
        errors = []
        found = fetched = 0
        try:
            entries = listing(fetcher, source)
            found = len(entries)
            _save_progress(db, run_id, source_run, found, fetched, stats)
            for entry in entries:
                keywords = source.get('include_keywords', [])
                if keywords and not any(word in entry.get('title', '') for word in keywords):
                    _record_filtered(db, run_id, source, entry, 'title_keyword_miss',
                                     at=run_at, title=entry.get('title'))
                    stats['filtered'] += 1
                    _save_progress(db, run_id, source_run, found, fetched, stats)
                    continue
                reason = _listing_publication_rejection(
                    entry, at=run_at, max_age_days=max_age_days)
                if reason:
                    _record_filtered(db, run_id, source, entry, reason, at=run_at,
                                     title=entry.get('title'))
                    stats['filtered'] += 1
                    _save_progress(db, run_id, source_run, found, fetched, stats)
                    continue
                result = None
                try:
                    response = fetcher.get(entry['url'])
                    result = ingest(db, data_root, run_id, source, entry, response,
                                    max_age_days=max_age_days, at=run_at)
                except Exception as exc:
                    errors.append(f"{entry['url']}: {type(exc).__name__}: {exc}")
                    stats['item_failed'] += 1
                    _save_progress(db, run_id, source_run, found, fetched, stats)
                    continue
                stats[result] += 1
                if result != 'filtered':
                    fetched += 1
                _save_progress(db, run_id, source_run, found, fetched, stats)
                if result == 'filtered':
                    continue
                # Publishing is deliberately outside the item-error handler.
                # A failed snapshot must not be counted as a network/item
                # failure, and must prevent this run from being reported as
                # complete.
                if on_record is not None:
                    try:
                        on_record()
                    except Exception as exc:
                        raise _PublicationFailure(exc) from exc
        except Exception as exc:
            if isinstance(exc, _PublicationFailure):
                raise exc.cause
            errors.append(f'{type(exc).__name__}: {exc}')
        status = 'failed' if found == 0 or (fetched == 0 and errors) else 'partial' if errors else 'success'
        if status != 'success':
            stats['source_failed'] += 1
        finished = now()
        message = '\n'.join(errors)[:4000] if errors else None
        db.execute('''UPDATE source_runs SET finished_at=?,status=?,found_count=?,
                       fetched_count=?,error=? WHERE id=?''',
                   (finished, status, found, fetched, message, source_run))
        if status == 'success':
            db.execute('''UPDATE sources SET last_success_at=?,last_error=NULL
                          WHERE id=?''', (finished, source['id']))
        else:
            db.execute('''UPDATE sources SET last_failure_at=?,last_error=?
                          WHERE id=?''', (finished, message, source['id']))
        db.commit()
    state = 'partial' if stats['source_failed'] else 'success'
    relink_projects(db)
    db.execute('UPDATE runs SET finished_at=?,status=?,stats_json=? WHERE id=?',
               (now(), state, json.dumps(stats, ensure_ascii=False), run_id))
    db.commit()
    return run_id, stats


def reprocess_saved(db):
    """Refresh rule-extracted fields from saved current-version text without fetching."""
    rows = db.execute('''SELECT r.*,s.category,v.body_text FROM records r
                         JOIN sources s ON s.id=r.source_id
                         JOIN versions v ON v.record_id=r.id AND v.version=r.current_version''').fetchall()
    for row in rows:
        fields = extract(row['title'], row['body_text'] or '', row['category'], row['published_at'])
        if row['category'] == 'procurement':
            fields['notice_number'] = fields['notice_number'] or row['notice_number']
            fields['deadline_at'] = fields['deadline_at'] or row['deadline_at']
            fields['buyer'] = fields['buyer'] or row['buyer']
        if row['category'] == 'news' and not fields['company']:
            fields['company'] = row['company']
        key = project_key(fields['buyer'], fields['project_name'], row['source_id'])
        db.execute('''UPDATE records SET buyer=?,notice_number=?,deadline_at=?,company=?,
                      equipment_json=?,facts_json=?,inferences_json=?,project_key=? WHERE id=?''',
                   (fields['buyer'], fields['notice_number'], fields['deadline_at'], fields['company'],
                    json_value(fields['equipment']), json_value(fields['facts']),
                    json_value(fields['inferences']), key, row['id']))
    relink_projects(db)
    db.commit()
    return len(rows)
