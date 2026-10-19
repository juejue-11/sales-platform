"""Regression coverage for dated notices whose saved reading lost its nodes."""
import asyncio
import json
from pathlib import Path

import pytest

from backend.procurement.reading import build_material, normalize_reading
from backend.application.service import ScreeningService
from backend.procurement.schedule import restore_milestones


@pytest.mark.parametrize('at', ['2026-10-13T09:00:00+08:00', '2026年10月13日9:00'])
def test_single_digit_source_hour_and_chinese_model_date_are_equivalent(at):
    body = '发售时间：\n2026年\n10\n月\n13\n日\n9:00 - 2026年\n10\n月\n19\n日\n16:00'
    material = build_material([{'id': 1, 'kind': 'tender', 'body_text': body}], [], version='test')
    reading = normalize_reading({'milestones': [{'kind': 'document_access', 'label': '文件发售开始',
        'at': at, 'sources': [{'source_kind': 'announcement', 'record_id': 1, 'quote': body}]}]}, material)
    assert len(reading['milestones']) == 1
    assert reading['milestones'][0]['at'] == '2026-10-13T09:00:00+08:00'


def test_date_cannot_borrow_hour_from_other_end_of_range():
    body = '发售时间：2026年10月13日9:00 - 2026年10月19日16:00'
    material = build_material([{'id': 1, 'kind': 'tender', 'body_text': body}], [], version='test')
    reading = normalize_reading({'milestones': [{'kind': 'document_access', 'label': '文件发售开始',
        'at': '2026-10-13T16:00:00+08:00',
        'sources': [{'source_kind': 'announcement', 'record_id': 1, 'quote': body}]}]}, material)
    assert reading['milestones'] == []


def test_saved_empty_reading_recovers_current_notice_dates_without_model_or_db_write(tmp_path):
    record = json.loads((Path(__file__).parent / 'fixtures/notice_224.json').read_text(encoding='utf-8'))
    record.update(source_id='test', project_name='notice', attachments=[])
    source = tmp_path / 'records.jsonl'
    source.write_text(json.dumps(record, ensure_ascii=False), encoding='utf-8')
    service = ScreeningService(db_path=tmp_path / 'db.sqlite3', source_path=source,
                               archive_root=tmp_path, provider=object())
    try:
        asyncio.run(service.import_file())
        project_id = service._project_for_record_id(224)
        version = service._reading_material(project_id)['material_version']
        service._save_procurement_reading(project_id, {'version': 1, 'status': 'partial',
            'summary': 'saved explanation', 'sections': [], 'milestones': [], 'unresolved': []},
            material_version=version)
        before = service.db.total_changes
        reading = service._procurement_reading_for_project(project_id)
        assert reading['summary'] == 'saved explanation'
        assert {(m['kind'], m['at']) for m in reading['milestones']} >= {
            ('document_access', '2026-10-19T16:00:00+08:00'),
            ('submission', '2026-10-23T13:30:00+08:00'),
            ('opening', '2026-10-23T13:30:00+08:00'),
        }
        assert all(s['quote'] in record['body_text'] for m in reading['milestones'] for s in m['sources'])
        assert service.db.total_changes == before
        assert service._procurement_reading_for_project(project_id)['milestones'] == reading['milestones']
    finally:
        service.close()


def recovered(body, **reading_fields):
    material = build_material([{'id': 1, 'kind': 'tender', 'body_text': body}], [], version='test')
    return restore_milestones({'status': 'partial', 'milestones': [], **reading_fields}, material)


@pytest.mark.parametrize('body, expected', [
    ('投标文件递交的截止时间为2026年10月20日08时00分。', '2026-10-20T08:00:00+08:00'),
    ('递交投标文件截止时间：2026年10月29日09:30时止。', '2026-10-29T09:30:00+08:00'),
    ('响应文件的递交\n递交截止时间：2026年10月28日09:00\n递交方式：详见采购文件', '2026-10-28T09:00:00+08:00'),
])
def test_different_notice_submission_labels_recover(body, expected):
    assert [(m['kind'], m['at']) for m in recovered(body)['milestones']] == [('submission', expected)]


def test_document_window_does_not_borrow_daily_hours_or_create_submission():
    result = recovered('发售时间：2026年10月8日至2026年10月14日每日9:00时至17:00时。')
    assert [(m['kind'], m['at']) for m in result['milestones']] == [
        ('document_access', '2026-10-08'), ('document_access', '2026-10-14')]


def test_ansteel_document_window_with_chinese_hours_and_padded_month():
    result = recovered('4.招标文件的获取\n4.1凡有意参加投标者，请于2026年09月29日08时00分至'
                       '2026年10月20日08时00分(北京时间，下同)，登录采购平台下载电子招标文件。')
    assert [(m['kind'], m['at']) for m in result['milestones']] == [
        ('document_access', '2026-09-29T08:00:00+08:00'),
        ('document_access', '2026-10-20T08:00:00+08:00')]


@pytest.mark.parametrize('body', [
    '投标保证金递交时间：2026年10月23日13:30。',
    '递交截止时间：详见采购文件。业绩要求：2026年1月1日后签订合同。',
    '投标截止时间：另行通知。开标地点：2026园区。',
    '历史项目2026年1月1日开标，本次截止另行通知。',
    '递交截止时间：另行通知，历史项目为2026年1月1日。',
])
def test_unrelated_or_missing_dates_do_not_create_nodes(body):
    assert recovered(body)['milestones'] == []


@pytest.mark.parametrize('fields', [{'stale': True}, {'status': 'pending'}, {'status': 'failed'}])
def test_stale_or_failed_reading_does_not_gain_verified_dates(fields):
    assert recovered('递交截止时间：2026年10月23日13:30。', **fields)['milestones'] == []


def test_duplicate_dates_merge_and_conflicting_deadlines_remain_distinct():
    from backend.procurement.milestones import submission_deadline
    existing = {'id': 'saved', 'kind': 'submission', 'at': '2026-10-20T08:00',
                'label': '', 'sources': [{'quote': 'saved'}]}
    result = recovered('递交截止时间：2026年10月20日08时00分。', milestones=[existing])
    assert len(result['milestones']) == 1
    assert result['milestones'][0]['label']
    conflict = recovered('递交截止时间：2026年10月21日08时00分。', milestones=[existing])
    assert len(conflict['milestones']) == 2
    assert submission_deadline(conflict) is None


def test_prompt_specifies_milestone_date_contract():
    from backend.procurement.reading import build_reading_messages
    prompt = build_reading_messages({'sources': [], 'segments': []})[0]['content']
    assert '"at":"2026-10-23T13:30:00+08:00"' in prompt
    assert 'YYYY-MM-DD' in prompt


def test_news_does_not_become_procurement_schedule():
    material = build_material([{'id': 1, 'kind': 'news',
        'body_text': '去年项目投标截止时间：2026年10月20日08时00分。'}], [], version='test')
    assert restore_milestones({'status': 'partial', 'milestones': []}, material)['milestones'] == []
