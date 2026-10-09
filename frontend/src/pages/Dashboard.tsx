import { Button, Pagination } from 'antd'
import { WarningOutlined } from '@ant-design/icons'
import { useCallback, useEffect, useRef } from 'react'
import { ErrorBlock, EmptyBlock, LoadingBlock, formatFreshness } from '../components/Common'
import { FeaturedOpportunity } from '../components/opportunities/FeaturedOpportunity'
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
    saveOpportunityDeskNavigation({
      query: desk.query,
      draftSearch: desk.draftSearch,
      advancedOpen: desk.advancedOpen,
      scrollY: window.scrollY,
    })
  }, [desk.advancedOpen, desk.draftSearch, desk.query])

  useEffect(() => {
    if (desk.restoredScrollY == null || desk.loadingProjects || restoredScroll.current) return
    restoredScroll.current = true
    const frame = window.requestAnimationFrame(() => window.scrollTo({ top: desk.restoredScrollY ?? 0, behavior: 'auto' }))
    return () => window.cancelAnimationFrame(frame)
  }, [desk.loadingProjects, desk.restoredScrollY])

  return (
    <main className="opportunity-page">
      {(desk.dashboardError || hasImportNotice) && (
        <section className="opportunity-notices" aria-label="数据提醒">
          <div className="opportunity-notice-heading"><WarningOutlined aria-hidden="true" /><span>数据提醒</span></div>
          {desk.dashboardError != null && <div className="opportunity-notice opportunity-notice-danger"><strong>统计暂时无法读取</strong><span>{desk.errorMessage(desk.dashboardError).message}</span><Button size="small" onClick={desk.refresh}>重试</Button></div>}
          {desk.dashboard?.import_error && <div className="opportunity-notice opportunity-notice-danger"><strong>采集文件存在异常</strong><span>{desk.dashboard.import_error}</span></div>}
          {desk.dashboard?.file_stale && <div className="opportunity-notice"><strong>采集文件可能已过期</strong><span>暂未看到近期 records.jsonl 更新，请检查采集系统和导入路径。</span></div>}
          {desk.dashboard?.import_pending && <div className="opportunity-notice opportunity-notice-info"><strong>新采集数据尚未导入</strong><span>自动导入可能未开启或仍待处理，请前往处理状态手动导入。</span></div>}
        </section>
      )}

      <OpportunityFilters
        tabs={desk.tabs}
        activeTab={desk.activeTab}
        query={desk.query}
        draftSearch={desk.draftSearch}
        advancedOpen={desk.advancedOpen}
        resultCount={desk.total}
        refreshTitle={`刷新机会列表；${formatFreshness(desk.dashboard?.data_updated_at)}`}
        onSelectTab={desk.selectTab}
        onSearchChange={desk.setDraftSearch}
        onSearch={desk.submitSearch}
        onUpdateFilter={desk.updateFilter}
        onReset={desk.resetFilters}
        onToggleAdvanced={() => desk.setAdvancedOpen((open) => !open)}
        onRefresh={desk.refresh}
      />

      {desk.activeTab === 'current' && <FeaturedOpportunity project={desk.featured} onOpen={rememberAndOpen} />}

      <section className="opportunity-feed" aria-labelledby="opportunity-feed-heading">
        <h2 className="opportunity-screen-reader" id="opportunity-feed-heading">采购信息</h2>
        {desk.projectsError != null ? <ErrorBlock error={desk.projectsError} onRetry={desk.retryProjects} /> : desk.loadingProjects ? <LoadingBlock text="正在读取采购信息" /> : desk.projects.length === 0 ? <EmptyBlock description={desk.query.q || desk.query.gate || desk.query.ai_decision ? '没有符合筛选条件的采购信息' : '暂无候选或待核实采购信息'} /> : (
          <div className="opportunity-feed-list">
            {desk.projects.map((project) => <OpportunityFeedItem key={project.id} project={project} onOpen={rememberAndOpen} />)}
          </div>
        )}
        {!desk.loadingProjects && !desk.projectsError && desk.total > 0 && <Pagination className="opportunity-pagination" current={desk.query.page} pageSize={desk.query.page_size} total={desk.total} showSizeChanger pageSizeOptions={[12, 24, 48]} onChange={desk.changePage} showTotal={(value) => `共 ${value} 条`} />}
      </section>

      {sourceNotes && <details className="opportunity-source-notes"><summary>来源说明</summary><p>{sourceNotes}</p></details>}
    </main>
  )
}
