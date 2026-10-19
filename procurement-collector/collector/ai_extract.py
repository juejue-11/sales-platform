"""Optional evidence-checked free-text extraction via OpenAI Responses API.

The scheduled collector runs without this module when no API key/model is configured.
"""

import json
import os
import re

import requests


EVIDENCE_ITEM = {
    'type': 'object',
    'properties': {'text': {'type': 'string'}, 'evidence': {'type': 'string'}},
    'required': ['text', 'evidence'], 'additionalProperties': False,
}
SCHEMA = {
    'type': 'object',
    'properties': {
        'equipment': {'type': 'array', 'items': EVIDENCE_ITEM},
        'facts': {'type': 'array', 'items': EVIDENCE_ITEM},
        'inferences': {'type': 'array', 'items': EVIDENCE_ITEM},
    },
    'required': ['equipment', 'facts', 'inferences'],
    'additionalProperties': False,
}


def configured():
    return bool(os.getenv('OPENAI_API_KEY') and os.getenv('OPENAI_MODEL'))


def normalized(text):
    return re.sub(r'\s+', '', text or '')


def validate(payload, body, category):
    if not isinstance(payload, dict):
        raise ValueError('AI output must be an object')
    result = {'equipment': [], 'facts': [], 'inferences': []}
    source = normalized(body)
    for key in result:
        values = payload.get(key, [])
        if not isinstance(values, list):
            raise ValueError(f'AI {key} must be a list')
        for item in values:
            if not isinstance(item, dict):
                continue
            value = str(item.get('text') or '').strip()
            evidence = str(item.get('evidence') or '').strip()
            if not value or not evidence or normalized(evidence) not in source:
                continue
            if key == 'equipment' and normalized(value) not in normalized(evidence):
                continue
            if key == 'inferences':
                if category != 'news':
                    continue
                result[key].append({'text': value, 'evidence': evidence, 'status': '待核实'})
            else:
                result[key].append({'text': value, 'evidence': evidence})
    return result


def request_extraction(body, category, session=None):
    api_key = os.environ['OPENAI_API_KEY']
    model = os.environ['OPENAI_MODEL']
    prompt = (
        '从以下中文原文中抽取设备/机型、明确发生的事实、可能的需求推测。'
        '每一项必须给出逐字原文证据片段。未写明的字段留空，不猜测采购、金额或日期。'
        '需求推测只用于新闻，不能说成已经发布的采购公告。'
        '如果没有证据，返回空数组。\n'
        f'资料类别：{category}\n原文：\n{body[:16000]}'
    )
    client = session or requests.Session()
    response = client.post(
        'https://api.openai.com/v1/responses',
        headers={'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'},
        json={'model': model, 'input': prompt, 'store': False,
              'text': {'format': {'type': 'json_schema', 'name': 'mining_extraction',
                                  'strict': True, 'schema': SCHEMA}}},
        timeout=60,
    )
    response.raise_for_status()
    data = response.json()
    if data.get('status') != 'completed':
        raise ValueError(f"AI response status: {data.get('status')}")
    chunks = [content.get('text', '') for output in data.get('output', [])
              if output.get('type') == 'message'
              for content in output.get('content', []) if content.get('type') == 'output_text']
    if not chunks:
        raise ValueError('AI response has no output_text')
    return validate(json.loads(''.join(chunks)), body, category)


def merge(fields, ai):
    fields['equipment'] = sorted(set(fields['equipment']) | {x['text'] for x in ai['equipment']})
    for key in ('facts', 'inferences'):
        existing = {(x['text'], x['evidence']) for x in fields[key]}
        for item in ai[key]:
            if (item['text'], item['evidence']) not in existing:
                fields[key].append(item)
    return fields
