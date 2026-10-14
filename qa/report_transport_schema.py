"""Transport schema for the existing v1 report, using DeepSeek's strict subset.

This only constrains serialization. Existing report/source validators remain
authoritative for evidence, unique IDs, sizes, and business meaning.
"""
from __future__ import annotations

from typing import Any


def _object(properties: dict[str, Any]) -> dict[str, Any]:
    return {'type': 'object', 'properties': properties,
            'required': list(properties), 'additionalProperties': False}


def _array(items: dict[str, Any]) -> dict[str, Any]:
    return {'type': 'array', 'items': items}


def strict_report_schema() -> dict[str, Any]:
    text = {'type': 'string'}
    strings = _array(text)
    body = _object({'record_id': {'type': 'integer', 'minimum': 1}, 'quote': text})
    document = _object({**body['properties'], 'document_id': text})
    research = _object({'source_ref': text, 'source_version': text, 'quote': text})
    evidence = _array({'anyOf': [body, document,
        _object({**document['properties'], 'page': {'type': 'integer', 'minimum': 1}}),
        research, _object({**research['properties'], 'page': {'type': 'integer', 'minimum': 1}})]})
    item = _object({'text': text, 'evidence': evidence})
    paragraph = _object({'type': {'type': 'string', 'enum': ['paragraph']},
                         'text': text, 'evidence': evidence})
    block_list = _object({'type': {'type': 'string', 'enum': ['list']}, 'items': _array(item)})
    table = _object({'type': {'type': 'string', 'enum': ['table']}, 'columns': strings,
                     'rows': _array(_object({'cells': strings, 'evidence': evidence}))})
    section = _object({'id': text, 'title': text, 'blocks': _array({'anyOf': [paragraph, block_list, table]})})
    opportunity = _object({'id': text,
        'kind': {'type': 'string', 'enum': ['explicit_demand', 'potential_opportunity']},
        'title': text, 'description': text, 'basis': text, 'uncertainties': strings,
        'next_actions': strings, 'evidence': evidence})
    gap = _object({'id': text, 'description': text,
        'status': {'type': 'string', 'enum': ['not_stated', 'not_read', 'read_failed', 'not_applicable']},
        'impact': text, 'next_action': text, 'worth_researching': {'type': 'boolean'}})
    option = _object({'id': text, 'title': text, 'description': text, 'suitable_for': text,
                      'tradeoffs': strings, 'unknowns': strings, 'next_action': text})
    return _object({'decision': {'type': 'string', 'enum': ['recommend', 'consider', 'verify', 'not_recommended']},
        'summary': text, 'demand_summary': text,
        'analysis_meta': _object({'company_match': {'type': 'string', 'enum': ['not_evaluated', 'partial', 'assessed']}}),
        'report': _object({'version': {'type': 'integer', 'const': 1}, 'sections': _array(section),
                           'opportunities': _array(opportunity), 'gaps': _array(gap)}),
        'options': _array(option)})


def strict_report_tool() -> dict[str, Any]:
    return {'type': 'function', 'function': {'name': 'submit_analysis', 'strict': True,
        'description': 'Submit the complete existing version=1 report as structured data. '
                       'Preserve supported facts and exact evidence quotes; do not perform external actions.',
        'parameters': strict_report_schema()}}
