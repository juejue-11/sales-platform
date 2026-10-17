"""Acceptance examples from the public notice referenced by the new design.

These validate the reading contract against real source text. They do not make
a paid model request or claim that a model always interprets it correctly.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

from backend.procurement.reading import build_material, normalize_reading


def example():
    notice = json.loads((Path(__file__).parent / 'fixtures' / 'notice_224.json').read_text(encoding='utf-8'))
    body = notice['body_text']
    qualification = body[body.index('3.3'):body.index('3.4')]
    access = body[body.index('发售时间：'):body.index('文件售价：')]
    submission = body[body.index('6.1'):body.index('6.2')]
    scope = body[body.index('2.6'):body.index('3.投标人资格要求')]
    source = lambda text: {'source_kind': 'announcement', 'record_id': 224, 'quote': text}
    payload = {
        'version': 1, 'summary': '本次采购02包破碎机及伴随服务。',
        'demand_type': 'confirmed', 'stage': 'bidding', 'buyer': notice['buyer'],
        'region': '新疆', 'category': ['破碎机'],
        'sections': [
            {'id': 'scope', 'title': '买什么', 'requirements': [
                {'id': 'scope-02', 'lot': '02包', 'text': '本次招标内容为02包。',
                 'explanation': '01包在项目介绍中提及，本次只采购02包。', 'sources': [source(scope)]}]},
            {'id': 'qualification', 'title': '谁能参加', 'requirements': [
                {'id': 'experience', 'lot': '02包', 'text': '02包应提供合同额不低于90万元的类似破碎机供货业绩。',
                 'explanation': '授权代理商提供制造商业绩；符合目录和主要参数的首台（套）装备，'
                                '提供有效证明材料及省级及以上目录的，视同满足业绩要求。',
                 'sources': [source(qualification)]}]},
        ],
        'milestones': [
            {'id': 'access', 'kind': 'document_access', 'label': '文件发售截止',
             'at': '2026-10-19T16:00:00+08:00', 'sources': [source(access)]},
            {'id': 'submission', 'kind': 'submission', 'label': '投标递交截止',
             'at': '2026-10-23T13:30:00+08:00', 'sources': [source(submission)]},
        ],
        'coverage': [], 'conflicts': [], 'unresolved': ['招标文件需要付费获取，材料清单待完善'],
    }
    return notice, payload


def test_fragmented_dates_and_current_lot_remain_distinct():
    notice, payload = example()
    reading = normalize_reading(payload, build_material([notice], [], version='fixture-v1'))
    dates = {entry['kind']: entry['at'] for entry in reading['milestones']}
    assert dates['document_access'] == '2026-10-19T16:00:00+08:00'
    assert dates['submission'] == '2026-10-23T13:30:00+08:00'
    scopes = reading['sections'][0]['requirements']
    assert {requirement.get('lot') for requirement in scopes} == {'02包'}
    requirement = reading['sections'][1]['requirements'][0]
    assert '首台（套）' in requirement['explanation']
    assert '省级及以上' in requirement['explanation']
    assert all(source['quote'] in notice['body_text'] for source in requirement['sources'])
    assert reading['status'] == 'partial'


def test_an_unrelated_quote_does_not_validate_a_fabricated_deadline():
    notice, payload = example()
    payload = copy.deepcopy(payload)
    payload['milestones'][1]['at'] = '2099-12-31T13:30:00+08:00'
    reading = normalize_reading(payload, build_material([notice], [], version='fixture-v1'))
    valid_dates = [entry['at'] for entry in reading['milestones']
                   if entry.get('sources') and entry.get('status') != 'uncertain']
    assert '2099-12-31T13:30:00+08:00' not in valid_dates
