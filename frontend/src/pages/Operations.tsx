import { useCallback, useEffect, useState } from 'react'
import { Alert, Button, Card, Collapse, Empty, List, Space, Table, Tag, Typography, message } from 'antd'
import { useNavigate } from 'react-router-dom'
import type { TableProps } from 'antd'
import { CloudDownloadOutlined, DatabaseOutlined, ExperimentOutlined, ReloadOutlined, SyncOutlined } from '@ant-design/icons'
import { api } from '../api'
import type { ReadingJob, StatusData, StatusJob } from '../types'
import { ErrorBlock, formatDate, formatFreshness, LoadingBlock } from '../components/Common'

const { Title, Text, Paragraph } = Typography

function jobColor(status: string): string {
  if (status === 'failed') return 'error'
  if (status === 'running') return 'processing'
  if (status === 'queued' || status === 'pending') return 'warning'
  if (status === 'succeeded' || status === 'done') return 'success'
  return 'default'
}

function jobLabel(status: string): string {
  const labels: Record<string, string> = { failed: '失败', running: '处理中', queued: '等待中', pending: '等待中', succeeded: '成功', done: '成功' }
  return labels[status] ?? status
}

function jobError(value: string): string {
  if (value.includes('evidence fragment was not read in this job') || value.includes('invalid evidence fragment_id')) return 'AI 选择的引用片段不属于本次已读取资料，结果未保存；请重试。'
  if (value.includes('MODEL_FORMAT_ERROR') || value.toLowerCase().includes('yaml') || value.toLowerCase().includes('dsml')) return '模型返回格式不正确，结果未保存；请重试。'
  if (value.includes('evidence quote does not match source record')) return 'AI 引用与项目原文不匹配，判断未保存；可重试。'
  if (value.includes('potential opportunity requires evidence') || value.includes('explicit demand requires evidence')) return 'AI 列出的机会缺少原文依据，结果未保存；可重试。'
  if (value.includes('not valid JSON') || value.includes('not JSON') || value.includes('Expecting ') || value.includes('Unterminated string')) return '模型返回的数据格式有误，判断未保存；可重试。'
  if (value.includes('positive AI judgment requires source evidence')) return 'AI 推荐缺少原文依据，判断未保存；请核对资料后重试。'
  return value
}

function importValue(item: Record<string, unknown>, keys: string[]): unknown {
  for (const key of keys) if (item[key] !== undefined && item[key] !== null && item[key] !== '') return item[key]
  return null
}

function importText(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—'
  if (typeof value === 'boolean') return value ? '是' : '否'
  return String(value)
}

function ImportHistory({ items }: { items: StatusData['imports'] }) {
  if (items.length === 0) return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无导入记录" />
  return <List size="small" dataSource={items.slice(0, 8)} renderItem={(item, index) => {
    const time = importValue(item, ['created_at', 'updated_at', 'imported_at', 'timestamp', 'time'])
    const rawState = importText(importValue(item, ['status', 'result']))
    const state = ({ success: '已导入', failed: '导入失败', running: '导入中' } as Record<string, string>)[rawState] ?? rawState
    const error = importValue(item, ['error', 'message', 'detail'])
    const hasError = error !== null && error !== undefined && error !== ''
    return <List.Item><div className="import-history-row"><Space wrap><Tag color={hasError ? 'error' : 'success'}>{state === '—' ? `第 ${index + 1} 次` : state}</Tag><Text type="secondary">{time ? formatDate(String(time), true) : '时间未记录'}</Text></Space><div className="import-counts">新增 {importText(importValue(item, ['inserted', 'added', 'new']))} · 更新 {importText(importValue(item, ['updated', 'changed']))} · 未变 {importText(importValue(item, ['unchanged', 'same']))}{hasError ? <span className="import-error-text"> · {importText(error)}</span> : null}</div></div></List.Item>
  }} />
}

export function OperationsPage() {
  const navigate = useNavigate()
  const [status, setStatus] = useState<StatusData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<unknown>(null)
  const [action, setAction] = useState('')

  const loadStatus = useCallback(async () => {
    setError(null)
    try {
      setStatus(await api.status())
    } catch (loadError) {
      setError(loadError)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    void loadStatus()
    const timer = window.setInterval(() => { void loadStatus() }, 10000)
    return () => window.clearInterval(timer)
  }, [loadStatus])

  const run = async (key: string, fn: () => Promise<{ message: string }>) => {
    setAction(key)
    try {
      const result = await fn()
      message.success(result.message)
      await loadStatus()
    } catch (runError) {
      message.error(runError instanceof Error ? runError.message : '操作失败，请稍后重试')
    } finally {
      setAction('')
    }
  }

  if (loading) return <div className="page-stack"><LoadingBlock text="正在读取处理状态" /></div>
  if (error || !status) return <div className="page-stack"><ErrorBlock error={error} onRetry={() => void loadStatus()} /></div>

  const jobs = status.jobs ?? []
  const readingJobs = status.reading_jobs ?? []
  const jobColumns: TableProps<StatusJob>['columns'] = [
    { title: '任务', dataIndex: 'id', key: 'id', width: 84, render: (value: number) => `#${value}` },
    { title: '项目', dataIndex: 'project_id', key: 'project_id', width: 90, render: (value: number | null) => value ? <Button type="link" onClick={() => navigate(`/projects/${value}`)}>项目 #{value}</Button> : '批量任务' },
    { title: '状态', dataIndex: 'status', key: 'status', width: 110, render: (value: string) => <Tag color={jobColor(value)}>{jobLabel(value)}</Tag> },
    { title: '尝试次数', dataIndex: 'attempts', key: 'attempts', width: 90 },
    { title: '失败说明', dataIndex: 'error', key: 'error', render: (value: string | null) => value ? <span title={value}>{jobError(value)}</span> : <Text type="secondary">—</Text> },
    { title: '创建时间', dataIndex: 'created_at', key: 'created_at', width: 150, render: (value: string) => formatDate(value, true) },
    { title: '更新时间', dataIndex: 'updated_at', key: 'updated_at', width: 150, render: (value: string) => formatDate(value, true) },
  ]
  const readingJobColumns: TableProps<ReadingJob>['columns'] = [
    { title: '阅读任务', dataIndex: 'id', key: 'id', width: 90, render: (value: number) => `#${value}` },
    { title: '采购项目', dataIndex: 'project_id', key: 'project_id', width: 120, render: (value: number) => <Button type="link" onClick={() => navigate(`/projects/${value}`)}>项目 #{value}</Button> },
    { title: '状态', dataIndex: 'status', key: 'status', width: 110, render: (value: string) => <Tag color={jobColor(value)}>{jobLabel(value)}</Tag> },
    { title: '尝试次数', dataIndex: 'attempts', key: 'attempts', width: 90 },
    { title: '失败说明', dataIndex: 'error', key: 'error', render: (value: string | null) => value ? <span title={value}>{value}</span> : <Text type="secondary">—</Text> },
    { title: '更新时间', dataIndex: 'updated_at', key: 'updated_at', width: 150, render: (value: string) => formatDate(value, true) },
    { title: '操作', key: 'actions', width: 110, render: (_: unknown, record: ReadingJob) => <Button size="small" icon={<ReloadOutlined />} loading={action === `reading-${record.project_id}`} onClick={() => void run(`reading-${record.project_id}`, () => api.refreshReading(record.project_id))}>重试该项目</Button> },
  ]

  return (
    <div className="page-stack operations-page">
      <div className="page-heading"><div><Title level={2}>处理状态</Title><Paragraph>确认资料是否同步，以及 AI 分析是否完成。</Paragraph></div><Space wrap><Text type="secondary">{formatFreshness(status.data_updated_at)}</Text><Button icon={<ReloadOutlined />} onClick={() => void loadStatus()}>刷新</Button></Space></div>
      <Alert type="info" showIcon icon={<DatabaseOutlined />} message="来源采集已独立管理" description="在信息采集中查看来源访问状态、启动采集并设置每日自动任务。" action={<Button size="small" type="link" onClick={() => navigate('/collection')}>打开信息采集</Button>} />
      {status.import_error && <Alert type="error" showIcon message="最近一次导入未成功" description={status.import_error} />}
      {status.file_stale && <Alert type="warning" showIcon message="采集文件超过 48 小时未更新，或文件不存在" description="请检查采集系统和导入路径。" />}
      {status.import_pending && <Alert type="info" showIcon message="新采集数据尚未导入" description="可点击“立即导入 records.jsonl”更新项目。" />}
      <div className="operation-stats">
        <OperationStat title="采集文件更新" value={status.data_updated_at ? formatDate(status.data_updated_at, true) : '暂无'} icon={<DatabaseOutlined />} />
        <OperationStat title="平台最近导入" value={status.last_import_at ? formatDate(status.last_import_at, true) : '暂无'} icon={<CloudDownloadOutlined />} />
        <OperationStat title="等待 / 处理中" value={String((status.job_counts.queued ?? 0) + (status.job_counts.running ?? 0))} icon={<SyncOutlined />} />
        <OperationStat title="当前失败项目" value={String(status.job_counts.failed ?? 0)} icon={<ExperimentOutlined />} />
      </div>
      <div className="operation-toolbar"><Space wrap><Button type="primary" icon={<CloudDownloadOutlined />} loading={action === 'import'} onClick={() => void run('import', api.importNow)}>立即导入 records.jsonl</Button><Button icon={<ExperimentOutlined />} loading={action === 'pending'} onClick={() => void run('pending', api.analyzePending)} disabled={!status.ai_configured}>分析待处理项目</Button><Button danger icon={<ReloadOutlined />} loading={action === 'retry'} onClick={() => void run('retry', api.retryFailed)} disabled={!status.ai_configured}>批量重试失败任务</Button></Space><Paragraph>导入异常会保留已有项目</Paragraph></div>
      {!status.ai_configured && <Alert type="info" showIcon message="模型尚未配置，可先查看和处理项目" action={<Button size="small" type="link" onClick={() => navigate('/settings')}>配置模型</Button>} />}
      <Card className="task-ledger" title={<span>分析任务历史 <Text type="secondary">（最近 {jobs.length} 条）</Text></span>} extra={<span className="operation-table-caption">每 10 秒自动更新</span>}>
        {jobs.length === 0 ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="还没有分析任务，配置模型后会开始处理候选项目" /> : <Table<StatusJob> rowKey="id" columns={jobColumns} dataSource={jobs} size="middle" scroll={{ x: 820 }} pagination={{ pageSize: 20, showTotal: value => `共 ${value} 条` }} />}
      </Card>
      <Card className="task-ledger reading-job-ledger" title={<span>采购阅读任务历史 <Text type="secondary">（最近 {readingJobs.length} 条）</Text></span>} extra={<span className="operation-table-caption">阅读失败可按项目重试</span>}>
        {readingJobs.length === 0 ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无采购阅读任务" /> : <Table<ReadingJob> rowKey="id" columns={readingJobColumns} dataSource={readingJobs} size="middle" scroll={{ x: 920 }} pagination={{ pageSize: 20, showTotal: value => `共 ${value} 条` }} />}
      </Card>
      <Collapse className="source-details" items={[
        { key: 'coverage', label: '查看来源覆盖与采集说明', children: status.source_notes ? <Paragraph className="source-note-text">{status.source_notes}</Paragraph> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂未读取来源说明" /> },
        { key: 'imports', label: `最近导入记录（${status.imports.length} 条）`, children: <ImportHistory items={status.imports} /> },
      ]} />
    </div>
  )
}

function OperationStat({ title, value, icon }: { title: string; value: string; icon: React.ReactNode }) {
  return <div className="operation-stat-card"><div className="operation-stat-label">{icon}<Text>{title}</Text></div><div className="operation-stat-value">{value}</div></div>
}
