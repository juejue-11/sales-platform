import io
import json
import re
import time
import xml.etree.ElementTree as ET
from urllib.parse import urljoin, urlsplit, urlencode

import requests
from bs4 import BeautifulSoup
from pypdf import PdfReader

from .extract import html_text


USER_AGENT = 'MiningInformationCollector/1.0 (+public information; contact configured by operator)'


class Fetcher:
    def __init__(self, timeout=20, delay=1.0):
        self.session = requests.Session()
        self.session.headers.update({'User-Agent': USER_AGENT,
                                     'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.5'})
        self.timeout = timeout
        self.delay = delay
        self.last_request = 0.0

    def get(self, url):
        for attempt in range(3):
            if self.last_request:
                time.sleep(max(0, self.delay - (time.monotonic() - self.last_request)))
            try:
                response = self.session.get(url, timeout=self.timeout, allow_redirects=True)
                self.last_request = time.monotonic()
                response.raise_for_status()
                if urlsplit(response.url).scheme not in ('http', 'https'):
                    raise ValueError('Unexpected URL scheme')
                if len(response.content) > 25 * 1024 * 1024:
                    raise ValueError('Document exceeds 25 MB limit')
                return response
            except requests.HTTPError as exc:
                status = exc.response.status_code if exc.response is not None else None
                if status != 429 and (status is None or status < 500):
                    raise
                if attempt == 2:
                    raise
            except requests.RequestException:
                if attempt == 2:
                    raise
            time.sleep(2 ** (attempt + 1))

    def post_json(self, url, payload):
        for attempt in range(3):
            if self.last_request:
                time.sleep(max(0, self.delay - (time.monotonic() - self.last_request)))
            try:
                response = self.session.post(url, json=payload, timeout=self.timeout)
                self.last_request = time.monotonic()
                response.raise_for_status()
                if len(response.content) > 25 * 1024 * 1024:
                    raise ValueError('API response exceeds 25 MB limit')
                return response
            except requests.HTTPError as exc:
                status = exc.response.status_code if exc.response is not None else None
                if status != 429 and (status is None or status < 500):
                    raise
                if attempt == 2:
                    raise
            except requests.RequestException:
                if attempt == 2:
                    raise
            time.sleep(2 ** (attempt + 1))


def local_name(tag):
    return tag.rsplit('}', 1)[-1]


def feed_entries(raw, base):
    root = ET.fromstring(raw)
    entries = []
    for item in root.iter():
        if local_name(item.tag) not in ('item', 'entry'):
            continue
        fields = {}
        for child in item:
            key = local_name(child.tag)
            if key == 'link':
                fields['url'] = child.attrib.get('href') or (child.text or '').strip()
            elif key in ('title', 'pubDate', 'published', 'updated', 'description'):
                fields[key] = ''.join(child.itertext()).strip()
        if fields.get('url') and fields.get('title'):
            entries.append({'url': urljoin(base, fields['url']), 'title': fields['title'],
                            'published_at': fields.get('published') or fields.get('pubDate') or fields.get('updated')})
    return entries


def html_entries(raw, base, source):
    soup = BeautifulSoup(raw, 'html.parser')
    entries = []
    rows = soup.select(source['item_selector'])
    for row in rows:
        anchor = row.select_one(source.get('link_selector', 'a[href]'))
        if not anchor or not anchor.get('href'):
            continue
        url = urljoin(base, anchor['href'])
        if urlsplit(url).scheme not in ('http', 'https'):
            continue
        title_el = row.select_one(source['list_title_selector']) if source.get('list_title_selector') else None
        title = title_el.get_text(' ', strip=True) if title_el else None
        title = title or (anchor.get(source['title_attribute']) if source.get('title_attribute') else None)
        title = title or anchor.get_text(' ', strip=True) or row.get_text(' ', strip=True)
        if not title:
            continue
        date_el = row.select_one(source['date_selector']) if source.get('date_selector') else None
        entries.append({'url': url, 'title': title,
                        'published_at': date_el.get_text(' ', strip=True) if date_el else None})
    return entries


def search_api_entries(response, source):
    data = response.json()
    while isinstance(data, dict) and isinstance(data.get('content'), str):
        data = json.loads(data['content'])
    result = data.get('result', data)
    records = result.get('records', [])
    if not isinstance(records, list):
        raise ValueError('Search API records is not a list')
    entries = []
    for record in records:
        link = record.get('linkurl')
        title = record.get('searchtitle') or record.get('title')
        if not link or not title:
            continue
        url = urljoin(source['entry_url'], link)
        if source.get('append_guid') and record.get('gonggaoguid'):
            url += ('&' if '?' in url else '?') + urlencode({'gonggaoguid': record['gonggaoguid']})
        entries.append({'url': url, 'title': re.sub(r'<[^>]+>', '', title),
                        'published_at': record.get('webdate') or record.get('infodate'),
                        'notice_number': record.get('prono') or record.get('projectnumber'),
                        'deadline_at': record.get('zhaobiaoenddate') or record.get('endtime'),
                        'buyer_hint': record.get('zhaobiaoren') or record.get('wtdw')})
    return entries


def listing(fetcher, source):
    if source['adapter'] not in ('rss', 'html_list', 'post_json_list'):
        raise ValueError(f"Source adapter {source['adapter']} is not automated")
    entries = []
    if source['adapter'] == 'post_json_list':
        api_host = urlsplit(source['api_url']).hostname
        if api_host != urlsplit(source['entry_url']).hostname:
            raise ValueError('Search API host differs from source host')
        for payload in source['queries']:
            entries.extend(search_api_entries(fetcher.post_json(source['api_url'], payload), source))
    else:
        for page_url in [source['entry_url'], *source.get('page_urls', [])]:
            response = fetcher.get(page_url)
            if source['adapter'] == 'rss':
                entries.extend(feed_entries(response.content, response.url))
            else:
                entries.extend(html_entries(response.content, response.url, source))
    if not entries:
        raise ValueError('Listing produced zero entries; selector/feed may have changed')
    allowed = set(source.get('allowed_hosts') or [urlsplit(source['entry_url']).hostname])
    seen = set()
    entries = [x for x in entries if urlsplit(x['url']).hostname in allowed
               and not (x['url'] in seen or seen.add(x['url']))]
    if not entries:
        raise ValueError('All listing links fell outside allowed hosts')
    return entries[:source.get('max_items', 30)]


def detail(response, source):
    mime = response.headers.get('Content-Type', '').split(';')[0].lower()
    if response.content.startswith(b'%PDF') or mime == 'application/pdf':
        try:
            pages = PdfReader(io.BytesIO(response.content)).pages
            text = '\n'.join(page.extract_text() or '' for page in pages)
            if not text.strip():
                return '', 'needs_ocr', 'PDF contains no extractable text', 'application/pdf'
            return text, 'ok', None, 'application/pdf'
        except Exception as exc:
            return '', 'parse_error', str(exc)[:500], 'application/pdf'
    if mime and not ('html' in mime or 'xml' in mime or mime == 'text/plain'):
        return '', 'unsupported', f'Unsupported content type: {mime}', mime
    response.encoding = response.apparent_encoding or response.encoding
    try:
        text = html_text(response.text, source.get('content_selector')) if 'html' in mime or not mime else response.text
        return text, 'ok' if text.strip() else 'empty', None, mime or 'text/html'
    except Exception as exc:
        return '', 'parse_error', str(exc)[:500], mime or 'text/html'
