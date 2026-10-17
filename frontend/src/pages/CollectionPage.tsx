import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { HTMLAttributes } from 'react'
import { Alert, Button, Card, Empty, Input, Space, Switch, Table, Tag, Typography, message } from 'antd'
import type { TableProps } from 'antd'
import {
  CheckCircleOutlined,
  ClockCircleOutlined,
  CloudDownloadOutlined,
  ExclamationCircleOutlined,
  LinkOutlined,
  ReloadOutlined,
  SettingOutlined,
  SyncOutlined,
} from '@ant-design/icons'
import { useNavigate } from 'react-router-dom'
import { api } from '../api'
import type { CollectionData, CollectionRun, CollectionSource, CollectionTask } from '../types'
import { ErrorBlock, formatDate, LoadingBlock } from '../components/Common'
import './CollectionPage.css'

const { Title, Paragraph, Text } = Typography

const statLabels: Record<string, string> = {
  found: '发现',
  found_count: '发现',
  fetched: '抓取',
  fetched_count: '抓取',
  imported: '导入',
  imported_count: '导入',
  inserted: '新增',
  updated: '更新',
  unchanged: '未变',
  new: '新增',
  changed: '更新',
  markup_changed: '标记变化',
  source_failed: '来源失败',
  item_failed: '条目失败',
  failed: '失败',
  error: '错误',
  total: '合计',
}

const accessLabels: Record<string, { label: string; color: string }> = {
  available: { label: '可访问', color: 'success' },
  ok: { label: '可访问', color: 'success' },
  ready: { label: '可访问', color: 'success' },
  connected: { label: '已连接', color: 'success' },
  public: { label: '公开来源', color: 'default' },
  public_access: { label: '公开来源', color: 'default' },
  blocked: { label: '访问受阻', color: 'error' },
  auth_required: { label: '需要登录', color: 'warning' },
  manual: { label: '待人工接入', color: 'warning' },
  unreachable: { label: '无法连接', color: 'error' },
  unverified: { label: '尚未验证', color: 'warning' },
  missing_input: { label: '待补充接入资料', color: 'warning' },
  unavailable: { label: '暂不可用', color: 'warning' },
  no_history: { label: '暂无历史', color: 'default' },
  network_error: { label: '网络失败', color: 'error' },
  failed: { label: '访问失败', color: 'error' },
  error: { label: '访问失败', color: 'error' },
  disabled: { label: '已停用', color: 'default' },
}

const taskLabels: Record<CollectionTask['status'], { label: string; color: string }> = {
  running: { label: '采集中', color: 'processing' },
  succeeded: { label: '采集成功', color: 'success' },
  partial: { label: '部分完成', color: 'warning' },
  failed: { label: '采集失败', color: 'error' },
}

function normalizeTime(value: string | null | undefined): string {
  if (!value) return '07:30'
  const match = value.match(/(\d{2}):(\d{2})/)
  return match ? `${match[1]}:${match[2]}` : '07:30'
}

function numberValue(value: unknown): string {
  if (typeof value === 'number' && Number.isFinite(value)) return value.toLocaleString('zh-CN')
  if (typeof value === 'string' && value.trim() !== '') return value
  return '—'
}

function statValue(stats: Record<string, unknown> | null | undefined, keys: string[]): unknown {
  if (!stats) return null
  for (const key of keys) {
    if (stats[key] !== undefined && stats[key] !== null) return stats[key]
  }
  return null
}

function sumStats(stats: Record<string, number> | null | undefined, keys: string[]): number | null {
  if (!stats) return null
  const values = keys.map(key => stats[key]).filter(value => typeof value === 'number' && Number.isFinite(value))
  return values.length > 0 ? values.reduce((total, value) => total + value, 0) : null
}

function importedCount(result: Record<string, unknown> | null | undefined): number {
  if (!result) return 0
  return ['inserted', 'updated', 'unchanged'].reduce((total, key) => {
    const value = result[key]
    return total + (typeof value === 'number' && Number.isFinite(value) && value > 0 ? value : 0)
  }, 0)
}

function hasImportedContent(result: Record<string, unknown> | null | undefined): boolean {
  return importedCount(result) > 0
}

function statusTag(status: string | null | undefined) {
  const value = (status ?? '').toLowerCase()
  if (value === 'running' || value === 'queued') return <Tag color="processing">进行中</Tag>
  if (value === 'succeeded' || value === 'success' || value === 'completed') return <Tag color="success">成功</Tag>
  if (value === 'partial' || value === 'warning') return <Tag color="warning">部分完成</Tag>
  if (value === 'failed' || value === 'error') return <Tag color="error">失败</Tag>
  if (value === 'interrupted') return <Tag color="warning">已中断</Tag>
  return <Tag>{status || '暂无记录'}</Tag>
}

function accessTag(status: string | null | undefined) {
  const key = (status ?? '').toLowerCase()
  const value = accessLabels[key] ?? { label: status || '未确认', color: 'default' }
  return <Tag color={value.color}>{value.label}</Tag>
}

function isAccessGap(status: string | null | undefined): boolean {
  const key = (status ?? '').toLowerCase()
  return !['available', 'ok', 'ready', 'connected', 'public', 'public_access'].includes(key)
}

function categoryLabel(category: string | null | undefined): string {
  const value = (category ?? '').trim()
  const key = value.toLowerCase()
  if (['procurement', 'announcement', 'tender', 'public_procurement', 'procurement_notice', '采购'].includes(key)) return '采购公告'
  if (['industry', 'industry_news', 'industry_information', 'news', '资讯', '行业'].includes(key)) return '行业资讯'
  return value || '未分类'
}

function sourceErrorText(source: CollectionSource): string | null {
  if (source.last_run?.error) return source.last_run.error
  const latestRunStatus = (source.last_run?.status ?? '').toLowerCase()
  const latestRunFailed = ['failed', 'error', 'partial', 'network_error'].includes(latestRunStatus)
  if (latestRunFailed && source.last_error) return source.last_error
  if (!source.last_run && source.last_error) return source.last_error
  if (!source.last_run && source.last_failure_at) return `最近一次访问失败（${formatDate(source.last_failure_at, true)}）`
  return null
}

function formatRunStats(stats: Record<string, number>): string {
  const entries = Object.entries(stats ?? {}).filter(([, value]) => value !== null && value !== undefined)
  if (entries.length === 0) return '暂无数量'
  return entries.map(([key, value]) => `${statLabels[key] ?? key} ${numberValue(value)}`).join(' · ')
}

function taskHeadline(task: CollectionTask): string {
  if (task.status === 'running') return '边采集边导入，已完成内容可立即查看'
  if (hasImportedContent(task.import_result) && task.status === 'failed') return '采集已中断，已导入内容保留'
  if (hasImportedContent(task.import_result) && task.status === 'partial') return '部分来源未完成，已导入内容保留'
  if (importFailureMessage(task)) return '采集已结束，导入未完成'
  if (task.status === 'succeeded') return '本次采集并导入完成'
  if (task.status === 'partial') return '本次采集完成，但有来源或导入步骤未完成'
  return '本次采集失败，平台保留已有可用数据'
}

function importFailureMessage(task: CollectionTask): string | null {
  const value = task.import_result?.error
  return typeof value === 'string' && value.trim() ? value : null
}

function ImportResult({ result }: { result: Record<string, unknown> | null }) {
  if (!result || Object.keys(result).length === 0) return <Text type="secondary">导入数量尚未返回</Text>
  const keys = ['found', 'found_count', 'fetched', 'fetched_count', 'inserted', 'updated', 'new', 'changed', 'markup_changed', 'unchanged', 'failed', 'source_failed', 'item_failed', 'total']
  const visibleKeys = keys.filter(key => typeof result[key] === 'number' && Number.isFinite(result[key]))
  return <div className="collection-import-result" aria-label="导入结果数量">
    {visibleKeys.length > 0 ? visibleKeys.map(key => <span key={key}><strong>{numberValue(result[key])}</strong>{statLabels[key] ?? key}</span>) : <Text type="secondary">未返回统计数量</Text>}
  </div>
}

function TaskResult({ task }: { task: CollectionTask }) {
  const navigate = useNavigate()
  const importFailure = importFailureMessage(task)
  const imported = hasImportedContent(task.import_result)
  const interrupted = imported && task.status === 'failed'
  const partiallyImported = imported && task.status === 'partial'
  const label = task.status === 'running'
    ? taskLabels.running
    : interrupted
      ? { label: '部分导入', color: 'warning' }
      : partiallyImported
        ? { label: '部分完成', color: 'warning' }
        : importFailure
          ? { label: '导入失败', color: 'error' }
          : taskLabels[task.status]
  const errorMessage = importFailure ?? task.error
  return <section className={`collection-task-result collection-task-${task.status}`} aria-live="polite" data-testid="collection-task">
    <div className="collection-task-result-heading">
      <div>
        <div className="collection-task-kicker"><span className="collection-task-dot" aria-hidden="true" />最近一次采集任务</div>
        <h2>{taskHeadline(task)}</h2>
        <p>{task.started_at ? `开始于 ${formatDate(task.started_at, true)}` : '开始时间未记录'}{task.finished_at ? `，结束于 ${formatDate(task.finished_at, true)}` : ''}</p>
      </div>
      <Tag color={label.color} icon={task.status === 'running' ? <SyncOutlined spin /> : importFailure || task.status === 'failed' ? <ExclamationCircleOutlined /> : <CheckCircleOutlined />}>{label.label}</Tag>
    </div>
    <div className="collection-task-result-body">
      <div>
        <span className="collection-result-label">导入结果</span>
        <ImportResult result={task.import_result} />
      </div>
      {errorMessage && <Alert className="collection-task-error" type={imported ? 'warning' : 'error'} showIcon message={imported ? '导入有提示，已导入内容保留' : importFailure ? '导入失败' : '任务说明'} description={errorMessage} />}
      {task.status === 'running' && <div className="collection-task-progress"><SyncOutlined spin /> 正在持续导入，已完成内容可立即查看，累计新增 {numberValue(task.import_result?.inserted)}，更新 {numberValue(task.import_result?.updated)}。</div>}
      <div className="collection-task-actions"><Button type="link" onClick={() => document.getElementById('collection-runs')?.scrollIntoView({ behavior: 'smooth', block: 'start' })}>查看运行记录</Button><Button type="link" onClick={() => navigate('/?view=all')}>去采购需求</Button>{task.status !== 'running' && <Button type="link" onClick={() => navigate('/operations')}>{importFailure ? '去处理状态重试导入' : '去处理状态'}</Button>}</div>
    </div>
  </section>
}

export function CollectionPage() {
  const navigate = useNavigate()
  const [data, setData] = useState<CollectionData | null>(null)
  const [task, setTask] = useState<CollectionTask | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<unknown>(null)
  const [action, setAction] = useState<string | null>(null)
  const [scheduleAt, setScheduleAt] = useState('07:30')
  const [scheduleEnabled, setScheduleEnabled] = useState(false)
  const scheduleDirtyRef = useRef(false)
  const scheduleRevisionRef = useRef(0)
  const collectionRequestRef = useRef(0)
  const collectionLoadRef = useRef<Promise<void> | null>(null)

  const loadCollection = useCallback((silent = false): Promise<void> => {
    if (collectionLoadRef.current) return collectionLoadRef.current
    const requestId = collectionRequestRef.current + 1
    collectionRequestRef.current = requestId
    if (!silent) setLoading(true)
    const request = (async () => {
      try {
        const next = await api.collection()
        if (requestId !== collectionRequestRef.current) return
        setData(next)
        setError(null)
        setTask(current => {
          if (next.task) return next.task
          if (!next.running) return null
          return current
        })
        if (!scheduleDirtyRef.current) {
          setScheduleAt(normalizeTime(next.schedule?.at))
          setScheduleEnabled(Boolean(next.schedule?.enabled))
        }
      } catch (loadError) {
        if (requestId !== collectionRequestRef.current) return
        setError(loadError)
      } finally {
        if (requestId === collectionRequestRef.current && !silent) setLoading(false)
      }
    })()
    collectionLoadRef.current = request
    request.then(
      () => { if (collectionLoadRef.current === request) collectionLoadRef.current = null },
      () => { if (collectionLoadRef.current === request) collectionLoadRef.current = null },
    )
    return request
  }, [])

  useEffect(() => {
    let disposed = false
    let timer: number | undefined
    const poll = async () => {
      if (disposed) return
      await loadCollection(true)
      if (!disposed) timer = window.setTimeout(() => { void poll() }, 5000)
    }
    void loadCollection().then(() => {
      if (!disposed) timer = window.setTimeout(() => { void poll() }, 5000)
    })
    return () => {
      disposed = true
      if (timer !== undefined) window.clearTimeout(timer)
    }
  }, [loadCollection])

  const activeTask = task ?? data?.task ?? null
  const running = Boolean(data?.running || activeTask?.status === 'running')
  const unavailable = data?.available === false
  const importDisconnected = data?.import_connected === false
  const controlsDisabled = Boolean(action || loading || running || unavailable || error)
  const accessGapCount = useMemo(() => data?.sources.filter(source => isAccessGap(source.access_status) || ['failed', 'partial', 'interrupted'].includes(source.last_run?.status ?? '')).length ?? 0, [data?.sources])
  const enabledCount = useMemo(() => data?.sources.filter(source => source.enabled).length ?? 0, [data?.sources])
  const latestRun = data?.runs?.[0]
  const processedCount = sumStats(latestRun?.stats, ['new', 'changed', 'markup_changed', 'unchanged'])
  const latestCollected = processedCount ?? statValue(latestRun?.stats, ['fetched_count', 'fetched', 'found_count', 'found'])

  const runCollection = async (sourceId: string | null = null) => {
    const key = sourceId ? `run-${sourceId}` : 'run-all'
    setAction(key)
    try {
      const result = await api.runCollection(sourceId)
      setTask(result.task)
      setData(current => current ? { ...current, running: true, task: result.task } : current)
      message.success(result.message || '采集任务已启动')
      await loadCollection(true)
    } catch (runError) {
      message.error(runError instanceof Error ? runError.message : '采集启动失败，请稍后重试')
    } finally {
      setAction(null)
    }
  }

  const toggleSource = async (source: CollectionSource, enabled: boolean) => {
    const key = `source-${source.id}`
    setAction(key)
    try {
      const result = await api.setCollectionSourceEnabled(source.id, enabled)
      setData(current => current ? { ...current, sources: current.sources.map(item => item.id === source.id ? { ...item, enabled } : item) } : current)
      message.success(result.message || (enabled ? '来源已启用' : '来源已停用'))
      await loadCollection(true)
    } catch (toggleError) {
      message.error(toggleError instanceof Error ? toggleError.message : '来源设置保存失败，请稍后重试')
    } finally {
      setAction(null)
    }
  }

  const saveSchedule = async () => {
    if (!scheduleAt) {
      message.warning('请选择每日采集时间')
      return
    }
    const revision = scheduleRevisionRef.current
    setAction('schedule')
    try {
      const result = await api.saveCollectionSchedule({ enabled: scheduleEnabled, at: scheduleAt })
      if (scheduleRevisionRef.current === revision) scheduleDirtyRef.current = false
      message.success(result.message || '每日采集设置已保存')
      await loadCollection(true)
    } catch (saveError) {
      message.error(saveError instanceof Error ? saveError.message : '每日采集设置保存失败，请稍后重试')
    } finally {
      setAction(null)
    }
  }

  const onScheduleAtChange = (value: string) => {
    scheduleDirtyRef.current = true
    scheduleRevisionRef.current += 1
    setScheduleAt(value)
  }

  const onScheduleEnabledChange = (value: boolean) => {
    scheduleDirtyRef.current = true
    scheduleRevisionRef.current += 1
    setScheduleEnabled(value)
  }

  const sourceColumns: TableProps<CollectionSource>['columns'] = [
    {
      title: '来源', key: 'source', width: 240, render: (_value, source) => <div className="collection-source-name">
        <strong>{source.name || source.id}</strong>
        <span>{categoryLabel(source.category)}</span>
        {source.entry_url ? <a href={source.entry_url} target="_blank" rel="noreferrer">打开来源 <LinkOutlined /></a> : null}
      </div>,
    },
    { title: '启用', dataIndex: 'enabled', key: 'enabled', width: 76, render: (enabled: boolean, source) => <Switch size="small" checked={enabled} disabled={controlsDisabled || action === `source-${source.id}`} loading={action === `source-${source.id}`} data-testid={`collection-source-toggle-${source.id}`} onChange={value => void toggleSource(source, value)} aria-label={`${source.name || source.id}来源开关`} /> },
    { title: '访问状态', dataIndex: 'access_status', key: 'access_status', width: 120, render: (value: string, source) => <div className="collection-access-cell">{accessTag(value)}<span>{source.access_note || (isAccessGap(value) ? '暂未取得可用历史' : '公开可访问类别')}</span></div> },
    { title: '最近采集', key: 'last_run', width: 245, render: (_value, source) => source.last_run ? <div className="collection-source-run">{statusTag(source.last_run.status)}<span>{source.last_run.finished_at ? formatDate(source.last_run.finished_at, true) : '时间未记录'}</span><span>发现 {numberValue(source.last_run.found_count)} · 抓取 {numberValue(source.last_run.fetched_count)}</span></div> : <Text type="secondary">暂无历史记录</Text> },
    { title: '最近错误', key: 'error', render: (_value, source) => { const errorText = sourceErrorText(source); return errorText ? <span className="collection-source-error" title={errorText}>{errorText}</span> : <Text type="secondary">—</Text> } },
    { title: '操作', key: 'actions', width: 116, render: (_value, source) => <Button size="small" icon={<ReloadOutlined />} loading={action === `run-${source.id}`} disabled={controlsDisabled || importDisconnected || !source.enabled} data-testid={`collection-source-run-${source.id}`} onClick={() => void runCollection(source.id)}>采集/重试</Button> },
  ]

  const runColumns: TableProps<CollectionRun>['columns'] = [
    { title: '状态', dataIndex: 'status', key: 'status', width: 110, render: status => statusTag(status) },
    { title: '开始时间', dataIndex: 'started_at', key: 'started_at', width: 165, render: value => value ? formatDate(value, true) : '—' },
    { title: '结束时间', dataIndex: 'finished_at', key: 'finished_at', width: 165, render: value => value ? formatDate(value, true) : '—' },
    { title: '采集数量', key: 'stats', render: (_value, run) => formatRunStats(run.stats) },
  ]

  if (loading && !data) return <div className="page-stack collection-page"><LoadingBlock text="正在读取采集来源" /></div>
  if (error && !data) return <div className="page-stack collection-page"><ErrorBlock error={error} onRetry={() => void loadCollection()} /></div>

  return <main className="page-stack collection-page" data-testid="collection-page">
    <header className="collection-page-heading">
      <div>
        <div className="collection-eyebrow"><span className="collection-eyebrow-mark" aria-hidden="true" />统一采集管理</div>
        <Title level={1}>信息采集</Title>
        <Paragraph>集中管理来源启停、每日采集与导入结果。来源覆盖代表已配置范围，不代表全网覆盖；模型阅读与研判将在采集完成后继续。</Paragraph>
      </div>
      <Space wrap className="collection-page-actions">
        <Button onClick={() => navigate('/?view=all')}>去采购需求</Button>
        <Button onClick={() => navigate('/operations')}>处理状态</Button>
        <Button icon={<ReloadOutlined />} onClick={() => void loadCollection()} loading={loading}>刷新</Button>
      </Space>
    </header>

    {unavailable && <Alert className="collection-availability" type="warning" showIcon message="采集服务暂时不可用" description={data?.message || '当前无法读取来源状态，请稍后重试。'} />}
    {!unavailable && !importDisconnected && data?.message && <Alert className="collection-availability" type="info" showIcon message={data.message} />}
    {!unavailable && importDisconnected && <Alert className="collection-availability" type="warning" showIcon data-testid="collection-import-warning" message="采集结果尚未接入平台" description="当前导入路径与采集输出不一致，采集尚未启动。请在系统设置的数据接入中连接正式采集结果后再试。" action={<Button onClick={() => navigate('/settings?section=import')}>检查数据接入</Button>} />}
    {error != null && data ? <Alert className="collection-availability" type="error" showIcon message="采集状态刷新失败" description={error instanceof Error ? error.message : '最新状态暂时无法读取，请刷新后再执行操作。'} action={<Button size="small" onClick={() => void loadCollection()}>重新加载</Button>} /> : null}

    <section className="collection-overview" aria-label="采集概览">
      <div className="collection-overview-item"><span className="collection-overview-icon"><CloudDownloadOutlined /></span><div><span>已启用来源</span><strong>{enabledCount} <small>/ {data?.sources.length ?? 0}</small></strong></div></div>
      <div className="collection-overview-item"><span className="collection-overview-icon collection-overview-warning"><ExclamationCircleOutlined /></span><div><span>访问缺口</span><strong>{accessGapCount}</strong></div></div>
      <div className="collection-overview-item"><span className="collection-overview-icon"><CheckCircleOutlined /></span><div><span>最近采集条数</span><strong>{numberValue(latestCollected)}</strong></div></div>
      <div className="collection-overview-item"><span className="collection-overview-icon"><ClockCircleOutlined /></span><div><span>最近运行</span><strong>{latestRun?.finished_at ? formatDate(latestRun.finished_at, true) : '暂无记录'}</strong></div></div>
    </section>

    {activeTask && <TaskResult task={activeTask} />}

    <section className="collection-control-grid">
      <Card className="collection-run-card" title={<span><CloudDownloadOutlined /> 运行采集</span>} extra={running ? <Tag color="processing" icon={<SyncOutlined spin />}>运行中</Tag> : <Text type="secondary">全量或单来源</Text>}>
        <p>启动后边采集边导入，已完成内容可立即查看。任务运行期间会锁定冲突操作。</p>
        <Button className="collection-run-primary" type="primary" size="large" icon={<CloudDownloadOutlined />} disabled={controlsDisabled || importDisconnected || enabledCount === 0} loading={action === 'run-all'} data-testid="collection-run-all" onClick={() => void runCollection()}>立即采集全部来源</Button>
        {enabledCount === 0 && <div className="collection-running-note">请先启用至少一个来源，再开始全量采集。</div>}
        {running && <div className="collection-running-note"><SyncOutlined spin /> 采集任务进行中，边采集边导入，页面会自动更新状态。</div>}
      </Card>
      <Card className="collection-schedule-card" title={<span><SettingOutlined /> 每日自动采集</span>} extra={data?.schedule?.registered ? <Tag color="success">已注册</Tag> : <Tag>未注册</Tag>}>
        <div className="collection-schedule-row"><div><strong>启用每日任务</strong><p>{data?.schedule?.message || '由平台后端控制 Windows 任务。'}</p></div><Switch checked={scheduleEnabled} disabled={controlsDisabled || data?.schedule?.available === false} onChange={onScheduleEnabledChange} aria-label="启用每日自动采集" /></div>
        <label className="collection-time-field" htmlFor="collection-schedule-time"><span>每日开始时间</span><Input id="collection-schedule-time" type="time" value={scheduleAt} onChange={event => onScheduleAtChange(event.target.value)} disabled={controlsDisabled || data?.schedule?.available === false} aria-describedby="collection-schedule-help" /></label>
        <div id="collection-schedule-help" className="collection-schedule-help">按本机时间执行，关闭电脑期间无法采集。</div>
        <Button onClick={() => void saveSchedule()} loading={action === 'schedule'} disabled={controlsDisabled || data?.schedule?.available === false}>保存每日设置</Button>
      </Card>
    </section>

    <Card className="collection-source-card" title={<span>来源与访问状态 <Text type="secondary">（{data?.sources.length ?? 0} 个来源）</Text></span>} extra={<Text type="secondary">覆盖范围按来源配置计算</Text>}>
      {data?.sources.length ? <Table<CollectionSource> rowKey="id" columns={sourceColumns} dataSource={data.sources} onRow={source => ({ 'data-testid': `collection-source-${source.id}` } as HTMLAttributes<HTMLTableRowElement>)} size="middle" scroll={{ x: 1050 }} pagination={false} /> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂未配置采集来源" />}
    </Card>

    <Card id="collection-runs" className="collection-history-card" title={<span>最近运行记录 <Text type="secondary">（{data?.runs.length ?? 0} 条）</Text></span>} extra={<Text type="secondary">失败会保留错误说明</Text>}>
      {data?.runs.length ? <Table<CollectionRun> rowKey="id" columns={runColumns} dataSource={data.runs.slice(0, 12)} size="middle" scroll={{ x: 680 }} pagination={false} /> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无采集历史，完成首次采集后会显示在这里" />}
    </Card>

    <footer className="collection-page-footer"><span>信息采集完成后，可在采购需求中查看导入结果。</span><span>采集来源与模型阅读、研判为不同阶段。</span></footer>
  </main>
}
