from backend.procurement.milestones import submission_deadline


def test_deadline_requires_current_sourced_unambiguous_submission():
    reading = {'status': 'partial', 'unresolved': ['招标文件待获取'], 'milestones': [
        {'kind': 'document_access', 'at': '2026-10-19T16:00:00+08:00', 'sources': [{'quote': '原文'}]},
        {'kind': 'submission', 'at': '2026-10-23T13:30:00+08:00', 'sources': [{'quote': '原文'}]},
    ]}
    assert submission_deadline(reading).isoformat() == '2026-10-23T13:30:00+08:00'
    assert submission_deadline({**reading, 'status': 'pending'}) is None
    assert submission_deadline({**reading, 'stale': True}) is None
    assert submission_deadline({**reading, 'conflicts': ['更正适用范围待确认']}) is None
    assert submission_deadline({'status': 'complete', 'milestones': [
        {'kind': 'submission', 'at': '2026-10-23T13:30:00+08:00', 'sources': []},
    ]}) is None
    reading['milestones'].append({'kind': 'submission', 'at': '2026-10-24T13:30:00+08:00',
                                  'sources': [{'quote': '另一标包'}]})
    assert submission_deadline(reading) is None
