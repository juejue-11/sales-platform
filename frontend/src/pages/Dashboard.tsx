import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Alert, Button, Card, Col, Input, Row, Select, Space, Table, Typography } from 'antd'
import type { TablePaginationConfig, TableProps } from 'antd'
import { ClockCircleOutlined, ReloadOutlined, SearchOutlined } from '@ant-design/icons'
import { api } from '../api'
import type { DashboardData, ProjectSummary } from '../types'
import { AiDecisionTag, AiStatusTag, CountCard, DecisionTag, EmptyBlock, ErrorBlock, formatDate, formatFreshness, GateTag, LoadingBlock, ProjectTitle } from '../components/Common'

const { Text, Title, Paragraph } = Typography

interface ProjectQuery {
  q: string
  gate: string
  ai_status: string
  decision: string
  kind: string
  source_id: string
  ai_decision: string
  page: number
  page_size: number
  sort: 'updated' | 'deadline'
  deadline_soon: boolean
}

const initialQuery: ProjectQuery = { q: '', gate: '', ai_status: '', decision: '', kind: '', source_id: '', ai_decision: '', page: 1, page_size: 30, sort: 'updated', deadline_soon: false }

function count(data: DashboardData | null, field: 'gate_counts' | 'ai_counts' | 'ai_decision_counts' | 'decision_counts', key: string): number {
  return data?.[field]?.[key] ?? 0
}

export function DashboardPage() {
  const navigate = useNavigate()
  const [dashboard, setDashboard] = useState<DashboardData | null>(null)
  const [projects, setProjects] = useState<ProjectSummary[]>([])
  const [total, setTotal] = useState(0)
  const [query, setQuery] = useState<ProjectQuery>(initialQuery)
  const [draftQ, setDraftQ] = useState('')
  const [loadingDashboard, setLoadingDashboard] = useState(true)
  const [loadingProjects, setLoadingProjects] = useState(true)
  const [dashboardError, setDashboardError] = useState<unknown>(null)
  const [projectsError, setProjectsError] = useState<unknown>(null)

  const loadDashboard = useCallback(async () => {
    setDashboardError(null)
    try {
      const result = await api.dashboard()
      setDashboard(result)
    } catch (error) {
      setDashboardError(error)
    } finally {
      setLoadingDashboard(false)
    }
  }, [])

  const loadProjects = useCallback(async (currentQuery: ProjectQuery) => {
    setLoadingProjects(true)
    setProjectsError(null)
    try {
      const result = await api.projects({ ...currentQuery })
      setProjects(result.items)
      setTotal(result.total)
    } catch (error) {
      setProjectsError(error)
    } finally {
      setLoadingProjects(false)
    }
  }, [])

  useEffect(() => {
    void loadDashboard()
    void loadProjects(initialQuery)
    const timer = window.setInterval(() => { void loadDashboard() }, 30000)
    return () => window.clearInterval(timer)
  }, [loadDashboard, loadProjects])

  const submitQuery = (next: Partial<ProjectQuery>) => {
    const nextQuery = { ...query, ...next, page: 1, deadline_soon: next.deadline_soon ?? false }
    setQuery(nextQuery)
    void loadProjects(nextQuery)
  }

  const onPageChange = (pagination: TablePaginationConfig) => {
    const nextQuery = { ...query, page: pagination.current ?? 1, page_size: pagination.pageSize ?? 30 }
    setQuery(nextQuery)
    void loadProjects(nextQuery)
  }

  const deadlineProjects = dashboard?.deadline_soon ?? []
  const deadlineCount = dashboard?.deadline_soon_count ?? deadlineProjects.length
  const deadlinePreviewCount = Math.min(4, deadlineProjects.length)

  const startFromInitial = (overrides: Partial<ProjectQuery> = {}) => {
    const nextQuery = { ...initialQuery, ...overrides }
    setQuery(nextQuery)
    setDraftQ(nextQuery.q)
    void loadProjects(nextQuery)
  }

  const columns: TableProps<ProjectSummary>['columns'] = useMemo(() => [
    {
      title: '项目 / 采购方',
      key: 'title',
      width: 340,
      render: (_value, record) => <ProjectTitle project={record} onOpen={(id) => navigate(`/projects/${id}`)} />,
    },
    {
      title: '规则状态',
      dataIndex: 'gate_status',
      key: 'gate_status',
      width: 110,
      render: (value: ProjectSummary['gate_status']) => <GateTag status={value} />,
    },
    {
      title: 'AI 建议',
      key: 'ai',
      width: 190,
      render: (_value, record) => <div className="status-stack"><div><AiStatusTag status={record.ai_status} /></div><AiDecisionTag decision={record.ai_decision} /></div>,
    },
    {
      title: '人工状态',
      key: 'decision',
      width: 110,
      render: (_value, record) => <DecisionTag status={record.decision.status} />,
    },
    {
      title: '截止时间',
      dataIndex: 'deadline_at',
      key: 'deadline_at',
      width: 130,
      render: (value: string | null) => <Text type={value && new Date(value).getTime() < Date.now() ? 'danger' : undefined}>{formatDate(value)}</Text>,
    },
    {
      title: '更新时间',
      dataIndex: 'updated_at',
      key: 'updated_at',
      width: 130,
      render: (value: string) => <Text type="secondary">{formatDate(value)}</Text>,
    },
    {
      title: '',
      key: 'action',
      width: 76,
      fixed: 'right',
      render: (_value, record) => <Button type="link" onClick={() => navigate(`/projects/${record.id}`)}>查看</Button>,
    },
  ], [navigate])

  return (
    <div className="page-stack">
      <div className="page-heading">
        <div>
          <Title level={2}>机会池</Title>
          <Paragraph type="secondary">把采集到的公告按规则、AI 判断和人工决定放在一个工作面里。</Paragraph>
        </div>
        <Space wrap>
          <Text type="secondary">{formatFreshness(dashboard?.data_updated_at)}</Text>
          <Button icon={<ReloadOutlined />} onClick={() => { void loadDashboard(); void loadProjects(query) }}>刷新</Button>
        </Space>
      </div>

      {dashboardError ? <Alert type="warning" showIcon message="统计暂时无法读取" description={dashboardError instanceof Error ? dashboardError.message : '请检查后端服务是否运行在 127.0.0.1:8766'} action={<Button size="small" onClick={() => void loadDashboard()}>重试</Button>} /> : null}
      {dashboard?.import_error && <Alert type="warning" showIcon message="采集文件存在异常" description={dashboard.import_error} />}
      {dashboard?.file_stale && <Alert type="warning" showIcon message="采集文件可能已过期" description="平台暂未看到近期的 records.jsonl 更新，请检查采集系统和导入路径。" />}
      {dashboard?.import_pending && <Alert type="info" showIcon message="新采集数据尚未导入" description="自动导入可能未开启或仍待处理，请前往处理状态手动导入。" />}
      {dashboard?.source_notes && <Alert type="info" showIcon message="来源覆盖说明" description={dashboard.source_notes} />}
      {dashboard && !dashboard.ai_configured && <Alert type="info" showIcon message="模型尚未配置" description="当前项目只展示规则结果和已有数据，不会生成虚构的 AI 建议。请在业务配置中填写模型信息后再分析。" action={<Button type="link" onClick={() => navigate('/settings')}>去配置</Button>} />}

      <Row gutter={[12, 12]} className="count-grid">
        <Col xs={12} sm={8} lg={4}><CountCard label="全部项目" value={dashboard?.total_projects ?? 0} onClick={() => startFromInitial()} /></Col>
        <Col xs={12} sm={8} lg={4}><CountCard label="建议跟进" value={count(dashboard, 'ai_decision_counts', 'recommend')} tone="blue" onClick={() => startFromInitial({ ai_decision: 'recommend' })} /></Col>
        <Col xs={12} sm={8} lg={4}><CountCard label="初筛候选" value={count(dashboard, 'gate_counts', 'candidate')} onClick={() => startFromInitial({ gate: 'candidate' })} /></Col>
        <Col xs={12} sm={8} lg={4}><CountCard label="待核实" value={count(dashboard, 'gate_counts', 'review')} tone="orange" onClick={() => startFromInitial({ gate: 'review' })} /></Col>
        <Col xs={12} sm={8} lg={4}><CountCard label="未来 7 天截止" value={deadlineCount} tone="orange" onClick={() => setQueryAndLoadDeadline()} /></Col>
        <Col xs={12} sm={8} lg={4}><CountCard label="等待分析" value={count(dashboard, 'ai_counts', 'queued')} /></Col>
        <Col xs={12} sm={8} lg={4}><CountCard label="跟进中" value={count(dashboard, 'decision_counts', 'following')} tone="green" onClick={() => submitQuery({ decision: 'following' })} /></Col>
      </Row>

      <Card className="deadline-card" title={<span><ClockCircleOutlined /> 未来 7 天临近截止项目</span>} extra={<Button type="link" onClick={() => setQueryAndLoadDeadline()}>查看全部</Button>}>
        {loadingDashboard ? <LoadingBlock text="正在读取提醒" /> : deadlineCount === 0 ? <EmptyBlock description="未来 7 天没有临近截止项目" /> : <>{deadlineProjects.length > 0 && <div className="deadline-list">{deadlineProjects.slice(0, 4).map((project) => <button type="button" key={project.id} className="deadline-row" onClick={() => navigate(`/projects/${project.id}`)}><span className="deadline-name">{project.title}</span><Text type="danger">{formatDate(project.deadline_at)}</Text><AiDecisionTag decision={project.ai_decision} /></button>)}</div>}{deadlineCount > deadlinePreviewCount && <Text type="secondary">还有 {deadlineCount - deadlinePreviewCount} 条提醒，请点击“查看全部”。</Text>}</>}
      </Card>

      <Card className="opportunity-card" title={<div><Title level={4}>项目列表</Title><Text type="secondary">共 {total.toLocaleString('zh-CN')} 个项目，已排除项目默认隐藏，可通过规则状态筛选查看。</Text></div>}>
        <div className="filter-bar">
          <Input.Search value={draftQ} onChange={(event) => setDraftQ(event.target.value)} onSearch={(value) => submitQuery({ q: value })} allowClear placeholder="搜索项目标题、采购方、设备" enterButton={<SearchOutlined />} className="filter-search" />
          <Select value={query.gate} onChange={(value) => submitQuery({ gate: value })} options={[{ value: '', label: '全部规则状态' }, { value: 'candidate', label: '候选' }, { value: 'review', label: '待核实' }, { value: 'excluded', label: '已排除' }, { value: 'expired', label: '已截止' }, { value: 'signal', label: '线索' }, { value: 'duplicate', label: '疑似重复' }]} />
          <Select value={query.ai_status} onChange={(value) => submitQuery({ ai_status: value })} options={[{ value: '', label: '全部 AI 状态' }, { value: 'not_configured', label: '未配置模型' }, { value: 'idle', label: '待分析' }, { value: 'queued', label: '等待分析' }, { value: 'running', label: '分析中' }, { value: 'succeeded', label: '已分析' }, { value: 'failed', label: '分析失败' }, { value: 'stale', label: '结果待复核' }, { value: 'skipped', label: '无需分析' }]} />
          <Select value={query.decision} onChange={(value) => submitQuery({ decision: value })} options={[{ value: '', label: '全部人工状态' }, { value: 'new', label: '未处理' }, { value: 'following', label: '跟进中' }, { value: 'verify', label: '待核实' }, { value: 'closed', label: '已关闭' }]} />
          <Select value={query.ai_decision} onChange={(value) => submitQuery({ ai_decision: value })} options={[{ value: '', label: '全部 AI 建议' }, { value: 'recommend', label: '建议跟进' }, { value: 'consider', label: '有条件跟进' }, { value: 'verify', label: '待核实' }, { value: 'not_recommended', label: '不建议' }]} />
          <Input value={query.kind} onChange={(event) => setQuery((current) => ({ ...current, kind: event.target.value }))} onPressEnter={(event) => submitQuery({ kind: event.currentTarget.value })} placeholder="公告类别" allowClear />
          <Input value={query.source_id} onChange={(event) => setQuery((current) => ({ ...current, source_id: event.target.value }))} onPressEnter={(event) => submitQuery({ source_id: event.currentTarget.value })} placeholder="来源 ID" allowClear />
          <Select value={query.sort} onChange={(value: ProjectQuery['sort']) => submitQuery({ sort: value })} options={[{ value: 'updated', label: '按更新时间' }, { value: 'deadline', label: '按截止时间' }]} />
        </div>
        {projectsError ? <ErrorBlock error={projectsError} onRetry={() => void loadProjects(query)} /> : loadingProjects ? <LoadingBlock text="正在读取项目" /> : projects.length === 0 ? <EmptyBlock description={query.q || query.gate || query.ai_status || query.ai_decision || query.decision || query.kind || query.source_id || query.deadline_soon ? '没有符合筛选条件的项目' : '暂无项目数据'} /> : <Table<ProjectSummary> rowKey="id" size="middle" columns={columns} dataSource={projects} scroll={{ x: 980 }} pagination={{ current: query.page, pageSize: query.page_size, total, showSizeChanger: true, showTotal: (value) => `共 ${value} 个`, pageSizeOptions: [15, 30, 60] }} onChange={onPageChange} />}
      </Card>
    </div>
  )

  function setQueryAndLoadDeadline() {
    startFromInitial({ sort: 'deadline', deadline_soon: true })
  }
}
