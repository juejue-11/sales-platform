import hashlib
import json
import re
from datetime import datetime
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from bs4 import BeautifulSoup


EQUIPMENT = ['旋回破碎机', '圆锥破碎机', '颚式破碎机', '破碎机', '衬板', '动锥', '定锥',
             '主轴', '轴承', '备件', '耐磨件', '磨机', '球磨机', '带式输送机', '输送机',
             '振动筛', '钻机', '渣浆泵', '水泵', '泵站', '平板车', '单轨吊', '装车站',
             '液压支架', '采煤机', '矿卡']
NEWS_SIGNALS = {
    '扩产': '该企业可能产生新增设备或备件需求',
    '技改': '该企业可能产生设备改造或备件需求',
    '检修': '该企业可能产生检修备件需求',
    '设备更新': '该企业可能产生设备更新需求',
}
TRACKING = re.compile(r'^(utm_|spm$|from$|source$|fbclid$)', re.I)
NOTICE = re.compile(r'(?:项目编号|招标编号|采购编号|公告编号)\s*[：:]\s*([^\s，,。；;]{4,60})')
NOTICE_LABEL = re.compile(r'(?:项目编号|招标编号|采购编号|公告编号)(?:\s*[：:]|为\s*[：:]?|是\s*[：:])\s*')
DATE_PATTERN = r'20\d{2}[-年/.]\d{1,2}[-月/.]\d{1,2}日?(?:\d{1,2}(?:[:：]\d{2}|时\d{1,2}分?))?'
DEADLINE = re.compile(r'(?:投标截止|响应文件提交截止|提交截止|报名截止|截止时间)[^。]{0,30}?(' + DATE_PATTERN + r')')
SUBMISSION = re.compile(r'(?:投标文件|响应文件|报价文件)(?:应于|须于|在)(' + DATE_PATTERN + r')之前(?:递交|提交|上传)')
BUYER = re.compile(r'(?:采购人|招标人|采购单位|建设单位)(?:名称)?(?:为|是|[：:])([^，,。；;]{2,100})')
MODEL = re.compile(r'\b[A-Z]{1,5}[- ]?\d{2,5}[A-Z0-9-]*\b')
MODEL_CONTEXT = re.compile(r'(?:型号|规格|机型)[：:]?[^\n。；;]{0,30}')
BUYER_MARKERS = ('公司', '集团', '研究院', '设计院', '矿', '中心', '政府', '厂', '大学', '学校', '医院', '管理局', '采购部')
PUBLISHED = re.compile(r'(?:发布时间|发布日期|公告日期)\s*[：:]\s*(20\d{2}[-年/.]\d{1,2}[-月/.]\d{1,2})')
NEGATION = re.compile(r'(?:未|没有|否认|取消|暂停|终止|不再).{0,8}$')


def canonical_url(url):
    p = urlsplit(url.strip())
    query = urlencode(sorted((k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
                           if not TRACKING.match(k)))
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path.rstrip('/') or '/', query, ''))


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def date_text(value):
    if not value:
        return None
    m = re.search(r'(20\d{2})[-年/.](\d{1,2})[-月/.](\d{1,2})', value)
    if not m:
        try:
            return parsedate_to_datetime(value).date().isoformat()
        except (TypeError, ValueError, IndexError):
            return None
    try:
        d = datetime(int(m[1]), int(m[2]), int(m[3]))
    except ValueError:
        return None
    tail = value[m.end()-1:]
    tm = re.search(r'(?:\s|日)(\d{1,2})[:：](\d{2})', tail) or re.search(r'日(\d{1,2})时(\d{1,2})分?', tail)
    return d.strftime('%Y-%m-%d') + (f' {int(tm[1]):02d}:{tm[2]}' if tm else '')


def html_text(raw, selector=None):
    soup = BeautifulSoup(raw, 'html.parser')
    for tag in soup(['script', 'style', 'nav', 'footer', 'header']):
        tag.decompose()
    scope = soup.select_one(selector) if selector else None
    scope = scope or soup.select_one('article') or soup.select_one('main') or soup.body or soup
    text = scope.get_text('\n', strip=True)
    return re.sub(r'\n{3,}', '\n\n', text)


def clean_title(title):
    return re.sub(r'\s+', ' ', title or '').strip()


def kind_from_title(title, category):
    if category == 'news':
        return 'news'
    for word, kind in [('更正', 'correction'), ('变更', 'correction'), ('澄清', 'correction'),
                       ('中标', 'award'), ('成交', 'award'), ('询价', 'inquiry')]:
        if word in title:
            return kind
    return 'tender'


def notice_number(text):
    match = NOTICE_LABEL.search(text)
    if not match:
        return None
    lines = text[match.end():match.end() + 160].splitlines()
    parts = []
    for line in lines[:4]:
        candidate = line.strip(' \t（）()，,。；;')
        if not candidate:
            continue
        if re.match(r'^\d+(?:\.\d+)?(?:[^A-Za-z0-9_/-]|$)', candidate):
            break
        if parts and not re.fullmatch(r'[A-Za-z0-9_/-]+', candidate):
            break
        parts.append(candidate)
        if not re.fullmatch(r'[A-Za-z0-9_/-]+', candidate):
            break
    value = ''.join(parts)
    return value if len(value) >= 4 else None


def extract(title, text, category, published_at=None):
    combined = title + '\n' + text
    compact = re.sub(r'\s+', '', text)
    number = notice_number(text)
    title_number = re.search(r'(?<![A-Z0-9])[A-Z]{2,}[-/]\d{4,}(?!\d)', title)
    if title_number and (not number or number.endswith('-')):
        number = title_number.group()
    buyer_match = BUYER.search(compact)
    buyer = buyer_match[1].strip() if buyer_match else None
    if buyer:
        buyer = re.split(r'地址|联系人|联系方式|联系电话|招标代理|采购代理|电话|邮编|采购人|招标人|办公', buyer, maxsplit=1)[0]
    if buyer and not any(marker in buyer for marker in BUYER_MARKERS):
        buyer = None
    deadline = DEADLINE.search(compact) or SUBMISSION.search(compact)
    equipment_terms = {x for x in EQUIPMENT if x in combined}
    equipment_terms = {x for x in equipment_terms if not any(x != longer and x in longer
                                                              for longer in equipment_terms)}
    equipment = sorted(equipment_terms | {model for context in MODEL_CONTEXT.findall(combined)
                                          for model in MODEL.findall(context)})
    # A fact is always a quoted excerpt of saved source text, never an inferred purchase.
    lines = [re.sub(r'\s+', ' ', x).strip() for x in text.splitlines()]
    lines = [x for x in lines if len(x) >= 8]
    published_match = PUBLISHED.search(text)
    facts = [{'text': x[:300], 'evidence': x[:300]} for x in lines[:3]]
    inferences = []
    if category == 'news':
        for trigger, hypothesis in NEWS_SIGNALS.items():
            evidence = next((x[:300] for x in lines if trigger in x
                             and not NEGATION.search(x[:x.index(trigger)])), None)
            if evidence:
                inferences.append({'text': hypothesis, 'evidence': evidence,
                                   'status': '待核实', 'trigger': trigger})
    return {
        'notice_number': number,
        'buyer': buyer,
        'project_name': title,
        'company': buyer if category == 'news' else None,
        'deadline_at': date_text(deadline[1]) if deadline else None,
        'equipment': equipment,
        'facts': facts,
        'inferences': inferences,
        'published_at': date_text(published_at) or
                        (date_text(published_match[1]) if published_match else None),
    }


def project_key(buyer, project_name, source_id):
    if not buyer or not project_name:
        return None
    normalized = re.sub(r'[\s\W_]+|(?:更正|变更|澄清|中标|成交|公告|结果|候选人|公开|询价|招标|采购)', '', project_name)
    if len(normalized) < 8:
        return None
    return sha256(f'{source_id}|{buyer}|{normalized}'.encode())[:20]
