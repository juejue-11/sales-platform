import { useCallback, useEffect, useState } from 'react'
import { Alert, Button, Card, Col, Form, Input, InputNumber, Row, Space, Switch, Tag, Typography, message } from 'antd'
import { LockOutlined, ReloadOutlined, SaveOutlined, SafetyCertificateOutlined } from '@ant-design/icons'
import { api } from '../api'
import type { SettingsData } from '../types'
import { ErrorBlock, LoadingBlock } from '../components/Common'

const { Title, Text, Paragraph } = Typography

const DEFAULT_ANALYSIS_GOAL = '围绕矿山设备经销商和生产商的市场开发需求，调查项目事实，识别明确需求与有依据的潜在机会，说明参与条件、关键缺口和下一步行动，并提供可核对的来源。'
const DEFAULT_ANALYSIS_VERSION = 'market_default_v1'

const emptySettings: SettingsData = {
  business: { company_name: '', scope_description: '', customer_role: '', equipment_categories: '', mineral_types: '', demand_focus: '', target_keywords: [], exclude_keywords: [], supported_products: '', capabilities: '', solution_examples: '', region_preferences: '' },
  ai: { base_url: '', model: '', enabled: false, has_api_key: false, engine: 'pi_sdk', sdk_ready: true },
  import: { path: '', auto_import: false, poll_seconds: 300 },
  version: 0,
  analysis: { goal: DEFAULT_ANALYSIS_GOAL, semantic_version: DEFAULT_ANALYSIS_VERSION, company_profile_present: false },
}

function copySettings(value: SettingsData): SettingsData {
  return {
    business: { ...value.business, target_keywords: [...value.business.target_keywords], exclude_keywords: [...value.business.exclude_keywords] },
    ai: { ...value.ai },
    import: { ...value.import },
    version: value.version,
    analysis: value.analysis ? { ...value.analysis } : undefined,
  }
}

function toLines(value: string[]): string {
  return value.join('\n')
}

function fromLines(value: string): string[] {
  return value.split(/[\n,，]/).map((item) => item.trim()).filter(Boolean)
}

function hasSubstantiveCompanyProfile(business: SettingsData['business']): boolean {
  return [business.scope_description, business.supported_products, business.capabilities, business.solution_examples]
    .some((value) => value.trim().length > 0)
}

export function SettingsPage() {
  const [settings, setSettings] = useState<SettingsData>(emptySettings)
  const [draft, setDraft] = useState<SettingsData>(emptySettings)
  const [keywordText, setKeywordText] = useState({ target: '', exclude: '' })
  const [apiKey, setApiKey] = useState('')
  const [clearApiKey, setClearApiKey] = useState(false)
  const [dirty, setDirty] = useState(false)
  const [loading, setLoading] = useState(true)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<unknown>(null)

  const loadSettings = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const result = await api.settings()
      const normalized = normalizeSettings(result)
      setSettings(normalized)
      setDraft(copySettings(normalized))
      setKeywordText({ target: toLines(normalized.business.target_keywords), exclude: toLines(normalized.business.exclude_keywords) })
      setApiKey('')
      setClearApiKey(false)
      setDirty(false)
    } catch (loadError) {
      setError(loadError)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { void loadSettings() }, [loadSettings])

  const updateBusiness = (key: keyof SettingsData['business'], value: string | string[]) => {
    setDirty(true)
    setDraft((current) => ({ ...current, business: { ...current.business, [key]: value } }))
  }

  const updateAi = (key: 'base_url' | 'model' | 'enabled', value: string | boolean) => {
    setDirty(true)
    setDraft((current) => ({ ...current, ai: { ...current.ai, [key]: value } }))
  }

  const updateImport = (key: keyof SettingsData['import'], value: string | number | boolean) => {
    setDirty(true)
    setDraft((current) => ({ ...current, import: { ...current.import, [key]: value } }))
  }

  const save = async () => {
    setSaving(true)
    try {
      const body: SettingsData = {
        business: { ...draft.business, target_keywords: fromLines(keywordText.target), exclude_keywords: fromLines(keywordText.exclude) },
        import: { ...draft.import },
        version: draft.version,
        // engine and sdk_ready describe the server runtime; keep them out of save requests.
        ai: { base_url: draft.ai.base_url, model: draft.ai.model, enabled: draft.ai.enabled, has_api_key: draft.ai.has_api_key, ...(apiKey ? { api_key: apiKey } : {}), ...(clearApiKey ? { clear_api_key: true } : {}) },
      }
      const saved = normalizeSettings(await api.saveSettings(body))
      const fresh = normalizeSettings(await api.settings())
      setSettings(fresh.version >= saved.version ? fresh : saved)
      setDraft(copySettings(fresh.version >= saved.version ? fresh : saved))
      const latest = fresh.version >= saved.version ? fresh : saved
      setKeywordText({ target: toLines(latest.business.target_keywords), exclude: toLines(latest.business.exclude_keywords) })
      setApiKey('')
      setClearApiKey(false)
      setDirty(false)
      message.success('业务配置已保存，相关旧判断会按后端规则标记待复核')
    } catch (saveError) {
      message.error(saveError instanceof Error ? saveError.message : '保存失败，请稍后重试')
    } finally {
      setSaving(false)
    }
  }

  if (loading) return <div className="page-stack"><LoadingBlock text="正在读取业务配置" /></div>
  if (error) return <div className="page-stack"><ErrorBlock error={error} onRetry={() => void loadSettings()} /></div>

  const companyProfileEmpty = !hasSubstantiveCompanyProfile(draft.business)
  const analysisGoal = draft.analysis?.goal?.trim() || DEFAULT_ANALYSIS_GOAL
  const modelComplete = draft.ai.enabled && draft.ai.base_url.trim() && draft.ai.model.trim() && !clearApiKey && (settings.ai.has_api_key || apiKey.trim())
  const savedModelReady = Boolean(settings.ai.enabled && settings.ai.has_api_key && settings.ai.base_url.trim() && settings.ai.model.trim())
  const aiEngine = settings.ai.engine ?? 'pi_sdk'
  const sdkReady = settings.ai.sdk_ready ?? true
  return (
    <div className="page-stack settings-page">
      <div className="page-heading">
        <div><Title level={2}>业务配置</Title><Paragraph>市场判断有默认目标；企业资料用于补充具体匹配，可按需要提供。</Paragraph></div>
        <Space wrap><Text type={dirty ? 'warning' : 'secondary'}>{dirty ? '有未保存修改' : `已保存版本 ${settings.version}`}</Text><Button icon={<ReloadOutlined />} onClick={() => void loadSettings()} disabled={dirty}>重新读取</Button><Button type="primary" icon={<SaveOutlined />} loading={saving} disabled={!dirty} onClick={() => void save()}>保存配置</Button></Space>
      </div>
      <Card id="platform-goal" title="平台分析目标（系统默认）" className="settings-panel settings-platform-goal">
        <Alert type="info" showIcon message="默认分析目标：矿山设备市场开发" description={<><span>{analysisGoal}</span><Text type="secondary">这是平台共同目标，只读；企业资料为空时仍可直接分析项目市场价值。</Text></>} />
      </Card>
      <nav className="settings-section-nav" aria-label="配置分区"><a href="#platform-goal">平台目标</a><a href="#customer-preferences">客户关注偏好</a><a href="#company-card">企业资料（可选）</a><a href="#model-config">模型接口</a><a href="#rule-config">明确排除</a><a href="#import-config">数据接入</a></nav>
      <div className="settings-grid">
        <div className="settings-main">
          <Card id="customer-preferences" title="客户关注偏好" className="settings-panel" extra={<Text type="secondary">用于调整分析重点</Text>}>
            <Paragraph className="settings-intro">填写希望优先关注的角色、设备、矿种、需求方向、地区和关键词。它们是分析偏好，不代表企业已有产品、资质或现场服务能力。</Paragraph>
            <Form layout="vertical">
              <Row gutter={18}>
                <Col xs={24} md={12}><Form.Item label={<label htmlFor="customer-role">客户角色</label>}><Input id="customer-role" value={draft.business.customer_role} onChange={(event) => updateBusiness('customer_role', event.target.value)} placeholder="经销商、生产商或其他关注角色" /></Form.Item></Col>
                <Col xs={24} md={12}><Form.Item label={<label htmlFor="equipment-categories">设备类别</label>}><Input.TextArea id="equipment-categories" value={draft.business.equipment_categories} onChange={(event) => updateBusiness('equipment_categories', event.target.value)} rows={2} placeholder="例如：破碎、输送、提升、智能化" /></Form.Item></Col>
              </Row>
              <Row gutter={18}>
                <Col xs={24} md={12}><Form.Item label={<label htmlFor="mineral-types">矿种 / 应用场景</label>}><Input.TextArea id="mineral-types" value={draft.business.mineral_types} onChange={(event) => updateBusiness('mineral_types', event.target.value)} rows={2} placeholder="例如：煤矿、金属矿、砂石骨料" /></Form.Item></Col>
                <Col xs={24} md={12}><Form.Item label={<label htmlFor="demand-focus">需求方向</label>}><Input.TextArea id="demand-focus" value={draft.business.demand_focus} onChange={(event) => updateBusiness('demand_focus', event.target.value)} rows={2} placeholder="新建、技改、备件或服务等关注方向" /></Form.Item></Col>
              </Row>
              <Row gutter={18}>
                <Col xs={24} md={12}><Form.Item label={<label htmlFor="business-regions">关注地区</label>} extra="只用于调整市场分析关注重点，不代表已覆盖该地区或具备现场服务能力"><Input.TextArea id="business-regions" value={draft.business.region_preferences} onChange={(event) => updateBusiness('region_preferences', event.target.value)} rows={2} placeholder="希望优先关注的地区，以及需要先确认的现场服务条件" /></Form.Item></Col>
                <Col xs={24} md={12}><Form.Item label={<label htmlFor="customer-target-keywords">关注关键词</label>} extra="每行一个，或用逗号分隔；只调整关注重点，不代表企业具备对应产品"><Input.TextArea id="customer-target-keywords" value={keywordText.target} onChange={(event) => { setKeywordText(current => ({ ...current, target: event.target.value })); setDirty(true) }} rows={2} placeholder={'破碎机\n智能矿山\n备件'} /></Form.Item></Col>
              </Row>
            </Form>
          </Card>
          <Card id="company-card" title="企业资料（可选）" className="settings-panel" extra={<Tag color={companyProfileEmpty ? 'default' : 'success'}>{companyProfileEmpty ? '未提供，可继续分析' : '已提供资料'}</Tag>}>
            <Paragraph className="settings-intro">企业资料用于补充具体匹配，不是开始市场分析的前置条件。只写已经确认的内容；暂不确定的产品、适配关系和交期，可以直接注明需要核实。</Paragraph>
            <Form layout="vertical">
              <Form.Item label={<label htmlFor="business-company-name">公司名称</label>}><Input id="business-company-name" value={draft.business.company_name} onChange={(event) => updateBusiness('company_name', event.target.value)} placeholder="填写公司真实名称" /></Form.Item>
              <Form.Item label={<label htmlFor="business-scope">业务范围（可选）</label>}><Input.TextArea id="business-scope" value={draft.business.scope_description} onChange={(event) => updateBusiness('scope_description', event.target.value)} rows={3} placeholder="主营业务，以及能够承接的项目范围" /></Form.Item>
              <Row gutter={18}>
                <Col xs={24} md={12}><Form.Item label={<label htmlFor="business-products">支持的产品 / 设备（可选）</label>}><Input.TextArea id="business-products" value={draft.business.supported_products} onChange={(event) => updateBusiness('supported_products', event.target.value)} rows={4} placeholder="设备、型号、备件或服务；未确认的请注明" /></Form.Item></Col>
                <Col xs={24} md={12}><Form.Item label={<label htmlFor="business-capabilities">明确能力（可选）</label>}><Input.TextArea id="business-capabilities" value={draft.business.capabilities} onChange={(event) => updateBusiness('capabilities', event.target.value)} rows={4} placeholder="供货、适配、改造和交付的能力与限制" /></Form.Item></Col>
              </Row>
              <Form.Item label={<label htmlFor="business-solutions">方案例子（可选）</label>} extra="已有的真实做法，有几份就先填几份"><Input.TextArea id="business-solutions" value={draft.business.solution_examples} onChange={(event) => updateBusiness('solution_examples', event.target.value)} rows={4} placeholder="做过什么，适用什么情况，有哪些取舍" /></Form.Item>
            </Form>
            <div className="settings-help"><p>{companyProfileEmpty ? '尚未提供企业资料也可以直接分析。补充产品、能力或真实方案例子后，页面会在资料能够支持的范围内显示企业匹配；填写资料不等于资质已核验。' : '企业资料变更后，相关 AI 判断会提示复核；资料只用于辅助匹配，不会把名称、偏好或自填内容当成已核验资格。'}</p></div>
          </Card>
          <Card id="rule-config" title="明确排除规则" className="settings-panel" extra={<Text type="secondary">只影响明确排除</Text>}>
            <Form layout="vertical"><Form.Item label={<label htmlFor="rule-exclude-keywords">明确排除关键词</label>} extra="只填写确定不承接的品类或服务"><Input.TextArea id="rule-exclude-keywords" value={keywordText.exclude} onChange={(event) => { setKeywordText(current => ({ ...current, exclude: event.target.value })); setDirty(true) }} rows={4} placeholder="完全不承接的业务" /></Form.Item></Form>
            <Paragraph className="settings-bottom-note">这里只填写明确不承接的内容。设备字段为空、资质不明和疑似重复，都会先保留待核实。</Paragraph>
          </Card>
        </div>
        <div className="settings-side">
          <Card id="model-config" title="模型接口" className="settings-panel model-panel" extra={<Tag color={savedModelReady ? 'success' : 'default'}>{savedModelReady ? '已配置' : '待配置'}</Tag>}>
            <div className="settings-switch-row"><div><label htmlFor="ai-enabled">启用模型</label><p>保存后，符合条件的项目开始分析</p></div><Switch id="ai-enabled" checked={draft.ai.enabled} onChange={checked => updateAi('enabled', checked)} /></div>
            <Space size={8} wrap><Text strong>Pi Agent SDK</Text><Tag color={aiEngine === 'pi_sdk' ? (sdkReady ? 'success' : 'warning') : 'default'}>{aiEngine === 'pi_sdk' ? '当前分析引擎' : '未选用'}</Tag></Space>
            <Paragraph className="settings-bottom-note">默认使用 Pi Agent SDK，按需读取项目原文、关联资料与可选企业资料，再生成市场判断和推进方向。</Paragraph>
            {aiEngine === 'pi_sdk' && !sdkReady && <Alert className="settings-alert" type="warning" showIcon message="分析组件未就绪，请运行安装依赖脚本" />}
            <Form layout="vertical">
              <Form.Item label={<label htmlFor="ai-base-url">兼容 OpenAI 的 Base URL</label>} extra="填写服务商提供的接口地址，通常以 /v1 结尾"><Input id="ai-base-url" value={draft.ai.base_url} onChange={event => updateAi('base_url', event.target.value)} placeholder="https://api.example.com/v1" /></Form.Item>
              <Form.Item label={<label htmlFor="ai-model">模型名称</label>} extra="模型需支持工具调用"><Input id="ai-model" value={draft.ai.model} onChange={event => updateAi('model', event.target.value)} placeholder="填写服务商的实际模型名称" /></Form.Item>
              <Form.Item label={<label htmlFor="ai-api-key">API Key</label>} extra="留空保留已有密钥，输入新密钥可替换"><Input.Password id="ai-api-key" prefix={<LockOutlined />} value={apiKey} onChange={event => { setApiKey(event.target.value); setDirty(true) }} placeholder={settings.ai.has_api_key ? '已保存，不显示原密钥' : '粘贴接口密钥'} /></Form.Item>
              <div className="settings-key-state"><Text type="secondary"><SafetyCertificateOutlined /> {settings.ai.has_api_key ? '已保存密钥' : '尚未保存密钥'}</Text><label className="clear-key-label"><input type="checkbox" checked={clearApiKey} onChange={event => { setClearApiKey(event.target.checked); setDirty(true) }} /> 清除已有密钥</label></div>
            </Form>
            <Alert className="settings-alert" type={modelComplete ? 'success' : 'info'} showIcon message={modelComplete ? '接口信息已填写，保存后启用' : '填好地址、模型与密钥后启用'} />
          </Card>
          <Card id="import-config" title="数据接入" className="settings-panel">
            <Paragraph className="settings-intro">读取信息采集平台导出的资料，项目变化后自动更新。</Paragraph>
            <Form layout="vertical">
              <Form.Item label={<label htmlFor="import-path">records.jsonl 路径</label>}><Input id="import-path" value={draft.import.path} onChange={event => updateImport('path', event.target.value)} placeholder="填写完整的采集导出文件路径" /></Form.Item>
              <Row gutter={16}><Col xs={12}><Form.Item label={<label htmlFor="import-auto">自动检查</label>}><Switch id="import-auto" checked={draft.import.auto_import} onChange={checked => updateImport('auto_import', checked)} /></Form.Item></Col><Col xs={12}><Form.Item label={<label htmlFor="import-poll-seconds">检查间隔（秒）</label>}><InputNumber id="import-poll-seconds" min={30} step={30} value={draft.import.poll_seconds} onChange={value => updateImport('poll_seconds', value ?? 300)} style={{ width: '100%' }} /></Form.Item></Col></Row>
            </Form>
            <Paragraph className="settings-bottom-note">也可以在“处理状态”手动导入。</Paragraph>
          </Card>
        </div>
      </div>
    </div>
  )
}

function normalizeSettings(value: SettingsData): SettingsData {
  const business = value.business ?? emptySettings.business
  const normalizedBusiness = {
    company_name: business.company_name ?? '', scope_description: business.scope_description ?? '', customer_role: business.customer_role ?? '', equipment_categories: business.equipment_categories ?? '', mineral_types: business.mineral_types ?? '', demand_focus: business.demand_focus ?? '', target_keywords: Array.isArray(business.target_keywords) ? business.target_keywords : [], exclude_keywords: Array.isArray(business.exclude_keywords) ? business.exclude_keywords : [], supported_products: typeof business.supported_products === 'string' ? business.supported_products : JSON.stringify(business.supported_products ?? ''), capabilities: typeof business.capabilities === 'string' ? business.capabilities : JSON.stringify(business.capabilities ?? ''), solution_examples: typeof business.solution_examples === 'string' ? business.solution_examples : JSON.stringify(business.solution_examples ?? ''), region_preferences: typeof business.region_preferences === 'string' ? business.region_preferences : JSON.stringify(business.region_preferences ?? ''),
  }
  return {
    version: value.version ?? 0,
    business: normalizedBusiness,
    ai: { base_url: value.ai?.base_url ?? '', model: value.ai?.model ?? '', enabled: Boolean(value.ai?.enabled), has_api_key: Boolean(value.ai?.has_api_key), engine: value.ai?.engine === 'single_call' ? 'single_call' : 'pi_sdk', sdk_ready: value.ai?.sdk_ready ?? true },
    import: { path: value.import?.path ?? '', auto_import: Boolean(value.import?.auto_import), poll_seconds: Number(value.import?.poll_seconds ?? 300) },
    analysis: {
      goal: typeof value.analysis?.goal === 'string' && value.analysis.goal.trim() ? value.analysis.goal : DEFAULT_ANALYSIS_GOAL,
      semantic_version: typeof value.analysis?.semantic_version === 'string' && value.analysis.semantic_version.trim() ? value.analysis.semantic_version : DEFAULT_ANALYSIS_VERSION,
      company_profile_present: typeof value.analysis?.company_profile_present === 'boolean' ? value.analysis.company_profile_present : hasSubstantiveCompanyProfile(normalizedBusiness),
    },
  }
}
