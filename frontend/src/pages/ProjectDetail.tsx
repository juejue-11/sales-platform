import { useCallback, useEffect, useRef, useState } from 'react'
import { useLocation, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { Alert, Button, Space, Tabs, Tag, Typography, message } from 'antd'
import { CalendarOutlined, EnvironmentOutlined, AppstoreOutlined, FolderOpenOutlined, ShareAltOutlined, ArrowLeftOutlined, FileSearchOutlined, RobotOutlined } from '@ant-design/icons'
import { api } from '../api'
import type { FollowupData, ProjectDetail as ProjectDetailData } from '../types'
import { ErrorBlock, LoadingBlock, GateTag, formatDate } from '../components/Common'
import { ProcurementReadingPanel, ProcurementSupplement } from '../components/procurement'
import { FollowupPanel } from '../components/followup/FollowupPanel'
import { readingStageLabel } from '../components/procurement/reading'
import { DecisionDashboard } from '../components/analysis/DecisionDashboard'
import { DetailCard, ProcurementOverview, ProjectInfoRail } from '../components/procurement/ProcurementOverview'
import './ProjectDetail.css'

const { Text, Title } = Typography

const detailTabs = [
  { key: 'overview', label: '项目概览' },
  { key: 'analysis', label: 'AI 研判' },
  { key: 'procurement', label: '采购内容' },
  { key: 'qualification', label: '资格要求' },
  { key: 'schedule', label: '时间节点' },
  { key: 'attachments', label: '相关附件' },
  { key: 'followup', label: '跟进记录' },
] as const

export function ProjectDetailPage() {
  const { id } = useParams<{ id: string }>()
  const projectId = Number(id)
  const navigate = useNavigate()
  const location = useLocation()
  const returnTo = useRef(typeof location.state?.from === 'string' && location.state.from.startsWith('/?') ? location.state.from : '/')
  const [searchParams, setSearchParams] = useSearchParams()
  const requestedTab = searchParams.get('tab')
  const activeTab = detailTabs.some((tab) => tab.key === requestedTab) ? requestedTab! : 'overview'
  const tabBarRef = useRef<HTMLDivElement>(null)
  const [detail, setDetail] = useState<ProjectDetailData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<unknown>(null)
  const [actionLoading, setActionLoading] = useState('')
  const [procurementLoading, setProcurementLoading] = useState(false)
  const [procurementError, setProcurementError] = useState<unknown>(null)
  const projectIdRef = useRef(projectId)
  const requestSequenceRef = useRef(0)
  projectIdRef.current = projectId

  const handleFollowupChange = useCallback((followup: FollowupData) => {
    setDetail(current => current ? { ...current, followup, is_following: followup.is_following, followup_stage: followup.stage } : current)
  }, [])

  const loadDetail = useCallback(async (showLoading = true) => {
    const requestedProjectId = projectIdRef.current
    const sequence = ++requestSequenceRef.current
    const current = () => sequence === requestSequenceRef.current && projectIdRef.current === requestedProjectId
    if (!Number.isInteger(requestedProjectId) || requestedProjectId <= 0) {
      setError(new Error('项目编号无效'))
      setLoading(false)
      return
    }
    if (showLoading) setLoading(true)
    setError(null)
    try {
      const result = await api.project(requestedProjectId)
      if (current()) setDetail(result)
    } catch (loadError) {
      if (current()) setError(loadError)
    } finally {
      if (showLoading && current()) setLoading(false)
    }
  }, [])

  useEffect(() => {
    requestSequenceRef.current += 1
    setDetail(null)
    setError(null)
    void loadDetail(true)
  }, [loadDetail, projectId])

  useEffect(() => {
    if (!detail || !['queued', 'running'].includes(detail.ai_status)) return
    const timer = window.setInterval(() => { void loadDetail(false) }, detail.ai_status === 'running' ? 3000 : 8000)
    return () => window.clearInterval(timer)
  }, [detail?.ai_status, loadDetail])

  const changeTab = (key: string) => {
    setSearchParams((current) => {
      const next = new URLSearchParams(current)
      next.set('tab', key)
      return next
    }, { preventScrollReset: true })
    const tabBar = tabBarRef.current
    if (tabBar && tabBar.getBoundingClientRect().top < 0) tabBar.scrollIntoView({ block: 'start' })
  }

  const runAction = async (key: string, action: () => Promise<unknown>, success: string) => {
    setActionLoading(key)
    try {
      await action()
      message.success(success)
      await loadDetail(false)
    } catch (actionError) {
      message.error(actionError instanceof Error ? actionError.message : '操作失败，请稍后重试')
    } finally { setActionLoading('') }
  }

  const refreshProcurement = async () => {
    setProcurementLoading(true)
    setProcurementError(null)
    try {
      const result = await api.refreshDocuments(projectId)
      setDetail((current) => current ? { ...current, procurement: result.procurement } : current)
      message.success(result.message || '采购资料已更新')
      await loadDetail(false)
    } catch (refreshError) {
      setProcurementError(refreshError)
      message.error(refreshError instanceof Error ? refreshError.message : '采购资料更新失败，请稍后重试')
    } finally { setProcurementLoading(false) }
  }

  if (loading) return <div className="page-stack"><LoadingBlock text="正在读取项目详情" /></div>
  if (error || !detail) return <div className="page-stack"><Button type="link" icon={<ArrowLeftOutlined />} onClick={() => navigate('/')}>返回机会池</Button><ErrorBlock error={error} onRetry={() => void loadDetail(true)} /></div>

  const reading = detail.procurement_reading ?? null
  const gateRestored = detail.gate_reasons.some((reason) => reason === '人工恢复规则排除')
  const tabPanels = {
    overview: <ProcurementOverview detail={detail} onTab={changeTab} />,
    qualification: <ProcurementOverview detail={detail} view="qualification" onTab={changeTab} />,
    schedule: <ProcurementOverview detail={detail} view="schedule" onTab={changeTab} />,
    attachments: <div className="detail-tab-panel"><DetailCard title="相关附件" icon={<FolderOpenOutlined />}><div className="project-attachment-list">{detail.procurement?.documents?.length ? detail.procurement.documents.map(document => <div key={document.id}><FileSearchOutlined /><div><strong>{document.name}</strong><Text type="secondary">{({ parsed: '已读取', failed: '读取失败', unsupported: '格式暂不支持', auth_required: '需授权访问', unreadable: '内容无法读取' })[document.status]}</Text></div>{document.url && <a href={document.url} target="_blank" rel="noreferrer">查看文件</a>}</div>) : <p className="project-empty-note">暂无已获取的公开附件，可在下方补充文件。</p>}</div></DetailCard><ProcurementSupplement projectId={projectId} records={detail.records} onUploaded={() => void loadDetail(false)} /></div>,
    procurement: <div className="detail-tab-panel detail-procurement-panel"><ProcurementReadingPanel projectId={projectId} reading={reading} briefing={detail.briefing} legacyProcurement={detail.procurement} records={detail.records} onRefresh={() => void refreshProcurement()} refreshing={procurementLoading} error={procurementError} onOpenOperations={() => navigate('/operations')} auxiliary={<ProjectInfoRail compact detail={detail} refreshing={procurementLoading} onRefresh={() => void refreshProcurement()} onFollowup={() => changeTab('followup')} onAttachments={() => changeTab('attachments')} />} /></div>,
    analysis: <DecisionDashboard key={projectId} detail={detail} onFollowup={() => changeTab('followup')} onFollowupChange={handleFollowupChange} />,
    followup: <div className="detail-tab-panel"><FollowupPanel projectId={projectId} initialFollowup={detail.followup} onChange={handleFollowupChange} /></div>,
  } as const

  return <div className={`page-stack detail-page${activeTab === 'procurement' ? ' detail-procurement-view' : activeTab === 'analysis' ? ' detail-analysis-view' : ''}`}>
    <div className="detail-main-column">
      <div className="detail-toolbar"><Button className="detail-back-button" type="link" icon={<ArrowLeftOutlined />} onClick={() => navigate(returnTo.current, { state: { restoreDesk: true } })}>{returnTo.current === '/' ? '返回首页' : '返回列表'}</Button></div>
      <header className="detail-hero"><Title level={2} title={detail.title || undefined}>{detail.title || '未命名项目'}</Title><div className="detail-subline"><Text className="detail-buyer">{detail.buyer || reading?.buyer || '采购方待核对'}</Text><Space className="detail-status-tags" wrap><Tag color={detail.demand_type === 'lead' || reading?.demand_type === 'lead' ? 'cyan' : 'blue'}>{detail.demand_type === 'lead' || reading?.demand_type === 'lead' ? '潜在线索' : '明确采购'}</Tag><Tag>{readingStageLabel(reading?.stage ?? detail.procurement_stage)}</Tag>{activeTab !== 'procurement' && <GateTag status={detail.gate_status} />}</Space></div>
      {activeTab !== 'analysis' && <div className="detail-project-meta"><span><EnvironmentOutlined />{detail.region || reading?.region || '地区待核对'}</span>{activeTab !== 'procurement' && <><span><AppstoreOutlined />设备 / 服务</span><span><CalendarOutlined />发布时间 {formatDate(detail.published_at, true)}</span><span># 项目编号 {detail.id}</span></>}<Button type="text" size="small" icon={<ShareAltOutlined />} onClick={() => { void navigator.clipboard.writeText(window.location.href).then(() => message.success('项目链接已复制')).catch(() => message.error('复制失败，请复制浏览器地址栏链接')) }}>分享</Button></div>}</header>
      <main className="detail-workspace"><Tabs className="detail-tabs" activeKey={activeTab} onChange={changeTab} size="small" destroyOnHidden={false} renderTabBar={(props, DefaultTabBar) => <div className="detail-tab-bar" ref={tabBarRef}><DefaultTabBar {...props} /></div>} items={detailTabs.map((tab) => ({ ...tab, children: tabPanels[tab.key] }))} /></main>
    </div>
    {activeTab !== 'procurement' && <ProjectInfoRail detail={detail} refreshing={procurementLoading} onRefresh={() => void refreshProcurement()} onFollowup={() => changeTab('followup')} onAttachments={() => changeTab('attachments')}>
      <div className="detail-actions" aria-label="项目操作">{(['failed', 'stale', 'succeeded', 'idle'].includes(detail.ai_status)) && <Button icon={<RobotOutlined />} loading={actionLoading === 'analyze'} onClick={() => void runAction('analyze', () => api.analyze(projectId, detail.ai_status !== 'idle'), '已提交研判任务')}>{detail.ai_status === 'idle' ? '开始研判' : '重新研判'}</Button>}{detail.ai_status === 'not_configured' && <Button icon={<RobotOutlined />} onClick={() => navigate('/settings')}>配置 AI 研判</Button>}{detail.gate_status === 'excluded' && <Button loading={actionLoading === 'gate'} onClick={() => void runAction('gate', () => api.gate(projectId, 'restore', '人工恢复采购需求'), '采购需求已恢复')}>恢复需求</Button>}{gateRestored && <Button loading={actionLoading === 'gate'} onClick={() => void runAction('gate', () => api.gate(projectId, 'clear', '清除人工恢复规则'), '已清除恢复规则')}>清除恢复</Button>}</div>
      {Boolean(procurementError) && <Alert type="error" showIcon message={procurementError instanceof Error ? procurementError.message : '资料更新失败，请重试'} />}
    </ProjectInfoRail>}
  </div>
}
