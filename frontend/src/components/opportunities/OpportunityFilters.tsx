import { useEffect, useState } from 'react'
import { Button, Input, Select } from 'antd'
import { FilterOutlined, ReloadOutlined, SearchOutlined } from '@ant-design/icons'
import type { OpportunityQuery, OpportunityTab, OpportunityTabKey } from '../../hooks/useOpportunityDesk'

interface OpportunityFiltersProps {
  tabs: OpportunityTab[]
  activeTab: OpportunityTabKey | null
  query: OpportunityQuery
  draftSearch: string
  advancedOpen: boolean
  resultCount: number
  refreshTitle: string
  onSelectTab: (key: OpportunityTabKey) => void
  onSearchChange: (value: string) => void
  onSearch: (value: string) => void
  onUpdateFilter: <K extends keyof OpportunityQuery>(key: K, value: OpportunityQuery[K]) => void
  onReset: () => void
  onToggleAdvanced: () => void
  onRefresh: () => void
}

const kindOptions = [
  { value: '', label: '全部公告类型' },
  { value: 'procurement', label: '采购公告' },
  { value: 'tender', label: '招标公告' },
  { value: 'bid', label: '招标公告' },
  { value: 'award', label: '成交 / 中标' },
  { value: 'result', label: '成交 / 中标' },
  { value: 'news', label: '行业新闻' },
  { value: 'signal', label: '线索' },
  { value: 'mining_rights', label: '矿业权动态' },
]

const gateOptions = [
  { value: '', label: '全部规则状态' },
  { value: 'candidate', label: '候选' },
  { value: 'review', label: '待核实' },
  { value: 'excluded', label: '已排除' },
  { value: 'expired', label: '已截止' },
  { value: 'closed', label: '已关闭' },
  { value: 'signal', label: '线索' },
  { value: 'duplicate', label: '确认重复' },
]

const aiStatusOptions = [
  { value: '', label: '全部 AI 状态' },
  { value: 'not_configured', label: '未配置模型' },
  { value: 'idle', label: '待分析' },
  { value: 'queued', label: '等待分析' },
  { value: 'running', label: '分析中' },
  { value: 'succeeded', label: '已分析' },
  { value: 'failed', label: '分析失败' },
  { value: 'stale', label: '结果待复核' },
  { value: 'skipped', label: '无需分析' },
]

const decisionOptions = [
  { value: '', label: '全部人工状态' },
  { value: 'new', label: '未处理' },
  { value: 'following', label: '跟进中' },
  { value: 'verify', label: '待核实' },
  { value: 'closed', label: '已关闭' },
]

const aiDecisionOptions = [
  { value: '', label: '全部 AI 建议' },
  { value: 'recommend', label: '建议跟进' },
  { value: 'consider', label: '有条件跟进' },
  { value: 'verify', label: '待核实' },
  { value: 'not_recommended', label: '不建议' },
]

export function OpportunityFilters({ tabs, activeTab, query, draftSearch, advancedOpen, resultCount, refreshTitle, onSelectTab, onSearchChange, onSearch, onUpdateFilter, onReset, onToggleAdvanced, onRefresh }: OpportunityFiltersProps) {
  const [sourceDraft, setSourceDraft] = useState(query.source_id)
  useEffect(() => setSourceDraft(query.source_id), [query.source_id])

  return (
    <section className="opportunity-controls" aria-label="机会筛选">
      <nav className="opportunity-tabs" aria-label="机会分类">
        {tabs.map((tab) => (
          <button
            type="button"
            key={tab.key}
            className={`opportunity-tab${activeTab === tab.key ? ' opportunity-tab-active' : ''}`}
            aria-current={activeTab === tab.key ? 'page' : undefined}
            onClick={() => onSelectTab(tab.key)}
          >
            <span>{tab.label}</span><strong>{tab.count.toLocaleString('zh-CN')}</strong>
          </button>
        ))}
      </nav>
      <div className="opportunity-toolbar">
        <Input.Search
          className="opportunity-search"
          value={draftSearch}
          allowClear
          onChange={(event) => onSearchChange(event.target.value)}
          onSearch={onSearch}
          placeholder="搜索项目标题、采购方、设备"
          enterButton={<span>搜索</span>}
          prefix={<SearchOutlined aria-hidden="true" />}
        />
        <div className="opportunity-toolbar-actions">
          <span className="opportunity-result-count">{resultCount.toLocaleString('zh-CN')} 条</span>
          <Button className={advancedOpen ? 'opportunity-advanced-button opportunity-advanced-button-open' : 'opportunity-advanced-button'} icon={<FilterOutlined />} onClick={onToggleAdvanced} aria-expanded={advancedOpen} aria-label="高级筛选"><span className="opportunity-filter-label">筛选</span></Button>
          <Button className="opportunity-refresh-button" icon={<ReloadOutlined />} onClick={onRefresh} aria-label="刷新机会列表" title={refreshTitle} />
        </div>
      </div>
      {advancedOpen && (
        <div className="opportunity-advanced" aria-label="高级筛选">
          <label><span>规则状态</span><Select value={query.gate} onChange={(value: string) => onUpdateFilter('gate', value)} options={gateOptions} /></label>
          <label><span>AI 状态</span><Select value={query.ai_status} onChange={(value: string) => onUpdateFilter('ai_status', value)} options={aiStatusOptions} /></label>
          <label><span>人工状态</span><Select value={query.decision} onChange={(value: string) => onUpdateFilter('decision', value)} options={decisionOptions} /></label>
          <label><span>AI 建议</span><Select value={query.ai_decision} onChange={(value: string) => onUpdateFilter('ai_decision', value)} options={aiDecisionOptions} /></label>
          <label><span>公告类型</span><Select value={query.kind} onChange={(value: string) => onUpdateFilter('kind', value)} options={kindOptions} /></label>
          <label><span>排序</span><Select value={query.sort} onChange={(value: OpportunityQuery['sort']) => onUpdateFilter('sort', value)} options={[{ value: 'deadline', label: '按截止时间' }, { value: 'updated', label: '按更新时间' }]} /></label>
          <label><span>来源 ID</span><Input value={sourceDraft} allowClear placeholder="输入来源 ID 后回车" onChange={(event) => setSourceDraft(event.target.value)} onPressEnter={() => onUpdateFilter('source_id', sourceDraft)} onClear={() => { setSourceDraft(''); onUpdateFilter('source_id', '') }} /></label>
          <Button className="opportunity-clear-button" onClick={onReset}>清除筛选</Button>
        </div>
      )}
    </section>
  )
}
