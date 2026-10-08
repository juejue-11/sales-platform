import { List, Space, Tag, Typography } from 'antd'
import type { ProjectVersion } from '../types'
import { formatDate } from './Common'

const { Paragraph, Text } = Typography

const fieldLabels: Record<string, string> = {
  id: '编号', record_id: '来源记录', title: '标题', buyer: '采购方', organization: '单位', company: '公司', equipment: '设备', kind: '公告类别', source_id: '来源', source: '来源',
  published_at: '发布时间', deadline_at: '截止时间', updated_at: '更新时间', created_at: '记录时间', changed_at: '变更时间', version: '版本', version_id: '版本编号',
  change_type: '变更类型', type: '类型', status: '状态', owner: '负责人', next_action: '下一步', note: '备注', selected_option_id: '选择的方案', reason: '原因', summary: '摘要',
  confidence: '置信说明', quote: '原文依据', text: '内容', parse_status: '解析状态', relation: '关联关系', stale: '是否待复核',
}

const valueLabels: Record<string, string> = {
  new: '未处理', following: '跟进中', verify: '待核实', closed: '已关闭', candidate: '候选', review: '待核实', excluded: '已排除', expired: '已截止', duplicate: '确认重复', signal: '线索',
  content: '正文变化', template: '页面模板变化', correction: '内容更正', deadline: '截止时间变化', award: '成交 / 中标', created: '首次接入', same_project: '同项目', dismiss: '排除提示',
}

function labelFor(key: string): string {
  if (fieldLabels[key]) return fieldLabels[key]
  return key.replace(/_/g, ' ')
}

function readable(value: unknown, depth = 0): string {
  if (value === null || value === undefined || value === '') return '未提供'
  if (typeof value === 'boolean') return value ? '是' : '否'
  if (typeof value === 'number') return String(value)
  if (typeof value === 'string') {
    const translated = valueLabels[value] ?? value
    return translated.length > 600 ? `${translated.slice(0, 600)}…` : translated
  }
  if (Array.isArray(value)) return value.length === 0 ? '未提供' : value.map((item) => readable(item, depth + 1)).join('、')
  if (depth >= 2) return '包含结构化内容'
  return Object.entries(value as Record<string, unknown>).map(([key, item]) => `${labelFor(key)}：${readable(item, depth + 1)}`).join('；') || '未提供'
}

function entries(value: unknown): Array<[string, unknown]> {
  if (value === null || value === undefined || value === '') return []
  if (Array.isArray(value)) return value.map((item, index) => [String(index + 1), item])
  if (typeof value === 'object') return Object.entries(value as Record<string, unknown>)
  return [['内容', value]]
}

export function StructuredFields({ value, emptyText = '暂无已抽取内容', tone = 'default' }: { value: unknown; emptyText?: string; tone?: 'default' | 'warning' }) {
  const rows = entries(value)
  if (rows.length === 0) return <Text type="secondary">{emptyText}</Text>
  return <div className={`structured-fields${tone === 'warning' ? ' structured-fields-warning' : ''}`}>{rows.map(([key, item]) => <div className="structured-field" key={key}><Text type="secondary">{labelFor(key)}</Text><span>{readable(item)}</span></div>)}</div>
}

function getValue(value: Record<string, unknown>, keys: string[]): unknown {
  for (const key of keys) if (value[key] !== undefined && value[key] !== null && value[key] !== '') return value[key]
  return null
}

function formatMaybeDate(value: unknown, key: string): string {
  if (typeof value !== 'string') return readable(value)
  if (key.endsWith('_at') || key === 'date' || key === 'time') return formatDate(value, true)
  return readable(value)
}

export function VersionHistory({ versions }: { versions: ProjectVersion[] }) {
  if (versions.length === 0) return <Text type="secondary">暂无版本变更</Text>
  return <List size="small" className="version-history" dataSource={versions} renderItem={(version, index) => {
    const timeKey = ['changed_at', 'updated_at', 'created_at', 'published_at'].find((key) => version[key]) ?? ''
    const changeType = getValue(version, ['change_type', 'type', 'kind']) ?? '版本更新'
    const summary = getValue(version, ['summary', 'reason', 'description', 'note'])
    return <List.Item><div className="history-item"><Space wrap><Tag color="blue">版本 {Number(version.version ?? index + 1)}</Tag><Text strong>{readable(changeType)}</Text>{timeKey && <Text type="secondary">{formatMaybeDate(version[timeKey], timeKey)}</Text>}</Space>{summary ? <Paragraph>{readable(summary)}</Paragraph> : <StructuredFields value={version} emptyText="暂无变更说明" />}</div></List.Item>
  }} />
}

export function DecisionHistoryList({ history }: { history: Array<Record<string, unknown>> }) {
  if (history.length === 0) return <Text type="secondary">暂无人工决定</Text>
  return <List size="small" className="decision-history-list" dataSource={history} renderItem={(item, index) => {
    const status = getValue(item, ['status', 'decision_status'])
    const timeKey = ['updated_at', 'created_at', 'changed_at'].find((key) => item[key]) ?? ''
    const selected = getValue(item, ['selected_option_title', 'selected_option_id'])
    const statusText = status === null || status === undefined || status === '' ? '' : readable(status)
    const note = getValue(item, ['note', 'reason'])
    return <List.Item><div className="history-item"><Space wrap><Tag>第 {index + 1} 次</Tag>{statusText && <Tag color={status === 'following' ? 'blue' : status === 'verify' ? 'orange' : undefined}>{statusText}</Tag>}{timeKey && <Text type="secondary">{formatMaybeDate(item[timeKey], timeKey)}</Text>}</Space><div className="history-facts"><span><Text type="secondary">负责人：</Text>{readable(getValue(item, ['owner']))}</span><span><Text type="secondary">下一步：</Text>{readable(getValue(item, ['next_action']))}</span><span><Text type="secondary">方案：</Text>{readable(selected)}</span></div>{note !== null && note !== undefined && note !== '' && <Paragraph>{readable(note)}</Paragraph>}</div></List.Item>
  }} />
}
