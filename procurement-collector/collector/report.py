import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .eligibility import REJECTION_LABELS
from .export import export_jsonl


LOCAL = ZoneInfo('Asia/Shanghai')


def local_date(iso):
    return datetime.fromisoformat(iso).astimezone(LOCAL).date()


def safe(value):
    return str(value or '未注明').replace('|', '｜').replace('\n', ' ').strip()


def item_line(row, *, changed=False):
    published = safe(row['published_at'])
    version = row['version'] if 'version' in row.keys() else row['current_version']
    parts = [f"- **{safe(row['title'])}**（{safe(row['source_name'])}，发布：{published}）",
             f"  原文：[查看公告]({row['canonical_url']})；记录 #{row['id']}，版本 {version}"]
    if row['buyer']:
        parts.append(f"  采购方：{safe(row['buyer'])}")
    if row['notice_number']:
        parts.append(f"  公告编号：{safe(row['notice_number'])}")
    if row['deadline_at']:
        parts.append(f"  截止：{safe(row['deadline_at'])}")
    equipment = json.loads(row['equipment_json'])
    if equipment:
        parts.append('  设备／机型：' + '、'.join(equipment))
    if row['possible_duplicate_of']:
        parts.append(f"  疑似重复：记录 #{row['possible_duplicate_of']}，待核对。")
    if row['related_record_id']:
        parts.append(f"  同项目关联：记录 #{row['related_record_id']}。")
    if changed:
        parts.append('  原文发生变化；请对照保存的版本核对具体内容。')
    return '\n'.join(parts)


def render(db, day=None):
    day = day or datetime.now(LOCAL).date()
    if isinstance(day, str):
        day = date.fromisoformat(day)
    rows = db.execute('''SELECT e.event_type,e.occurred_at,e.version,r.*,s.name AS source_name
                         FROM events e JOIN records r ON r.id=e.record_id
                         JOIN sources s ON s.id=r.source_id ORDER BY e.id''').fetchall()
    today = [r for r in rows if local_date(r['occurred_at']) == day]
    new_procurement = [r for r in today if r['event_type'] == 'new'
                       and r['kind'] != 'news' and not r['duplicate_of']]
    changes = [r for r in today if r['event_type'] == 'changed' and r['kind'] != 'news']
    markup_changes = [r for r in today if r['event_type'] == 'markup_changed']
    news = [r for r in today if r['kind'] == 'news' and r['event_type'] in ('new', 'changed')]
    filtered_rows = db.execute('''SELECT f.*,s.name AS source_name
                                  FROM filtered_entries f JOIN sources s ON s.id=f.source_id
                                  ORDER BY f.id''').fetchall()
    filtered_today = [r for r in filtered_rows if local_date(r['filtered_at']) == day]
    filtered_counts = {}
    filtered_source_counts = {}
    for row in filtered_today:
        filtered_counts[row['reason']] = filtered_counts.get(row['reason'], 0) + 1
        filtered_source_counts[row['source_name']] = filtered_source_counts.get(row['source_name'], 0) + 1
    deadline_rows = db.execute('''SELECT r.*,s.name AS source_name FROM records r
                                  JOIN sources s ON s.id=r.source_id
                                  WHERE r.deadline_at IS NOT NULL AND r.kind!='news'
                                  AND r.duplicate_of IS NULL ORDER BY r.deadline_at''').fetchall()
    upcoming = []
    for r in deadline_rows:
        try:
            deadline_day = date.fromisoformat(r['deadline_at'][:10])
        except ValueError:
            continue
        if day <= deadline_day <= day + timedelta(days=3):
            upcoming.append(r)
    source_runs = db.execute('''SELECT sr.*,s.name FROM source_runs sr JOIN sources s ON s.id=sr.source_id
                                ORDER BY sr.id''').fetchall()
    day_source_runs = [r for r in source_runs if local_date(r['started_at']) == day]
    latest_source_status = {r['source_id']: r['status'] for r in day_source_runs}
    failures = [r for r in day_source_runs if r['status'] != 'success']
    ran_source_ids = {r['source_id'] for r in source_runs if local_date(r['started_at']) == day}
    not_run = db.execute('SELECT * FROM sources WHERE enabled=1 ORDER BY id').fetchall()
    not_run = [r for r in not_run if r['id'] not in ran_source_ids]
    gaps = db.execute('''SELECT * FROM sources WHERE enabled=0 OR access_status NOT IN
                         ('public','authorized') ORDER BY id''').fetchall()
    parse_issues = db.execute('''SELECT v.*,r.title FROM versions v JOIN records r ON r.id=v.record_id
                                 WHERE v.parse_status!='ok' ORDER BY v.id''').fetchall()
    parse_issues = [r for r in parse_issues if local_date(r['fetched_at']) == day]
    # Unchanged notices keep their earlier version. Keep unresolved attachment
    # gaps visible on each day that notice was observed, even without a new version.
    attached_rows = db.execute('''SELECT r.id,r.current_version,r.last_seen_at,v.version,v.fetched_at,
                                  a.attachments_json FROM records r JOIN versions v ON v.record_id=r.id
                                  JOIN version_attachments a ON a.record_id=v.record_id
                                  AND a.version=v.version ORDER BY v.version''').fetchall()
    observed_attachments = {}
    for row in attached_rows:
        if (local_date(row['fetched_at']) == day or
                (row['version'] == row['current_version'] and local_date(row['last_seen_at']) == day)):
            observed_attachments[row['id']] = row
    attachment_issues = []
    for row in observed_attachments.values():
        for attachment in json.loads(row['attachments_json']):
            if attachment.get('status') != 'parsed':
                attachment_issues.append((row, attachment))
    lines = [f'# 招采与矿业资讯日报 · {day.isoformat()}', '',
             '日报是信息库的阅读视图；原始内容和历史版本保存在 `data/archive/` 与 SQLite 数据库中。', '',
             '“新增”表示今日首次入库，首次运行可能包含较早发布的公告；以每条发布日期为准。', '',
             '## ① 新增招采公告', '']
    lines += [item_line(r) for r in new_procurement] or ['今日没有**成功采集到的**新增公告。']
    lines += ['', '## ② 公告变更与临近截止', '']
    lines += ['### 原文变更', '']
    lines += [item_line(r, changed=True) for r in changes] or ['今日未检测到公告原文变更。']
    if markup_changes:
        lines += ['', '### 页面字节变化（正文未变）', '']
        lines += [f"- {safe(r['title'])}：记录 #{r['id']} v{r['version']}；已保存新网页版本，正文未变。"
                  for r in markup_changes]
    lines += ['', '### 未来三天截止', '']
    lines += [item_line(r) for r in upcoming] or ['信息库中暂无未来三天截止的公告。']
    lines += ['', '## ③ 行业新闻事实与潜在需求', '']
    if news:
        for r in news:
            lines.append(f"- **{safe(r['title'])}**（{safe(r['source_name'])}，发布：{safe(r['published_at'])}）")
            lines.append(f"  原文：[查看新闻]({r['canonical_url']})；记录 #{r['id']}")
            if r['company']:
                lines.append(f"  企业：{safe(r['company'])}")
            facts = json.loads(r['facts_json'])
            for fact in facts[:2]:
                lines.append(f"  事实（原文摘录）：{safe(fact['evidence'])}")
            for inference in json.loads(r['inferences_json']):
                lines.append(f"  **推测，待核实**：{safe(inference['text'])}；依据：{safe(inference['evidence'])}")
            if not facts:
                lines.append('  正文未提取出可用事实；请查看原文存档。')
    else:
        lines.append('今日没有**成功采集到的**行业新闻。')
    lines += ['', '## ④ 采集异常与覆盖缺口', '']
    if not failures and not parse_issues and not attachment_issues and not gaps and not not_run:
        lines.append('今日未记录采集异常或覆盖缺口。')
    for r in not_run:
        lines.append(f"- 今日未运行：**{safe(r['name'])}**；无法判断是否有新消息。")
    for r in failures:
        recovery = '；同日后续运行已恢复' if latest_source_status[r['source_id']] == 'success' else ''
        lines.append(f"- 来源 **{safe(r['name'])}**：{r['status']}；发现 {r['found_count']} 条，成功 {r['fetched_count']} 条；原因：{safe(r['error'])}{recovery}")
    for r in parse_issues:
        lines.append(f"- 正文解析异常：记录 #{r['record_id']} v{r['version']}，{safe(r['title'])}；{r['parse_status']}；{safe(r['parse_error'])}")
    reasons = {'attachment_count_limit': '超过单条公告最多4个附件的读取上限，其他附件请从原文获取',
               'attachment_too_large': '文件超过12 MiB下载上限',
               'needs_ocr': '原件已保存，扫描件需要OCR才能提取文字',
               'format_not_supported': '原件已保存，当前未支持此格式的文字解析'}
    for r, attachment in attachment_issues:
        error = attachment.get('error') or 'unknown'
        reason = reasons.get(error, error)
        link = f"；[查看来源]({attachment['url']})" if attachment.get('url') else ''
        lines.append(f"- 附件异常：记录 #{r['id']} v{r['version']}，{safe(attachment.get('name'))}；"
                     f"{safe(attachment.get('status'))}；{reason}（{error}）{link}")
    for r in gaps:
        lines.append(f"- 未覆盖：**{safe(r['name'])}**；接入状态 {r['access_status']}；{safe(r['access_note'])}")
    lines += ['', '## ⑤ 过滤记录', '']
    if filtered_today:
        lines.append(f'- 过滤数量：{len(filtered_today)}')
        for reason, count in sorted(filtered_counts.items()):
            lines.append(f'- 原因：{REJECTION_LABELS.get(reason, reason)}；数量：{count}')
        for source_name, count in sorted(filtered_source_counts.items()):
            lines.append(f'- 来源 {safe(source_name)}：{count}')
        for row in filtered_today:
            label = REJECTION_LABELS.get(row['reason'], row['reason'])
            lines.append(f"- **{safe(row['title'])}**（{safe(row['source_name'])}）；"
                         f"原因：{label}；"
                         f"发布日期：{safe(row['published_at'])}；"
                         f"[查看原文]({row['url']})")
    else:
        lines.append('- 过滤数量：0')
    lines += ['', '说明：没有成功采集的来源不能据此判断“没有新消息”。新闻需求信号均为待核实推测。', '']
    return '\n'.join(lines)


def save_report(db, data_root, day=None):
    day = day or datetime.now(LOCAL).date()
    if isinstance(day, str):
        day = date.fromisoformat(day)
    output = data_root / 'reports' / f'{day.isoformat()}.md'
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(render(db, day), encoding='utf-8')
    return output


def trial_status(db, data_root, days=14, today=None):
    first = db.execute('SELECT started_at FROM runs ORDER BY id LIMIT 1').fetchone()
    today = today or datetime.now(LOCAL).date()
    initial_start = local_date(first['started_at']) if first else today
    sources = db.execute('SELECT id,name FROM sources WHERE enabled=1 ORDER BY id').fetchall()
    runs = db.execute('SELECT source_id,started_at,status FROM source_runs ORDER BY id').fetchall()
    latest = {(r['source_id'], local_date(r['started_at'])): r['status'] for r in runs}

    def consecutive(source_ids):
        count = 0
        day = today
        while day >= initial_start and all(latest.get((source_id, day)) == 'success'
                                           for source_id in source_ids):
            count += 1
            day -= timedelta(days=1)
        return count

    all_streak = consecutive([s['id'] for s in sources]) if sources else 0
    lines = ['# 连续试运行状态', '',
             f'- 首次启动：{initial_start}；截至：{today}。',
             '- 以下只检查每日成功采集是否连续；漏采、编号、截止日、重复和变更仍需抽样核对。']
    if all_streak:
        streak_start = today - timedelta(days=all_streak - 1)
        target_end = streak_start + timedelta(days=days - 1)
        lines.append(f'- 当前连续周期：{streak_start} 起；连续成功 {all_streak}/{days} 天；'
                     f'最早完成日期：{target_end}。')
    else:
        lines.append('- 当前没有连续成功的采集周期。')
    missing = [initial_start + timedelta(days=i)
               for i in range(max(0, (today - initial_start).days + 1))
               if not sources or any(latest.get((s['id'], initial_start + timedelta(days=i))) != 'success'
                                     for s in sources)]
    if missing:
        shown = '、'.join(map(str, missing[-30:]))
        lines.append(f'- 历史缺失：共 {len(missing)} 天；最近日期：{shown}。')
    lines.append('')
    for source in sources:
        source_streak = consecutive([source['id']])
        lines.append(f"- {source['name']}：截至今日连续成功 {source_streak} 天。")
    if not sources:
        lines.append('- 尚无启用来源。')
    lines += ['', '- 连续运行验收：' +
              ('通过（仅运行连续性；采集质量仍需样本核对）。' if all_streak >= days else
               '进行中；历史缺失日期不计入连续天数。')]
    output = data_root / 'trial-status.md'
    output.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    return output
