"""Recover explicit announcement dates without depending on model formatting.

Only labelled procurement clauses with complete dates qualify. Quotes retain
the exact source text; ambiguous dates remain separate rather than choosing a
project-wide deadline. Historical readings can use this as a read-only repair.
"""
from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from ..core.utils import sha256_text
from .dates import dates_in_quote, normalize_timestamp


_MARKERS = re.compile(
    r'(?P<document_access>(?:招标|采购)文件的获取|(?:文件)?发售时间|(?:文件)?获取时间)'
    r'|(?P<submission>递交(?:的)?(?:截止)?时间|提交(?:的)?截止时间|'
    r'投标(?:文件)?(?:递交)?截止时间|响应文件(?:递交)?截止时间)'
    r'|(?P<opening>开标时间)'
    r'|(?P<question>(?:提问|澄清|答疑)截止时间)'
    r'|(?P<registration>报名截止时间)'
)
_LABELS = {'document_access': '文件获取', 'submission': '投标 / 响应文件递交截止',
           'opening': '开标', 'question': '提问 / 澄清截止', 'registration': '报名截止'}


def announcement_milestones(material: Mapping[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for source in material.get('sources', []):
        if (not isinstance(source, Mapping) or source.get('source_kind') != 'announcement'
                or source.get('kind') not in {'tender', 'procurement', 'notice', 'purchase'}):
            continue
        text = str(source.get('text') or '')
        positions = [index for index, char in enumerate(text) if not char.isspace()]
        compact = ''.join(text[index] for index in positions)
        matches = list(_MARKERS.finditer(compact))
        for index, match in enumerate(matches):
            # A guarantee-payment deadline is not the bid submission deadline.
            if match.lastgroup == 'submission' and re.search('保证金|标书费|履约', compact[max(0, match.start()-12):match.start()]):
                continue
            end = min(len(compact), match.end() + 240,
                      matches[index + 1].start() if index + 1 < len(matches) else len(compact))
            clause = compact[match.start():end]
            boundary = re.search('[。；;]', clause)
            if boundary:
                end = match.start() + boundary.end()
            prefix = compact[match.end():end]
            first_date = re.search(r'\d{4}(?:年|[./-])\d{1,2}', prefix)
            if (not first_date or first_date.start() > 80
                    or re.search('另行|详见|待定|未定|参见|历史|不适用', prefix[:first_date.start()])):
                continue
            quote = text[positions[match.start()]:positions[end-1] + 1]
            dates = list(dict.fromkeys(dates_in_quote(quote)))
            kind = str(match.lastgroup)
            # A singular cutoff must not silently pick one of multiple dates.
            if not dates or len(dates) > (2 if kind == 'document_access' else 1):
                continue
            for date_index, at in enumerate(dates):
                label = _LABELS[kind]
                if kind == 'document_access':
                    # A lone date is only a cutoff when the text says so.
                    if len(dates) == 1 and not re.search('截止|截至', quote):
                        continue
                    label += '开始' if len(dates) == 2 and date_index == 0 else '截止'
                source_ref = {'source_kind': 'announcement', 'record_id': source.get('record_id'),
                              'quote': quote}
                if source.get('sha256'):
                    source_ref['sha256'] = source['sha256']
                identity = f"{source.get('record_id')}:{kind}:{at}:{label}"
                result.append({'id': 'announcement-date-' + sha256_text(identity)[:16],
                               'kind': kind, 'label': label, 'at': at, 'sources': [source_ref]})
    return result


def restore_milestones(reading: Mapping[str, Any], material: Mapping[str, Any]) -> dict[str, Any]:
    """Add evidenced dates to a current saved reading, leaving storage intact."""
    value = dict(reading)
    if reading.get('stale') or reading.get('status') not in {'complete', 'partial'}:
        return value
    milestones = [dict(item) for item in reading.get('milestones', []) if isinstance(item, Mapping)]
    # Canonicalize older saved timestamps before comparing them with recovered
    # nodes. An existing 08:00 and an extracted 08:00:00 must not conflict.
    for item in milestones:
        item['at'] = normalize_timestamp(item.get('at')) or item.get('at')
        if not item.get('label'):
            item['label'] = _LABELS.get(item.get('kind'), '采购时间节点')
    for candidate in announcement_milestones(material):
        existing = next((item for item in milestones
                         if item.get('kind') == candidate['kind'] and item.get('at') == candidate['at']), None)
        if existing is not None:
            existing['sources'] = list(existing.get('sources') or [])
            for source in candidate['sources']:
                if source not in existing['sources']:
                    existing['sources'].append(source)
        else:
            milestones.append(candidate)
    value['milestones'] = sorted(milestones, key=lambda item: (str(item.get('at') or ''), str(item.get('id') or '')))
    return value
