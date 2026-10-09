import { Button, Pagination, Typography } from 'antd'
import { DatabaseOutlined, ReloadOutlined, RobotOutlined, WarningOutlined } from '@ant-design/icons'
import { useNavigate } from 'react-router-dom'
import { ErrorBlock, EmptyBlock, LoadingBlock, formatFreshness } from '../components/Common'
import { FeaturedOpportunity } from '../components/opportunities/FeaturedOpportunity'
import { OpportunityFeedItem } from '../components/opportunities/OpportunityFeedItem'
import { OpportunityFilters } from '../components/opportunities/OpportunityFilters'
import { useOpportunityDesk } from '../hooks/useOpportunityDesk'
import './Dashboard.css'

const { Title } = Typography

export function DashboardPage() {
  const navigate = useNavigate()
  const desk = useOpportunityDesk()
  const hasImportNotice = Boolean(desk.dashboard?.import_error || desk.dashboard?.file_stale || desk.dashboard?.import_pending)
  const sourceNotes = desk.dashboard?.source_notes?.trim()

  return (
    <main className="opportunity-page">
      <header className="opportunity-heading">
        <div>
          <Title level={2}>机会池</Title>
          <p>采购什么、能否参与、何时截止，在这里先看清。</p>
        </div>
        <div className="opportunity-sync" aria-label="数据同步状态">
          <DatabaseOutlined aria-hidden="true" />
          <span>{formatFreshness(desk.dashboard?.data_updated_at)}</span>
          <Button type="text" icon={<ReloadOutlined />} onClick={desk.refresh}>刷新</Button>
        </div>
      </header>

      {(desk.dashboardError || hasImportNotice) && (
        <section className="opportunity-notices" aria-label="数据提醒">
          <div className="opportunity-notice-heading"><WarningOutlined aria-hidden="true" /><span>数据提醒</span></div>
          {desk.dashboardError != null && <div className="opportunity-notice opportunity-notice-danger"><strong>统计暂时无法读取</strong><span>{desk.errorMessage(desk.dashboardError).message}</span><Button size="small" onClick={desk.refresh}>重试</Button></div>}
          {desk.dashboard?.import_error && <div className="opportunity-notice opportunity-notice-danger"><strong>采集文件存在异常</strong><span>{desk.dashboard.import_error}</span></div>}
          {desk.dashboard?.file_stale && <div className="opportunity-notice"><strong>采集文件可能已过期</strong><span>暂未看到近期 records.jsonl 更新，请检查采集系统和导入路径。</span></div>}
          {desk.dashboard?.import_pending && <div className="opportunity-notice opportunity-notice-info"><strong>新采集数据尚未导入</strong><span>自动导入可能未开启或仍待处理，请前往处理状态手动导入。</span></div>}
        </section>
      )}

      <FeaturedOpportunity project={desk.featured} basis={desk.featuredBasis} loading={desk.loadingDashboard || desk.loadingProjects} onOpen={(id) => navigate(`/projects/${id}`)} />

      {desk.dashboard && !desk.dashboard.ai_configured && (
        <div className="opportunity-ai-note"><RobotOutlined aria-hidden="true" /><span>当前展示公告信息与规则筛选；配置模型后可查看 AI 理由和方案。</span><Button type="link" onClick={() => navigate('/settings')}>去配置</Button></div>
      )}

      <OpportunityFilters
        tabs={desk.tabs}
        activeTab={desk.activeTab}
        query={desk.query}
        draftSearch={desk.draftSearch}
        advancedOpen={desk.advancedOpen}
        resultCount={desk.total}
        onSelectTab={desk.selectTab}
        onSearchChange={desk.setDraftSearch}
        onSearch={desk.submitSearch}
        onUpdateFilter={desk.updateFilter}
        onReset={desk.resetFilters}
        onToggleAdvanced={() => desk.setAdvancedOpen((open) => !open)}
        onRefresh={desk.refresh}
      />

      <section className="opportunity-feed" aria-labelledby="opportunity-feed-heading">
        <header className="opportunity-feed-section-heading">
          <div><h2 id="opportunity-feed-heading">采购信息</h2><p>公告原文摘录，可展开核对。</p></div>
          <span>{desk.total.toLocaleString('zh-CN')} 条</span>
        </header>
        {desk.projectsError != null ? <ErrorBlock error={desk.projectsError} onRetry={desk.retryProjects} /> : desk.loadingProjects ? <LoadingBlock text="正在读取采购信息" /> : desk.projects.length === 0 ? <EmptyBlock description={desk.query.q || desk.query.gate || desk.query.ai_decision ? '没有符合筛选条件的采购信息' : '暂无候选或待核实采购信息'} /> : (
          <div className="opportunity-feed-list">
            {desk.projects.map((project) => <OpportunityFeedItem key={project.id} project={project} detailState={desk.details[project.id]} expanded={desk.expandedSourceId === project.id} onOpen={(id) => navigate(`/projects/${id}`)} onToggleOriginal={desk.toggleOriginal} onRetryOriginal={desk.retryOriginal} />)}
          </div>
        )}
        {!desk.loadingProjects && !desk.projectsError && desk.total > 0 && <Pagination className="opportunity-pagination" current={desk.query.page} pageSize={desk.query.page_size} total={desk.total} showSizeChanger pageSizeOptions={[12, 24, 48]} onChange={desk.changePage} showTotal={(value) => `共 ${value} 条`} />}
      </section>

      {sourceNotes && <details className="opportunity-source-notes"><summary>来源说明</summary><p>{sourceNotes}</p></details>}
    </main>
  )
}
