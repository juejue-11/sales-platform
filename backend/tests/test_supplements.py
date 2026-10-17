from __future__ import annotations

import base64

import pytest

from backend.procurement.supplements import (
    SupplementError, corrections, ensure_schema, prepare_document,
    save_correction, save_document, uploaded_file,
)
from backend.storage.db import connect


@pytest.fixture
def db(tmp_path):
    connection = connect(tmp_path / 'test.sqlite3')
    ensure_schema(connection)
    connection.execute("INSERT INTO projects VALUES (1,'now','now')")
    connection.execute("INSERT INTO projects VALUES (2,'now','now')")
    connection.execute("INSERT INTO records(id,source_id,kind,title) VALUES (224,'test','tender','采购')")
    connection.execute("INSERT INTO project_records(project_id,record_id,added_at) VALUES (1,224,'now')")
    yield connection
    connection.close()


def payload(text='采购破碎机两套。'):
    return {'record_id': 224, 'name': '补充文件.txt',
            'content_base64': base64.b64encode(text.encode()).decode()}


def test_upload_preserves_original_and_is_project_scoped_and_idempotent(db):
    document, raw = prepare_document(payload())
    with db:
        save_document(db, 1, document, raw)
        save_document(db, 1, document, raw)
    assert document['text'] == '采购破碎机两套。'
    assert document['status'] == 'parsed'
    assert uploaded_file(db, 1, document['id'])['original_bytes'] == raw
    assert uploaded_file(db, 2, document['id']) is None
    assert db.execute('SELECT count(*) FROM procurement_uploads').fetchone()[0] == 1
    with pytest.raises(SupplementError, match='不属于'):
        save_document(db, 2, document, raw)


@pytest.mark.parametrize('change', [
    {'name': '../秘密.txt'}, {'name': 'a\\b.txt'}, {'name': '程序.exe'},
    {'content_base64': '!!'}, {'content_base64': ''}, {'record_id': True},
])
def test_invalid_uploads_are_rejected(change):
    with pytest.raises(SupplementError):
        prepare_document({**payload(), **change})


def test_unreadable_bytes_retained_for_manual_review(db):
    document, raw = prepare_document({**payload(), 'name': '扫描.pdf',
                                     'content_base64': base64.b64encode(b'%PDF-broken').decode()})
    with db:
        save_document(db, 1, document, raw)
    assert document['status'] != 'parsed'
    assert uploaded_file(db, 1, document['id'])['original_bytes'] == b'%PDF-broken'


def test_corrections_are_append_only_and_bound_to_material_version(db):
    with db:
        save_correction(db, 1, {'requirement_id': 'scope-1', 'text': '仅采购第一包', 'reason': '第二包是历史公告'},
                        material_version='v1', requirement_ids={'scope-1'})
        save_correction(db, 1, {'requirement_id': 'omission', 'text': '保留联合体例外', 'reason': '公告第三条说明'},
                        material_version='v2', requirement_ids=set())
    entries = corrections(db, 1)
    assert [entry['material_version'] for entry in entries] == ['v1', 'v2']
    assert entries[0]['text'] == '仅采购第一包'
    assert corrections(db, 2) == []
    with pytest.raises(SupplementError):
        save_correction(db, 1, {'requirement_id': 'other', 'text': 'x', 'reason': 'x'},
                        material_version='v1', requirement_ids={'scope-1'})
    with pytest.raises(SupplementError):
        save_correction(db, 1, {'requirement_id': 'omission', 'text': 'x', 'reason': ' '},
                        material_version='v1', requirement_ids=set())
