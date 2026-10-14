import { Button, Pagination } from 'antd'
import { WarningOutlined } from '@ant-design/icons'
import { useCallback, useEffect, useRef } from 'react'
import { ErrorBlock, EmptyBlock, LoadingBlock, formatFreshness } from '../components/Common'
import { OpportunityFeedItem } from '../components/opportunities/OpportunityFeedItem'
import { OpportunityFilters } from '../components/opportunities/OpportunityFilters'
import { saveOpportunityDeskNavigation, useOpportunityDesk } from '../hooks/useOpportunityDesk'
import './Dashboard.css'

export function DashboardPage() {
  const desk = useOpportunityDesk()
  const hasImportNotice = Boolean(desk.dashboard?.import_error || desk.dashboard?.file_stale || desk.dashboard?.import_pending)
  const sourceNotes = desk.dashboard?.source_notes?.trim()
  const restoredScroll = useRef(false)
  const rememberAndOpen = useCallback((_id: number) => {
    saveOpportunityDeskNavigation({ query: desk.query, draftSearch: desk.draftSearch, advancedOpen: desk.advancedOpen, scrollY: window.scrollY })
  }, [desk.advancedOpen, desk.draftSearch, desk.query])

  useEffect(() => {
    if (desk.restoredScrollY == null || desk.loadingProjects || restoredScroll.current) return
    restoredScroll.current = true
    const frame = window.requestAnimationFrame(() => window.scrollTo({ top: desk.restoredScrollY ?? 0, behavior: 'auto' }))
    return () => window.cancelAnimationFrame(frame)
  }, [desk.loadingProjects, desk.restoredScrollY])

  return (
    <main className="opportunity-page">
      <header className="opportunity-hero"><div><span className="opportunity-eyebrow">采购需求目录</span><h1>先看清需求，再决定跟进</h1><p>平台持续整理已接入来源中的采购信息。明确采购与潜在线索分开标识，提交时间只使用采购阅读中的有来源节点。</p></div><div className="opportunity-hero-note"><strong>{desk.total.toLocaleString('zh-CN')}</strong><span>当前筛选</span></div></header>
      {(desk.dashboardError || hasImportNotice) && <section className="opportunity-notices" aria-label="数据提醒">
        <div className="opportunity-notice-heading"><WarningOutlined aria-hidden="true" /><span>数据提醒</span></div>
        {desk.dashboardError != null && <div className="opportunity-notice opportunity-notice-danger"><strong>统计暂时无法读取</strong><span>{desk.errorMessage(desk.dashboardError).message}</span><Button size="small" onClick={desk.refresh}>重试</Button></div>}
        {desk.dashboard?.import_error && <div className="opportunity-notice opportunity-notice-danger"><strong>采集文件存在异常</strong><span>{desk.dashboard.import_error}</span></div>}
        {desk.dashboard?.file_stale && <div className="opportunity-notice"><strong>采集文件可能已过期</strong><span>来源更新时间需要在处理状态中确认。</span></div>}
        {desk.dashboard?.import_pending && <div className="opportunity-notice opportunity-notice-info"><strong>新采集数据尚未导入</strong><span>请前往处理状态查看导入进度。</span></div>}
      </section>}
      <OpportunityFilters tabs={desk.tabs} activeTab={desk.activeTab} query={desk.query} draftSearch={desk.draftSearch} advancedOpen={desk.advancedOpen} resultCount={desk.total} refreshTitle={`刷新采购列表；${formatFreshness(desk.dashboard?.data_updated_at)}`} onSelectTab={desk.selectTab} onSearchChange={desk.setDraftSearch} onSearch={desk.submitSearch} onUpdateFilter={desk.updateFilter} onReset={desk.resetFilters} onToggleAdvanced={() => desk.setAdvancedOpen((open) => !open)} onRefresh={desk.refresh} />
      <section className="opportunity-feed" aria-labelledby="opportunity-feed-heading">
        <div className="opportunity-feed-heading-row"><div><h2 id="opportunity-feed-heading">采购需求</h2><p>卡片与详情使用同一份采购阅读事实。资料失败原因请到处理状态查看。</p></div><span>{desk.loadingProjects ? '读取中…' : `${desk.total.toLocaleString('zh-CN')} 条`}</span></div>
        {desk.projectsError != null ? <ErrorBlock error={desk.projectsError} onRetry={desk.retryProjects} /> : desk.loadingProjects ? <LoadingBlock text="正在读取采购信息" /> : desk.projects.length === 0 ? <EmptyBlock description={desk.query.q || desk.query.category || desk.query.region || desk.query.buyer || desk.query.procurement_stage || desk.query.demand_type ? '没有符合筛选条件的采购信息' : '暂无可浏览的采购信息'} /> : <div className="opportunity-feed-list">{desk.projects.map((project) => <OpportunityFeedItem key={project.id} project={project} onOpen={rememberAndOpen} />)}</div>}
        {!desk.loadingProjects && !desk.projectsError && desk.total > 0 && <Pagination className="opportunity-pagination" current={desk.query.page} pageSize={desk.query.page_size} total={desk.total} showSizeChanger pageSizeOptions={[12, 24, 48]} onChange={desk.changePage} showTotal={(value) => `共 ${value} 条`} />}
      </section>
      {sourceNotes && <details className="opportunity-source-notes"><summary>来源覆盖说明</summary><p>{sourceNotes}</p></details>}
    </main>
  )
}
