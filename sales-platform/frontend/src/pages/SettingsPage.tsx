import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useSearchParams } from 'react-router-dom'
import { Alert, AutoComplete, Button, Checkbox, Form, Input, InputNumber, Select, Switch, message } from 'antd'
import { AimOutlined, ApiOutlined, BankOutlined, CheckOutlined, CloudDownloadOutlined, FilterOutlined, LockOutlined, ReloadOutlined, SafetyCertificateOutlined, SlidersOutlined } from '@ant-design/icons'
import { api } from '../api'
import type { SettingsData } from '../types'
import { ErrorBlock, LoadingBlock } from '../components/Common'
import './SettingsPage.css'

const DEFAULT_ANALYSIS_GOAL = '围绕矿山设备经销商和生产商的市场开发需求，调查项目事实，识别明确需求与有依据的潜在机会，说明参与条件、关键缺口和下一步行动，并提供可核对的来源。'
const DEFAULT_ANALYSIS_VERSION = 'market_default_v1'
const sections = [
  { key: 'platform', label: '平台目标', description: '所有市场研判共享的分析目标，由平台统一维护。', icon: <AimOutlined /> },
  { key: 'preferences', label: '客户关注偏好', description: '设置关注方向，让市场研判更贴近你的业务重点。', icon: <SlidersOutlined /> },
  { key: 'company', label: '企业资料', description: '补充真实产品与能力，帮助研判具体的企业匹配。', icon: <BankOutlined /> },
  { key: 'model', label: '模型接口', description: '管理模型服务、访问凭据与启用状态。', icon: <ApiOutlined /> },
  { key: 'exclusions', label: '明确排除', description: '仅排除确定不承接的品类或服务。', icon: <FilterOutlined /> },
  { key: 'import', label: '数据接入', description: '连接采集平台导出的资料，设置自动检查频率。', icon: <CloudDownloadOutlined /> },
] as const
type SectionKey = typeof sections[number]['key']
const businessFields: Partial<Record<SectionKey, readonly (keyof SettingsData['business'])[]>> = {
  preferences: ['customer_role', 'equipment_categories', 'mineral_types', 'demand_focus', 'region_preferences', 'target_keywords'],
  company: ['company_name', 'scope_description', 'supported_products', 'capabilities', 'solution_examples'],
  exclusions: ['exclude_keywords'],
}
const emptySettings: SettingsData = {
  business: { company_name: '', scope_description: '', customer_role: '', equipment_categories: '', mineral_types: '', demand_focus: '', target_keywords: [], exclude_keywords: [], supported_products: '', capabilities: '', solution_examples: '', region_preferences: '' },
  ai: { base_url: '', model: '', enabled: false, has_api_key: false, engine: 'pi_sdk', sdk_ready: true },
  import: { path: '', auto_import: false, poll_seconds: 300 }, version: 0,
  analysis: { goal: DEFAULT_ANALYSIS_GOAL, semantic_version: DEFAULT_ANALYSIS_VERSION, company_profile_present: false },
}
function copySettings(value: SettingsData): SettingsData {
  return { business: { ...value.business, target_keywords: [...value.business.target_keywords], exclude_keywords: [...value.business.exclude_keywords] }, ai: { ...value.ai }, import: { ...value.import }, version: value.version, analysis: value.analysis ? { ...value.analysis } : undefined }
}
function mergeSections(base: SettingsData, source: SettingsData, keys: readonly SectionKey[]): SettingsData {
  const next = copySettings(base)
  for (const section of keys) {
    for (const key of businessFields[section] ?? []) next.business = { ...next.business, [key]: source.business[key] }
    if (section === 'model') next.ai = { ...next.ai, base_url: source.ai.base_url, model: source.ai.model, enabled: source.ai.enabled }
    if (section === 'import') next.import = { ...source.import }
  }
  return next
}
function hasSubstantiveCompanyProfile(business: SettingsData['business']): boolean {
  return [business.scope_description, business.supported_products, business.capabilities, business.solution_examples].some(value => value.trim().length > 0)
}
// Drafts survive in-app navigation in memory only. Never put credentials in
// localStorage, sessionStorage, URLs, or the saved settings object.
let pendingDraft: { draft: SettingsData; sections: SectionKey[]; apiKey: string; clearApiKey: boolean; version: number } | null = null

export function SettingsPage() {
  const [params, setParams] = useSearchParams()
  const activeSection = sections.find(section => section.key === params.get('section')) ?? sections[1]
  const [settings, setSettings] = useState<SettingsData>(emptySettings)
  const [draft, setDraft] = useState<SettingsData>(emptySettings)
  const [apiKey, setApiKey] = useState('')
  const [clearApiKey, setClearApiKey] = useState(false)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<unknown>(null)
  const [restored, setRestored] = useState(false)
  const [remoteChanged, setRemoteChanged] = useState(false)
  const [feedback, setFeedback] = useState<{ section: SectionKey; error: boolean; text: string } | null>(null)
  const mounted = useRef(false)
  const loadSequence = useRef(0)
  const saveLock = useRef(false)
  const dirtySections = useMemo(() => sections.filter(({ key }) => {
    if (key === 'platform') return false
    if (key === 'model') return apiKey !== '' || clearApiKey || (['base_url', 'model', 'enabled'] as const).some(field => draft.ai[field] !== settings.ai[field])
    if (key === 'import') return JSON.stringify(draft.import) !== JSON.stringify(settings.import)
    return (businessFields[key] ?? []).some(field => JSON.stringify(draft.business[field]) !== JSON.stringify(settings.business[field]))
  }).map(section => section.key), [settings, draft, apiKey, clearApiKey])
  const dirty = dirtySections.length > 0
  const currentDirty = dirtySections.includes(activeSection.key)

  const loadSettings = useCallback(async () => {
    const sequence = ++loadSequence.current
    setLoading(true)
    setError(null)
    try {
      const fresh = normalizeSettings(await api.settings())
      if (!mounted.current || sequence !== loadSequence.current) return
      const cached = pendingDraft
      setSettings(fresh)
      setDraft(cached ? mergeSections(fresh, cached.draft, cached.sections) : copySettings(fresh))
      setApiKey(cached?.apiKey ?? '')
      setClearApiKey(cached?.clearApiKey ?? false)
      setRestored(Boolean(cached))
      setRemoteChanged(Boolean(cached && cached.version !== fresh.version))
    } catch (reason) { if (mounted.current && sequence === loadSequence.current) setError(reason) }
    finally { if (mounted.current && sequence === loadSequence.current) setLoading(false) }
  }, [])
  useEffect(() => {
    mounted.current = true
    void loadSettings()
    return () => { mounted.current = false; loadSequence.current += 1 }
  }, [loadSettings])
  useEffect(() => {
    if (loading || error) return
    pendingDraft = dirty ? { draft: copySettings(draft), sections: dirtySections, apiKey, clearApiKey, version: settings.version } : null
  }, [loading, error, dirty, draft, dirtySections, apiKey, clearApiKey, settings.version])
  useEffect(() => {
    if (!dirty) return
    const warn = (event: BeforeUnloadEvent) => { event.preventDefault(); event.returnValue = '' }
    window.addEventListener('beforeunload', warn)
    return () => window.removeEventListener('beforeunload', warn)
  }, [dirty])
  const edited = () => { setFeedback(null); setRestored(false) }
  const updateBusiness = (key: keyof SettingsData['business'], value: string | string[]) => {
    edited(); setDraft(current => ({ ...current, business: { ...current.business, [key]: value } }))
  }
  const updateAi = (key: 'base_url' | 'model' | 'enabled', value: string | boolean) => {
    edited(); setDraft(current => ({ ...current, ai: { ...current.ai, [key]: value } }))
  }
  const updateImport = (key: keyof SettingsData['import'], value: string | number | boolean) => {
    edited(); setDraft(current => ({ ...current, import: { ...current.import, [key]: value } }))
  }
  const reset = () => {
    setDraft(current => mergeSections(current, settings, [activeSection.key]))
    if (activeSection.key === 'model') { setApiKey(''); setClearApiKey(false) }
    setFeedback(null); setRestored(false)
    message.info(`已恢复${activeSection.label}的已保存值`)
  }
  const save = async () => {
    if (saveLock.current || !currentDirty) return
    saveLock.current = true; setSaving(true); setFeedback(null)
    const section = activeSection.key
    const snapshot = copySettings(draft)
    const remaining = dirtySections.filter(key => key !== section)
    try {
      // Use current server values for other categories, keeping their drafts local.
      const fresh = normalizeSettings(await api.settings())
      const next = mergeSections(fresh, snapshot, [section])
      const body: SettingsData = {
        business: next.business, import: next.import, version: fresh.version,
        ai: { base_url: next.ai.base_url, model: next.ai.model, enabled: next.ai.enabled, has_api_key: next.ai.has_api_key,
          ...(section === 'model' && apiKey && !clearApiKey ? { api_key: apiKey } : {}),
          ...(section === 'model' && clearApiKey ? { clear_api_key: true } : {}),
        },
      }
      const saved = normalizeSettings(await api.saveSettings(body))
      const nextDraft = mergeSections(saved, snapshot, remaining)
      pendingDraft = remaining.length ? { draft: nextDraft, sections: remaining, apiKey: section === 'model' ? '' : apiKey, clearApiKey: section === 'model' ? false : clearApiKey, version: saved.version } : null
      if (!mounted.current) return
      setSettings(saved); setDraft(nextDraft)
      if (section === 'model') { setApiKey(''); setClearApiKey(false) }
      setRestored(false); setRemoteChanged(false)
      setFeedback({ section, error: false, text: `${activeSection.label}已保存` })
      message.success(`${activeSection.label}已保存`)
    } catch (reason) {
      if (mounted.current) setFeedback({ section, error: true, text: reason instanceof Error ? reason.message : '保存失败，请稍后重试' })
    } finally { saveLock.current = false; if (mounted.current) setSaving(false) }
  }
  if (loading) return <div className="page-stack"><LoadingBlock text="正在读取业务配置" /></div>
  if (error) return <div className="page-stack"><ErrorBlock error={error} onRetry={() => void loadSettings()} /></div>

  const companyProfileEmpty = !hasSubstantiveCompanyProfile(draft.business)
  const modelComplete = Boolean(settings.ai.base_url.trim() && settings.ai.model.trim() && settings.ai.has_api_key)
  const sdkReady = settings.ai.sdk_ready ?? true
  const engine = settings.ai.engine ?? 'pi_sdk'
  const modelStatus = !settings.ai.enabled ? '模型已停用' : !modelComplete ? '配置待完善' : engine === 'pi_sdk' && !sdkReady ? '分析组件未就绪' : '已配置，连接未验证'
  const otherDirtyCount = dirtySections.filter(key => key !== activeSection.key).length
  const tokenValues = (values: string[]) => values.map(value => value.trim()).filter(Boolean)
  const renderContent = () => {
    switch (activeSection.key) {
      case 'platform': return <div className="settings-center-goal">
        <div className="settings-center-readonly"><LockOutlined /> 系统默认 · 只读</div><h3>矿山设备市场开发</h3><p>{settings.analysis?.goal?.trim() || DEFAULT_ANALYSIS_GOAL}</p>
        <div className="settings-center-policy"><h4>资料如何影响研判</h4><dl><div><dt>客户关注偏好</dt><dd>调整分析重点，不作为产品、资质或服务能力的证明。</dd></div><div><dt>企业资料</dt><dd>用于补充具体匹配；留空时仍可分析项目市场价值。</dd></div><div><dt>明确排除</dt><dd>仅排除确定不承接的内容，信息不明的项目保留待核实。</dd></div></dl></div>
      </div>
      case 'preferences': return <>
        <div className="settings-center-form-grid">
          <Form.Item label="客户角色" htmlFor="customer-role"><AutoComplete id="customer-role" value={draft.business.customer_role} onChange={value => updateBusiness('customer_role', value)} options={['经销商', '生产商'].map(value => ({ value }))} placeholder="选择或输入关注角色" /></Form.Item>
          <Form.Item label="设备类别" htmlFor="equipment-categories"><Input.TextArea id="equipment-categories" value={draft.business.equipment_categories} onChange={event => updateBusiness('equipment_categories', event.target.value)} autoSize={{ minRows: 1, maxRows: 3 }} placeholder="例如：破碎、输送、提升、智能化" /></Form.Item>
          <Form.Item label="矿种 / 应用场景" htmlFor="mineral-types"><Input.TextArea id="mineral-types" value={draft.business.mineral_types} onChange={event => updateBusiness('mineral_types', event.target.value)} autoSize={{ minRows: 1, maxRows: 3 }} placeholder="例如：煤矿、金属矿、砂石骨料" /></Form.Item>
          <Form.Item label="需求方向" htmlFor="demand-focus"><Input.TextArea id="demand-focus" value={draft.business.demand_focus} onChange={event => updateBusiness('demand_focus', event.target.value)} autoSize={{ minRows: 1, maxRows: 3 }} placeholder="例如：新建、技改、备件或服务" /></Form.Item>
        </div>
        <Form.Item label="关注地区" htmlFor="business-regions" extra="仅调整关注重点，不代表已具备当地服务能力。"><Input.TextArea id="business-regions" value={draft.business.region_preferences} onChange={event => updateBusiness('region_preferences', event.target.value)} autoSize={{ minRows: 1, maxRows: 3 }} placeholder="填写希望优先关注的地区及需要确认的服务条件" /></Form.Item>
        <Form.Item label="关注关键词" htmlFor="customer-target-keywords" extra="输入后按回车添加，也可用逗号分隔。"><Select id="customer-target-keywords" mode="tags" value={draft.business.target_keywords} onChange={values => updateBusiness('target_keywords', tokenValues(values))} tokenSeparators={[',', '，', '\n']} open={false} suffixIcon={null} placeholder="输入设备、项目或业务关键词" /></Form.Item>
        <p className="settings-center-note">关注偏好仅用于调整分析重点，不代表企业具备对应产品、资质或现场服务能力。</p>
      </>
      case 'company': return <>
        <Form.Item label="公司名称" htmlFor="business-company-name"><Input id="business-company-name" value={draft.business.company_name} onChange={event => updateBusiness('company_name', event.target.value)} placeholder="填写公司真实名称" /></Form.Item>
        <Form.Item label="业务范围" htmlFor="business-scope" extra="可选"><Input.TextArea id="business-scope" value={draft.business.scope_description} onChange={event => updateBusiness('scope_description', event.target.value)} autoSize={{ minRows: 2, maxRows: 6 }} placeholder="主营业务，以及能够承接的项目范围" /></Form.Item>
        <div className="settings-center-form-grid">
          <Form.Item label="支持的产品 / 设备" htmlFor="business-products" extra="可选 · 未确认的产品或适配关系请注明。"><Input.TextArea id="business-products" value={draft.business.supported_products} onChange={event => updateBusiness('supported_products', event.target.value)} autoSize={{ minRows: 3, maxRows: 8 }} placeholder="设备、型号、备件或服务" /></Form.Item>
          <Form.Item label="明确能力" htmlFor="business-capabilities" extra="可选 · 说明已确认的能力与限制。"><Input.TextArea id="business-capabilities" value={draft.business.capabilities} onChange={event => updateBusiness('capabilities', event.target.value)} autoSize={{ minRows: 3, maxRows: 8 }} placeholder="供货、适配、改造和交付能力" /></Form.Item>
        </div>
        <Form.Item label="方案例子" htmlFor="business-solutions" extra="可选 · 仅填写真实做法与适用条件。"><Input.TextArea id="business-solutions" value={draft.business.solution_examples} onChange={event => updateBusiness('solution_examples', event.target.value)} autoSize={{ minRows: 3, maxRows: 10 }} placeholder="做过什么，适用什么情况，有哪些取舍" /></Form.Item>
        <p className="settings-center-note">{companyProfileEmpty ? '企业资料可留空，市场分析仍可使用。' : '已提供企业资料，用于辅助具体匹配。'} 填写资料不等于资质已核验。</p>
      </>
      case 'model': return <>
        <div className="settings-center-toggle-row"><div><label htmlFor="ai-enabled">启用模型</label><p>保存后，符合条件的项目开始分析。</p></div><Switch id="ai-enabled" checked={draft.ai.enabled} onChange={value => updateAi('enabled', value)} /></div>
        <div className="settings-center-connection" role="status"><div><span className={`settings-center-status-dot ${settings.ai.enabled && !modelComplete ? 'is-warning' : ''}`} /><strong>{modelStatus}</strong></div><Link to="/operations">查看处理状态</Link><p>状态基于已保存配置；现有接口不提供独立连通性检测，调用结果请在处理状态中查看。</p></div>
        <Form.Item label="兼容 OpenAI 的 Base URL" htmlFor="ai-base-url" extra="填写服务商提供的接口地址，通常以 /v1 结尾。"><Input id="ai-base-url" value={draft.ai.base_url} onChange={event => updateAi('base_url', event.target.value)} placeholder="https://api.example.com/v1" autoComplete="off" spellCheck={false} /></Form.Item>
        <Form.Item label="模型名称" htmlFor="ai-model" extra="需支持工具调用。"><Input id="ai-model" value={draft.ai.model} onChange={event => updateAi('model', event.target.value)} placeholder="填写服务商的实际模型名称" autoComplete="off" spellCheck={false} /></Form.Item>
        <Form.Item label="API Key" htmlFor="ai-api-key" extra={clearApiKey ? '保存后清除本地保存的密钥；运行环境提供的密钥不受影响。' : '留空保留已有密钥，填写新密钥可替换。'}><Input.Password id="ai-api-key" prefix={<LockOutlined />} visibilityToggle={false} disabled={clearApiKey || saving} value={apiKey} onChange={event => { edited(); setApiKey(event.target.value) }} placeholder={settings.ai.has_api_key ? '已配置密钥，不显示原值' : '粘贴接口密钥'} autoComplete="new-password" spellCheck={false} /></Form.Item>
        <div className="settings-center-key-row"><span><SafetyCertificateOutlined />{settings.ai.has_api_key ? '已配置密钥' : '尚未配置密钥'}</span><Checkbox checked={clearApiKey} onChange={event => { edited(); setClearApiKey(event.target.checked) }}>清除已保存密钥</Checkbox></div>
        <div className="settings-center-runtime"><span>分析引擎</span><strong>{engine === 'pi_sdk' ? 'Pi Agent SDK' : '单次调用'}</strong><span>{engine === 'pi_sdk' ? sdkReady ? '组件已就绪' : '组件未就绪' : '当前运行模式'}</span></div>
        {engine === 'pi_sdk' && !sdkReady && <Alert type="warning" showIcon message="分析组件未就绪，请运行安装依赖脚本。" />}
      </>
      case 'exclusions': return <>
        <Form.Item label="明确排除关键词" htmlFor="rule-exclude-keywords" extra="输入确定不承接的品类或服务，按回车添加。"><Select id="rule-exclude-keywords" mode="tags" value={draft.business.exclude_keywords} onChange={values => updateBusiness('exclude_keywords', tokenValues(values))} tokenSeparators={[',', '，', '\n']} open={false} suffixIcon={null} placeholder="输入明确排除的业务关键词" /></Form.Item>
        <p className="settings-center-note">设备字段为空、资质不明或疑似重复的项目，仍会保留待核实。</p>
      </>
      case 'import': return <>
        <Form.Item label="records.jsonl 路径" htmlFor="import-path" extra="填写采集平台导出文件的完整路径。"><Input id="import-path" value={draft.import.path} onChange={event => updateImport('path', event.target.value)} placeholder="填写完整的采集导出文件路径" spellCheck={false} /></Form.Item>
        <div className="settings-center-toggle-row"><div><label htmlFor="import-auto">自动检查</label><p>按设定频率检查采集文件并更新项目。</p></div><Switch id="import-auto" checked={draft.import.auto_import} onChange={value => updateImport('auto_import', value)} /></div>
        <Form.Item className="settings-center-poll" label="检查间隔" htmlFor="import-poll-seconds" extra="最短 30 秒。"><InputNumber id="import-poll-seconds" min={30} step={30} addonAfter="秒" value={draft.import.poll_seconds} onChange={value => updateImport('poll_seconds', value ?? 300)} /></Form.Item>
        <p className="settings-center-note">也可前往<Link to="/operations">处理状态</Link>手动导入。</p>
      </>
    }
  }
  return <main className="settings-center">
    <header className="settings-center-page-heading"><div><h1>业务配置</h1><p>管理分析偏好、企业资料与平台连接。</p></div><div className="settings-center-overview"><span className={dirty ? 'has-changes' : ''}>{dirty ? `${dirtySections.length} 类配置待保存` : `已保存 · 版本 ${settings.version}`}</span><Button type="text" aria-label="重新读取配置" title={dirty ? '请先保存或重置修改' : '重新读取配置'} icon={<ReloadOutlined />} disabled={dirty || saving} onClick={() => { setFeedback(null); void loadSettings() }} /></div></header>
    <div className="settings-center-layout">
      <aside className="settings-center-sidebar"><nav aria-label="配置类别">{sections.map(section => <button type="button" key={section.key} aria-current={section.key === activeSection.key ? 'page' : undefined} disabled={saving} onClick={() => { setParams(current => { const next = new URLSearchParams(current); next.set('section', section.key); return next }, { preventScrollReset: true }) }}><span className="settings-center-nav-icon" aria-hidden="true">{section.icon}</span><span>{section.label}</span>{dirtySections.includes(section.key) && <span className="settings-center-nav-dot" role="img" aria-label="有未保存修改" />}</button>)}</nav><p>切换分类会保留未保存的修改</p></aside>
      <section className="settings-center-panel" aria-labelledby="settings-section-title" aria-busy={saving}>
        <header className="settings-center-panel-heading"><div><h2 id="settings-section-title">{activeSection.label}</h2><p>{activeSection.description}</p></div><span className={`settings-center-section-state${currentDirty ? ' has-changes' : ''}`}>{activeSection.key === 'platform' ? <><LockOutlined /> 只读</> : currentDirty ? '未保存' : <><CheckOutlined /> 已保存</>}</span></header>
        <div className="settings-center-panel-body">
          {restored && dirty && <div className="settings-center-restored" role="status">已恢复未保存的修改。</div>}
          {remoteChanged && dirty && <Alert className="settings-center-remote-notice" type="warning" showIcon message="设置已有更新，草稿已保留，请核对后再保存。" />}
          <Form layout="vertical" requiredMark={false} disabled={saving} component="div" className="settings-center-form">{renderContent()}</Form>
          {feedback?.section === activeSection.key && feedback.error && <Alert className="settings-center-save-error" type="error" showIcon role="alert" message={feedback.text} description="修改已保留，可重试保存。" />}
        </div>
        <footer className="settings-center-panel-footer"><div className="settings-center-save-status" role="status">{activeSection.key === 'platform' ? '平台目标为系统默认值' : saving ? '正在保存…' : feedback?.section === activeSection.key && !feedback.error ? <span className="is-saved"><CheckOutlined />{feedback.text}</span> : currentDirty ? '仅保存当前分类的修改' : '当前分类没有未保存的修改'}{otherDirtyCount > 0 && <small>其他 {otherDirtyCount} 类配置尚未保存</small>}</div>{activeSection.key !== 'platform' && <div className="settings-center-footer-actions"><Button aria-label="重置当前配置" disabled={!currentDirty || saving} onClick={reset}>重置</Button><Button type="primary" aria-label="保存当前配置" disabled={!currentDirty} loading={saving} onClick={() => void save()}>保存更改</Button></div>}</footer>
      </section>
    </div>
  </main>
}

function normalizeSettings(value: SettingsData): SettingsData {
  const business = value.business ?? emptySettings.business
  const normalizedBusiness = {
    company_name: business.company_name ?? '', scope_description: business.scope_description ?? '', customer_role: business.customer_role ?? '', equipment_categories: business.equipment_categories ?? '', mineral_types: business.mineral_types ?? '', demand_focus: business.demand_focus ?? '', target_keywords: Array.isArray(business.target_keywords) ? business.target_keywords : [], exclude_keywords: Array.isArray(business.exclude_keywords) ? business.exclude_keywords : [], supported_products: typeof business.supported_products === 'string' ? business.supported_products : JSON.stringify(business.supported_products ?? ''), capabilities: typeof business.capabilities === 'string' ? business.capabilities : JSON.stringify(business.capabilities ?? ''), solution_examples: typeof business.solution_examples === 'string' ? business.solution_examples : JSON.stringify(business.solution_examples ?? ''), region_preferences: typeof business.region_preferences === 'string' ? business.region_preferences : JSON.stringify(business.region_preferences ?? ''),
  }
  return {
    version: value.version ?? 0, business: normalizedBusiness,
    ai: { base_url: value.ai?.base_url ?? '', model: value.ai?.model ?? '', enabled: Boolean(value.ai?.enabled), has_api_key: Boolean(value.ai?.has_api_key), engine: value.ai?.engine === 'single_call' ? 'single_call' : 'pi_sdk', sdk_ready: value.ai?.sdk_ready ?? true },
    import: { path: value.import?.path ?? '', auto_import: Boolean(value.import?.auto_import), poll_seconds: Number(value.import?.poll_seconds ?? 300) },
    analysis: { goal: typeof value.analysis?.goal === 'string' && value.analysis.goal.trim() ? value.analysis.goal : DEFAULT_ANALYSIS_GOAL, semantic_version: typeof value.analysis?.semantic_version === 'string' && value.analysis.semantic_version.trim() ? value.analysis.semantic_version : DEFAULT_ANALYSIS_VERSION, company_profile_present: typeof value.analysis?.company_profile_present === 'boolean' ? value.analysis.company_profile_present : hasSubstantiveCompanyProfile(normalizedBusiness) },
  }
}
