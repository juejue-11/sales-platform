from __future__ import annotations

import asyncio
import base64
import json

import httpx
import pytest

from backend.application.service import ScreeningService
from backend.application.ingest import _merge_projects
from backend.main import create_app


@pytest.fixture
def desk(tmp_path):
    source = tmp_path / 'records.jsonl'
    source.write_text(json.dumps({'id': 224, 'source_id': 'test', 'kind': 'tender',
                                 'title': '破碎机采购', 'body_text': '投标前获取文件，提供授权证明。',
                                 'equipment': ['破碎机'], 'current_version': 1}), encoding='utf-8')
    service = ScreeningService(db_path=tmp_path / 'desk.sqlite3', source_path=source,
                               archive_root=tmp_path, provider=object())
    service.update_settings({'ai': {'enabled': False}, 'import': {'auto_import': False}})
    asyncio.run(service.import_file())
    source_ref = {'source_kind': 'announcement', 'record_id': 224,
                  'quote': '投标前获取文件，提供授权证明。'}
    reading = {'version': 1, 'material_version': 'v1', 'status': 'partial', 'stale': False,
               'summary': '采购破碎机', 'demand_type': 'confirmed', 'stage': '招标',
               'sections': [{'id': 'participation', 'title': '怎样参与', 'requirements': [
                   {'id': 'obtain', 'text': '投标前获取文件', 'explanation': '先办理获取文件',
                    'sources': [source_ref]},
               ]}, {'id': 'materials', 'title': '需要什么材料', 'requirements': [
                   {'id': 'authorization', 'text': '提供授权证明', 'explanation': '代理商提供授权',
                    'sources': [source_ref]},
               ]}], 'milestones': [], 'coverage': [], 'conflicts': [], 'unresolved': ['招标文件待获取']}
    original = service.project_detail
    service.project_detail = lambda project_id: {**original(project_id), 'procurement_reading': reading}
    app = create_app(service)
    yield service, app, reading
    service.close()


def request(app, method, url, **kwargs):
    async def execute():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            return await client.request(method, url, **kwargs)
    return asyncio.run(execute())


def test_file_supplement_and_correction_http_roundtrip(desk):
    service, app, _ = desk
    raw = '资格条款补充。'.encode()
    response = request(app, 'POST', '/api/projects/224/documents/supplement', json={
        'record_id': 224, 'name': '补充.txt', 'content_base64': base64.b64encode(raw).decode()})
    assert response.status_code == 200, response.text
    document_id = response.json()['document']['id']
    saved = request(app, 'GET', f'/api/projects/224/documents/{document_id}/uploaded-file')
    assert saved.content == raw
    assert 'attachment' in saved.headers['content-disposition']
    assert request(app, 'GET', f'/api/projects/999/documents/{document_id}/uploaded-file').status_code == 404
    correction = request(app, 'POST', '/api/projects/224/reading/corrections', json={
        'requirement_id': 'authorization', 'text': '制造商无需代理授权', 'reason': '资格条款例外'})
    assert correction.status_code == 200, correction.text
    assert request(app, 'GET', '/api/projects/224/reading/corrections').json()['corrections'][0]['material_version'] == 'v1'
    assert request(app, 'POST', '/api/projects/224/reading/corrections', json={
        'requirement_id': 'unknown', 'text': 'x', 'reason': 'x'}).status_code == 400
    assert service.db.execute('SELECT count(*) FROM procurement_uploads').fetchone()[0] == 1


def test_preview_requires_adoption_and_submission_requires_actual_details(desk):
    _, app, reading = desk
    preview = request(app, 'GET', '/api/projects/224/followup')
    assert preview.status_code == 200, preview.text
    assert preview.json()['tasks'] == []
    plan = preview.json()['plan']
    assert plan and plan['tasks']
    adopted = request(app, 'POST', '/api/projects/224/followup/adopt', json={'plan_id': plan['id']})
    assert adopted.status_code == 200, adopted.text
    tasks = adopted.json()['tasks']
    assert tasks
    task_id = tasks[0]['id']
    skipped = request(app, 'PATCH', f'/api/projects/224/followup/tasks/{task_id}', json={'status': 'skipped'})
    assert skipped.status_code == 400, skipped.text
    done = request(app, 'PATCH', f'/api/projects/224/followup/tasks/{task_id}',
                   json={'status': 'done', 'completion_note': '已获取文件'})
    assert done.status_code == 200, done.text
    assert done.json()['stage'] != 'submitted'
    assert request(app, 'PUT', '/api/projects/224/followup', json={'stage': 'submitted'}).status_code == 400
    submitted = request(app, 'PUT', '/api/projects/224/followup', json={
        'stage': 'submitted', 'submitted_at': '2026-10-14T10:00:00+08:00', 'submission_channel': '采购平台'})
    assert submitted.status_code == 200, submitted.text
    reading['material_version'] = 'v2'
    reading['sections'][0]['requirements'][0]['text'] = '重新获取更正文件'
    changed = request(app, 'GET', '/api/projects/224/followup').json()
    assert changed['stage'] == 'submitted'
    assert changed['submitted_at'] == '2026-10-14T10:00:00+08:00'
    assert next(task for task in changed['tasks'] if task['id'] == task_id)['status'] == 'done'
    assert changed['needs_review']
    updated = request(app, 'POST', '/api/projects/224/followup/adopt',
                      json={'plan_id': changed['plan']['id']})
    assert updated.status_code == 200, updated.text
    assert updated.json()['submitted_at'] == changed['submitted_at']
    assert next(task for task in updated.json()['tasks'] if task['id'] == task_id)['status'] == 'done'
    assert request(app, 'PATCH', '/api/projects/224/followup/tasks/unknown', json={'status': 'done'}).status_code == 404


def test_existing_human_following_choice_retains_owner_without_inventing_actions(desk):
    service, app, _ = desk
    service.save_decision(224, {'status': 'following', 'owner': '原负责人',
                              'next_action': '联系采购方', 'note': '已电话沟通'})
    state = request(app, 'GET', '/api/projects/224/followup').json()
    assert state['is_following']
    assert state['owner'] == '原负责人'
    assert state['next_action'] == '联系采购方'
    assert state['tasks'] == []
    assert state['submitted_at'] is None


def test_followup_list_filter_and_dashboard_use_explicit_user_choice(desk):
    _, app, _ = desk
    # Opening a proposal does not add the project to the user's follow-up list.
    assert request(app, 'GET', '/api/projects/224/followup').status_code == 200
    assert request(app, 'GET', '/api/projects?is_following=true').json()['total'] == 0
    assert request(app, 'GET', '/api/dashboard').json()['followup_count'] == 0
    assert request(app, 'PUT', '/api/projects/224/followup', json={
        'is_following': True, 'stage': 'working', 'owner': '负责同事',
    }).status_code == 200
    filtered = request(app, 'GET', '/api/projects?is_following=true&followup_stage=working').json()
    assert filtered['total'] == 1
    assert filtered['items'][0]['followup_stage'] == 'working'
    assert request(app, 'GET', '/api/projects?followup_stage=submitted').json()['total'] == 0
    assert request(app, 'GET', '/api/dashboard').json()['followup_count'] == 1


def test_importer_project_merge_retains_submissions_uploads_and_reading_history(desk):
    service, app, _ = desk
    source = service.source_path
    source.write_text(source.read_text(encoding='utf-8') + '\n' + json.dumps({
        'id': 225, 'source_id': 'test', 'kind': 'tender', 'title': '另一采购项目',
        'body_text': '另一项目采购设备。', 'notice_number': 'P225', 'project_key': 'separate-225',
    }), encoding='utf-8')
    asyncio.run(service.import_file())
    assert service._project_for_record_id(225) == 225
    state = request(app, 'GET', '/api/projects/224/followup').json()
    state = request(app, 'POST', '/api/projects/224/followup/adopt', json={'plan_id': state['plan']['id']}).json()
    task_id = state['tasks'][0]['id']
    assert request(app, 'PATCH', f'/api/projects/224/followup/tasks/{task_id}',
                   json={'status': 'done', 'completion_note': '已经办理'}).status_code == 200
    assert request(app, 'PUT', '/api/projects/224/followup', json={
        'owner': '负责同事', 'stage': 'submitted', 'submitted_at': '2026-10-14T10:00:00+08:00',
        'submission_channel': '采购平台',
    }).status_code == 200
    raw = '补充资格条件。'.encode()
    uploaded = request(app, 'POST', '/api/projects/224/documents/supplement', json={
        'record_id': 224, 'name': '补充.txt', 'content_base64': base64.b64encode(raw).decode(),
    }).json()['document']['id']
    with service.db:
        for project_id in (224, 225):
            service.db.execute("INSERT INTO procurement_readings(project_id,material_version,status,created_at,updated_at) "
                               "VALUES (?,'retained-test','complete','now','now')", (project_id,))
        _merge_projects(service.db, 224, 225)
    merged = request(app, 'GET', '/api/projects/225/followup').json()
    assert merged['stage'] == 'submitted'
    assert merged['owner'] == '负责同事'
    assert merged['submitted_at'] == '2026-10-14T10:00:00+08:00'
    assert next(task for task in merged['tasks'] if task['id'] == task_id)['status'] == 'done'
    assert merged['needs_review']
    assert request(app, 'GET', f'/api/projects/225/documents/{uploaded}/uploaded-file').content == raw
    assert service.db.execute("SELECT count(*) FROM procurement_readings WHERE project_id=225 AND material_version='retained-test'").fetchone()[0] == 2
    assert service.db.execute('PRAGMA foreign_key_check').fetchall() == []
