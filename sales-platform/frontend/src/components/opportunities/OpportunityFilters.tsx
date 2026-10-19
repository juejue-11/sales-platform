import { useEffect, useState } from 'react'
import { Button, Input, Select, Tooltip } from 'antd'
import { AppstoreOutlined, DownOutlined, EnvironmentOutlined, ReloadOutlined, SearchOutlined, UnorderedListOutlined, UndoOutlined } from '@ant-design/icons'
import { procurementStageLabels } from '../procurement/reading'
import type { OpportunityQuery, OpportunityTab, OpportunityTabKey } from '../../hooks/useOpportunityDesk'

interface OpportunityFiltersProps {
  tabs: OpportunityTab[]
  activeTab: OpportunityTabKey | null
  query: OpportunityQuery
  advancedOpen: boolean
  resultCount: number
  countsUnavailable: boolean
  refreshTitle: string
  viewMode: 'cards' | 'list'
  onViewModeChange: (mode: 'cards' | 'list') => void
  onSelectTab: (key: OpportunityTabKey) => void
  onApplyFilters: (filters: Partial<OpportunityQuery>) => void
  onUpdateFilter: <K extends keyof OpportunityQuery>(key: K, value: OpportunityQuery[K]) => void
  onReset: () => void
  onToggleAdvanced: () => void
  onRefresh: () => void
}

const provinces = ['北京', '天津', '河北', '山西', '内蒙古', '辽宁', '吉林', '黑龙江', '上海', '江苏', '浙江', '安徽', '福建', '江西', '山东', '河南', '湖北', '湖南', '广东', '广西', '海南', '重庆', '四川', '贵州', '云南', '西藏', '陕西', '甘肃', '青海', '宁夏', '新疆', '台湾', '香港', '澳门']
const regionOptions = provinces.map(value => ({ value, label: value }))
const categoryOptions = ['破碎机', '压裂泵', '钢材', '机械设备', '劳务服务', '施工工程', '技术服务'].map(value => ({ value, label: value }))
const stageOptions = Object.entries(procurementStageLabels).map(([value, label]) => ({ value, label }))
const gateOptions = [{ value: '', label: '全部规则状态' }, ...Object.entries({ candidate: '候选', review: '待核实', excluded: '已排除', expired: '已截止', closed: '已关闭', signal: '线索', duplicate: '确认重复' }).map(([value, label]) => ({ value, label }))]
const aiStatusOptions = [{ value: '', label: '全部 AI 状态' }, ...Object.entries({ not_configured: '未配置模型', idle: '待分析', queued: '等待分析', running: '分析中', succeeded: '已研判', failed: '分析失败', stale: '结果待复核', skipped: '无需分析' }).map(([value, label]) => ({ value, label }))]
const timeOptions = [{ value: '', label: '全部提交时间' }, { value: 'today', label: '今天需办理' }, { value: '7d', label: '7 天内提交' }, { value: '30d', label: '30 天内提交' }, { value: 'unknown', label: '提交时间待核对' }]
const primaryTabs: OpportunityTabKey[] = ['all', 'procurement', 'leads', 'analyzed']

export function OpportunityFilters({ tabs, activeTab, query, advancedOpen, resultCount, countsUnavailable, refreshTitle, viewMode, onViewModeChange, onSelectTab, onApplyFilters, onUpdateFilter, onReset, onToggleAdvanced, onRefresh }: OpportunityFiltersProps) {
  const [draft, setDraft] = useState(query)
  useEffect(() => setDraft(query), [query])
  const update = <K extends keyof OpportunityQuery>(key: K, value: OpportunityQuery[K]) => setDraft(current => ({ ...current, [key]: value }))
  const apply = () => onApplyFilters(draft)
  const advancedCount = ['gate', 'ai_status', 'decision', 'ai_decision', 'buyer', 'key_time', 'kind', 'source_id'].filter(key => Boolean(draft[key as keyof OpportunityQuery])).length
  const selectedRegionOptions = draft.region && !provinces.includes(draft.region) ? [{ value: draft.region, label: draft.region }, ...regionOptions] : regionOptions

  return <section className="catalog-controls" aria-label="采购需求筛选">
    <form className="catalog-filter-row" onSubmit={event => { event.preventDefault(); apply() }}>
      <div className="catalog-filter-group">
        <div className="catalog-region-filter"><EnvironmentOutlined aria-hidden="true" /><Select aria-label="地区" variant="borderless" showSearch allowClear placeholder="地区" value={draft.region || undefined} onChange={value => update('region', value ?? '')} options={selectedRegionOptions} suffixIcon={<DownOutlined />} /></div>
        <Select className="catalog-stage-filter" aria-label="采购阶段" variant="borderless" allowClear placeholder="采购阶段" value={draft.procurement_stage || undefined} onChange={value => update('procurement_stage', value ?? '')} options={stageOptions} suffixIcon={<DownOutlined />} />
        <Select className="catalog-category-filter" aria-label="设备或服务" variant="borderless" mode="tags" maxCount={1} allowClear placeholder="设备 / 服务" value={draft.category ? [draft.category] : []} onChange={values => update('category', values[0] ?? '')} options={categoryOptions} suffixIcon={<DownOutlined />} />
        <Select className="catalog-published-filter" aria-label="发布时间" variant="borderless" value={draft.published_days} onChange={value => update('published_days', value)} options={[{ value: 0, label: '发布时间' }, { value: 7, label: '发布近 7 天' }, { value: 30, label: '发布近 30 天' }, { value: 90, label: '发布近 3 个月' }]} suffixIcon={<DownOutlined />} />
        <Button className={`catalog-more-filter${advancedOpen ? ' is-open' : ''}`} type="text" onClick={onToggleAdvanced} aria-label="更多筛选" aria-expanded={advancedOpen}>更多筛选{advancedCount > 0 && <span className="catalog-filter-count">{advancedCount}</span>}<DownOutlined rotate={advancedOpen ? 180 : 0} /></Button>
      </div>
      <div className="catalog-filter-actions"><Button className="catalog-reset" icon={<UndoOutlined />} onClick={onReset} aria-label="重置">重置</Button><Button className="catalog-submit" type="primary" htmlType="submit" icon={<SearchOutlined />} aria-label="搜索">搜索</Button></div>
    </form>
    {advancedOpen && <div className="catalog-advanced" aria-label="更多筛选条件">
      <label><span>关键词</span><Input value={draft.q} placeholder="项目、采购方、设备或关键词" allowClear onChange={event => update('q', event.target.value)} onPressEnter={apply} /></label>
      <label><span>设备 / 服务关键词</span><Input value={draft.category} placeholder="输入设备或服务" allowClear onChange={event => update('category', event.target.value)} onPressEnter={apply} /></label>
      <label><span>地区关键词</span><Input value={draft.region} placeholder="输入省市或地区" allowClear onChange={event => update('region', event.target.value)} onPressEnter={apply} /></label>
      <label><span>采购单位</span><Input value={draft.buyer} placeholder="输入采购单位" allowClear onChange={event => update('buyer', event.target.value)} onPressEnter={apply} /></label>
      <label><span>规则状态</span><Select value={draft.gate} options={gateOptions} onChange={value => update('gate', value)} /></label>
      <label><span>AI 状态</span><Select value={draft.ai_status} options={aiStatusOptions} onChange={value => update('ai_status', value)} /></label>
      <label><span>人工状态</span><Select value={draft.decision} onChange={value => update('decision', value)} options={[{ value: '', label: '全部人工状态' }, { value: 'new', label: '未处理' }, { value: 'following', label: '跟进中' }, { value: 'verify', label: '待核实' }, { value: 'closed', label: '已关闭' }]} /></label>
      <label><span>AI 建议</span><Select value={draft.ai_decision} onChange={value => update('ai_decision', value)} options={[{ value: '', label: '全部 AI 建议' }, { value: 'recommend', label: '建议关注' }, { value: 'consider', label: '有条件关注' }, { value: 'verify', label: '待核实' }, { value: 'not_recommended', label: '不建议关注' }]} /></label>
      <label><span>提交时间</span><Select value={draft.key_time} options={timeOptions} onChange={value => update('key_time', value)} /></label>
      <label><span>公告类型</span><Select value={draft.kind} onChange={value => update('kind', value)} options={[{ value: '', label: '全部公告类型' }, { value: 'procurement', label: '采购公告' }, { value: 'tender', label: '招标公告' }, { value: 'bid', label: '招标公告（bid）' }, { value: 'award', label: '成交 / 中标' }, { value: 'result', label: '结果公告' }, { value: 'news', label: '行业新闻' }, { value: 'signal', label: '线索' }, { value: 'mining_rights', label: '矿业权动态' }]} /></label>
      <label><span>来源 ID</span><Input value={draft.source_id} placeholder="输入来源 ID" allowClear onChange={event => update('source_id', event.target.value)} onPressEnter={apply} /></label>
      <label><span>需求类型</span><Select value={draft.demand_type} onChange={value => update('demand_type', value)} options={[{ value: '', label: '采购与线索' }, { value: 'confirmed', label: '明确采购' }, { value: 'lead', label: '潜在线索' }, { value: 'unknown', label: '类型待核对' }]} /></label>
    </div>}
    <div className="catalog-results-bar">
      <nav className="opportunity-tabs" aria-label="采购需求分类">{primaryTabs.map(key => {
        const tab = tabs.find(item => item.key === key)!
        return <button type="button" key={key} className={`opportunity-tab${activeTab === key ? ' opportunity-tab-active' : ''}`} aria-pressed={activeTab === key} onClick={() => onSelectTab(key)}><span>{tab.label}</span><strong>{countsUnavailable ? '—' : tab.count.toLocaleString('zh-CN')}</strong></button>
      })}</nav>
      <div className="catalog-display-controls" aria-label={`共 ${resultCount} 个项目，排序与显示方式`}>
        <Select className="catalog-sort" aria-label="项目排序" variant="borderless" value={query.sort} onChange={value => onUpdateFilter('sort', value)} options={[{ value: 'published', label: '最新发布' }, { value: 'updated', label: '最近更新' }, { value: 'deadline', label: '提交截止' }]} suffixIcon={<DownOutlined />} />
        <div className="catalog-view-switch" aria-label="显示方式"><Tooltip title="卡片视图"><Button type="text" icon={<AppstoreOutlined />} aria-label="卡片视图" aria-pressed={viewMode === 'cards'} className={viewMode === 'cards' ? 'selected' : ''} onClick={() => onViewModeChange('cards')} /></Tooltip><Tooltip title="列表视图"><Button type="text" icon={<UnorderedListOutlined />} aria-label="列表视图" aria-pressed={viewMode === 'list'} className={viewMode === 'list' ? 'selected' : ''} onClick={() => onViewModeChange('list')} /></Tooltip></div>
        <Tooltip title={refreshTitle}><Button className="catalog-refresh" type="text" icon={<ReloadOutlined />} onClick={onRefresh} aria-label="刷新采购列表" /></Tooltip>
      </div>
    </div>
  </section>
}
