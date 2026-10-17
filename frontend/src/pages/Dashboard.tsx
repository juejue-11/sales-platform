import { useLocation } from 'react-router-dom'
import { Button, Pagination } from 'antd'
import { WarningOutlined } from '@ant-design/icons'
import { useCallback, useEffect, useRef, useState } from 'react'
import { ErrorBlock, EmptyBlock, LoadingBlock, formatFreshness } from '../components/Common'
import { OpportunityFeedItem } from '../components/opportunities/OpportunityFeedItem'
import { OpportunityFilters } from '../components/opportunities/OpportunityFilters'
import { saveOpportunityDeskNavigation, useOpportunityDesk } from '../hooks/useOpportunityDesk'
import { HomeDashboard } from './HomeDashboard'
import { FollowupPage } from './FollowupPage'
import './Dashboard.css'

export function DashboardPage() {
  const location = useLocation()
  if (new URLSearchParams(location.search).get('view') === 'following') return <FollowupPage />
  return location.search ? <ProcurementDeskPage /> : <HomeDashboard />
}

function ProcurementDeskPage() {
  const desk = useOpportunityDesk()
  const location = useLocation()
  const [viewMode, setViewMode] = useState<'cards' | 'list'>(() => desk.restoredViewMode ?? 'cards')
  const appliedSearch = useRef<string | null>(desk.restoredScrollY != null ? location.search : null)
  useEffect(() => {
    if (appliedSearch.current === location.search) return
    appliedSearch.current = location.search
    const params = new URLSearchParams(location.search)
    const view = params.get('view')
    const q = params.get('q')
    const region = params.get('region')
    const overrides = { ...(q !== null ? { q } : {}), ...(region !== null ? { region } : {}) }
    if (view === 'following' || view === 'ai' || view === 'procurement' || view === 'all' || view === 'deadline' || view === 'queued' || view === 'review' || view === 'analyzed') desk.selectTab(view, overrides)
    else desk.selectTab('all', overrides)
  }, [location.search])
  const hasImportNotice = Boolean(desk.dashboard?.import_error || desk.dashboard?.file_stale || desk.dashboard?.import_pending)
  const sourceNotes = desk.dashboard?.source_notes?.trim()
  const title = new URLSearchParams(location.search).get('view') === 'following' ? '项目跟进' : '采购需求'
  const restoredScroll = useRef(false)
  const rememberAndOpen = useCallback((_id: number) => {
    saveOpportunityDeskNavigation({ query: desk.query, draftSearch: desk.draftSearch, advancedOpen: desk.advancedOpen, scrollY: window.scrollY, search: location.search, viewMode })
  }, [desk.advancedOpen, desk.draftSearch, desk.query, location.search, viewMode])
  useEffect(() => {
    if (desk.restoredScrollY == null || desk.loadingProjects || restoredScroll.current) return
    restoredScroll.current = true
    const frame = window.requestAnimationFrame(() => window.scrollTo({ top: desk.restoredScrollY ?? 0, behavior: 'auto' }))
    return () => window.cancelAnimationFrame(frame)
  }, [desk.loadingProjects, desk.restoredScrollY])

  const selectCategory = (key: Parameters<typeof desk.selectTab>[0]) => {
    const { q, region, category, procurement_stage, published_days, sort, buyer, key_time } = desk.query
    desk.selectTab(key, { q, region, category, procurement_stage, published_days, sort, buyer, key_time })
  }

  return <main className="opportunity-page procurement-catalog">
    <header className="catalog-heading"><h1>{title}</h1><p>汇聚采购信息，支持多维度筛选与 AI 研判，快速发现值得关注的项目。</p></header>
    {(desk.dashboardError || hasImportNotice) && <section className="opportunity-notices" aria-label="数据提醒">
      <div className="opportunity-notice-heading"><WarningOutlined aria-hidden="true" /><span>数据提醒</span></div>
      {desk.dashboardError != null && <div className="opportunity-notice opportunity-notice-danger"><strong>统计暂时无法读取</strong><span>{desk.errorMessage(desk.dashboardError).message}</span><Button size="small" onClick={desk.refresh}>重试</Button></div>}
      {desk.dashboard?.import_error && <div className="opportunity-notice opportunity-notice-danger"><strong>采集文件存在异常</strong><span>{desk.dashboard.import_error}</span></div>}
      {desk.dashboard?.file_stale && <div className="opportunity-notice"><strong>采集文件可能已过期</strong><span>请在处理状态中确认来源更新时间。</span></div>}
      {desk.dashboard?.import_pending && <div className="opportunity-notice"><strong>新采集数据尚未导入</strong><span>请在处理状态中查看导入进度。</span></div>}
    </section>}
    <OpportunityFilters tabs={desk.tabs} activeTab={desk.activeTab} query={desk.query} advancedOpen={desk.advancedOpen} resultCount={desk.total} countsUnavailable={desk.loadingDashboard || desk.dashboardError != null} refreshTitle={`刷新采购列表；${formatFreshness(desk.dashboard?.data_updated_at)}`} viewMode={viewMode} onViewModeChange={setViewMode} onSelectTab={selectCategory} onApplyFilters={desk.applyFilters} onUpdateFilter={desk.updateFilter} onReset={desk.resetFilters} onToggleAdvanced={() => desk.setAdvancedOpen(open => !open)} onRefresh={desk.refresh} />
    <section className="opportunity-feed" aria-label="采购项目列表" aria-busy={desk.loadingProjects}>
      {desk.projectsError != null ? <ErrorBlock error={desk.projectsError} onRetry={desk.retryProjects} /> : desk.loadingProjects ? <LoadingBlock text="正在读取采购信息" /> : desk.projects.length === 0 ? <EmptyBlock description="没有符合筛选条件的采购信息，试试调整条件或重置筛选" /> : <div className={`opportunity-feed-list catalog-view-${viewMode}`}>{desk.projects.map(project => <OpportunityFeedItem key={project.id} project={project} onOpen={rememberAndOpen} onFollowupChange={desk.refresh} />)}</div>}
      {!desk.loadingProjects && !desk.projectsError && desk.total > desk.query.page_size && <Pagination className="opportunity-pagination" current={desk.query.page} pageSize={desk.query.page_size} total={desk.total} showSizeChanger pageSizeOptions={[12, 24, 48]} onChange={desk.changePage} showTotal={value => `共 ${value} 条`} />}
    </section>
    <footer className="catalog-footer"><span>{desk.loadingProjects ? '正在读取…' : `共 ${desk.total.toLocaleString('zh-CN')} 个项目`}</span><span>{formatFreshness(desk.dashboard?.data_updated_at)}</span></footer>
    {sourceNotes && <details className="opportunity-source-notes"><summary>数据来源与覆盖说明</summary><p>{sourceNotes}</p></details>}
  </main>
}
