import { useCallback, useEffect, useState } from 'react'
import { Alert, Button, Card, Col, Empty, List, Row, Space, Table, Tag, Typography, message } from 'antd'
import type { TableProps } from 'antd'
import { CloudDownloadOutlined, DatabaseOutlined, ExperimentOutlined, ReloadOutlined, SyncOutlined } from '@ant-design/icons'
import { api } from '../api'
import type { StatusData, StatusJob } from '../types'
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
    const state = importText(importValue(item, ['status', 'result']))
    const error = importValue(item, ['error', 'message', 'detail'])
    const hasError = error !== null && error !== undefined && error !== ''
    return <List.Item><div className="import-history-row"><Space wrap><Tag color={hasError ? 'error' : 'success'}>{state === '—' ? `第 ${index + 1} 次` : state}</Tag><Text type="secondary">{time ? formatDate(String(time), true) : '时间未记录'}</Text></Space><div className="import-counts">新增 {importText(importValue(item, ['inserted', 'added', 'new']))} · 更新 {importText(importValue(item, ['updated', 'changed']))} · 未变 {importText(importValue(item, ['unchanged', 'same']))}{hasError ? <span className="import-error-text"> · {importText(error)}</span> : null}</div></div></List.Item>
  }} />
}

export function OperationsPage() {
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
  const jobColumns: TableProps<StatusJob>['columns'] = [
    { title: '任务', dataIndex: 'id', key: 'id', width: 84, render: (value: number) => `#${value}` },
    { title: '项目', dataIndex: 'project_id', key: 'project_id', width: 90, render: (value: number | null) => value ? `项目 #${value}` : '批量任务' },
    { title: '状态', dataIndex: 'status', key: 'status', width: 110, render: (value: string) => <Tag color={jobColor(value)}>{jobLabel(value)}</Tag> },
    { title: '尝试次数', dataIndex: 'attempts', key: 'attempts', width: 90 },
    { title: '失败说明', dataIndex: 'error', key: 'error', render: (value: string | null) => value || <Text type="secondary">—</Text> },
    { title: '创建时间', dataIndex: 'created_at', key: 'created_at', width: 150, render: (value: string) => formatDate(value, true) },
    { title: '更新时间', dataIndex: 'updated_at', key: 'updated_at', width: 150, render: (value: string) => formatDate(value, true) },
  ]

  return (
    <div className="page-stack operations-page">
      <div className="page-heading"><div><Title level={2}>处理状态</Title><Paragraph type="secondary">查看采集文件、规则处理和 AI 任务状态；异常时可以从这里手动恢复。</Paragraph></div><Space><Text type="secondary">{formatFreshness(status.data_updated_at)}</Text><Button icon={<ReloadOutlined />} onClick={() => void loadStatus()}>刷新</Button></Space></div>
      {status.import_error && <Alert type="error" showIcon message="最近一次导入存在异常" description={status.import_error} />}
      {status.file_stale && <Alert type="warning" showIcon message="采集文件可能已过期" description="后端没有发现近期更新，请检查采集系统导出文件和导入路径。" />}
      {!status.ai_configured && <Alert type="info" showIcon message="模型未配置" description="AI 任务不会生成结果。可先处理采集和规则，配置模型后再批量分析。" />}
      <Row gutter={[12, 12]} className="operation-stats"><Col xs={12} sm={6}><OperationStat title="项目数据更新时间" value={status.data_updated_at ? formatDate(status.data_updated_at, true) : '暂无'} icon={<DatabaseOutlined />} /></Col><Col xs={12} sm={6}><OperationStat title="最近导入" value={status.last_import_at ? formatDate(status.last_import_at, true) : '暂无'} icon={<CloudDownloadOutlined />} /></Col><Col xs={12} sm={6}><OperationStat title="等待 / 处理中" value={String((status.job_counts.queued ?? 0) + (status.job_counts.running ?? 0))} icon={<SyncOutlined />} /></Col><Col xs={12} sm={6}><OperationStat title="失败任务" value={String(status.job_counts.failed ?? 0)} icon={<ExperimentOutlined />} /></Col></Row>
      <Card title="手动处理" className="operation-actions-card"><Space wrap><Button icon={<CloudDownloadOutlined />} loading={action === 'import'} onClick={() => void run('import', api.importNow)}>立即导入 records.jsonl</Button><Button icon={<ExperimentOutlined />} loading={action === 'pending'} onClick={() => void run('pending', api.analyzePending)} disabled={!status.ai_configured}>分析待处理项目</Button><Button danger icon={<ReloadOutlined />} loading={action === 'retry'} onClick={() => void run('retry', api.retryFailed)} disabled={!status.ai_configured}>批量重试失败任务</Button></Space><Paragraph type="secondary">导入会先完成文件校验，失败时保留已有项目。重试只处理失败任务；每个任务的错误说明会保留。</Paragraph></Card>
      <Card title="来源与采集说明" className="source-card">{status.source_notes ? <Paragraph>{status.source_notes}</Paragraph> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无来源说明" />}<div className="import-history"><Text strong>导入记录</Text><ImportHistory items={status.imports} /></div></Card>
      <Card title={<span>任务列表 <Text type="secondary">（最近 {jobs.length} 条）</Text></span>} extra={<Text type="secondary">每 10 秒自动刷新</Text>}>{jobs.length === 0 ? <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无后台任务" /> : <Table<StatusJob> rowKey="id" columns={jobColumns} dataSource={jobs} size="middle" scroll={{ x: 850 }} pagination={{ pageSize: 20, showTotal: (value) => `共 ${value} 条` }} />}</Card>
    </div>
  )
}

function OperationStat({ title, value, icon }: { title: string; value: string; icon: React.ReactNode }) {
  return <Card className="operation-stat-card"><div className="operation-stat-label">{icon}<Text>{title}</Text></div><div className="operation-stat-value">{value}</div></Card>
}
