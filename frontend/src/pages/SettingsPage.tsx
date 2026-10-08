import { useCallback, useEffect, useState } from 'react'
import { Alert, Button, Card, Col, Form, Input, InputNumber, Row, Space, Switch, Tag, Typography, message } from 'antd'
import { LockOutlined, ReloadOutlined, SaveOutlined, SafetyCertificateOutlined } from '@ant-design/icons'
import { api } from '../api'
import type { SettingsData } from '../types'
import { ErrorBlock, LoadingBlock } from '../components/Common'

const { Title, Text, Paragraph } = Typography

const emptySettings: SettingsData = {
  business: { company_name: '', scope_description: '', target_keywords: [], exclude_keywords: [], supported_products: '', capabilities: '', solution_examples: '', region_preferences: '' },
  ai: { base_url: '', model: '', enabled: false, has_api_key: false },
  import: { path: '', auto_import: false, poll_seconds: 300 },
  version: 0,
}

function copySettings(value: SettingsData): SettingsData {
  return { business: { ...value.business, target_keywords: [...value.business.target_keywords], exclude_keywords: [...value.business.exclude_keywords] }, ai: { ...value.ai }, import: { ...value.import }, version: value.version }
}

function toLines(value: string[]): string {
  return value.join('\n')
}

function fromLines(value: string): string[] {
  return value.split(/[\n,，]/).map((item) => item.trim()).filter(Boolean)
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
      const body: SettingsData = copySettings({ ...draft, business: { ...draft.business, target_keywords: fromLines(keywordText.target), exclude_keywords: fromLines(keywordText.exclude) }, ai: { ...draft.ai, ...(apiKey ? { api_key: apiKey } : {}), ...(clearApiKey ? { clear_api_key: true } : {}) } })
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

  const capabilityEmpty = !draft.business.scope_description.trim() && !draft.business.supported_products.trim() && !draft.business.capabilities.trim()
  const modelComplete = draft.ai.enabled && draft.ai.base_url.trim() && draft.ai.model.trim() && !clearApiKey && (settings.ai.has_api_key || apiKey.trim())
  const savedModelReady = Boolean(settings.ai.enabled && settings.ai.has_api_key && settings.ai.base_url.trim() && settings.ai.model.trim())
  return (
    <div className="page-stack settings-page">
      <div className="page-heading">
        <div><Title level={2}>业务配置</Title><Paragraph>写清我们能做什么，让判断和方案有依据。</Paragraph></div>
        <Space wrap><Text type={dirty ? 'warning' : 'secondary'}>{dirty ? '有未保存修改' : `已保存版本 ${settings.version}`}</Text><Button icon={<ReloadOutlined />} onClick={() => void loadSettings()} disabled={dirty}>重新读取</Button><Button type="primary" icon={<SaveOutlined />} loading={saving} disabled={!dirty} onClick={() => void save()}>保存配置</Button></Space>
      </div>
      <nav className="settings-section-nav" aria-label="配置分区"><a href="#company-card">公司能力</a><a href="#model-config">模型接口</a><a href="#rule-config">筛选门槛</a><a href="#import-config">数据接入</a></nav>
      <div className="settings-grid">
        <div className="settings-main">
          <Card id="company-card" title="公司能力卡" className="settings-panel" extra={<Tag color={capabilityEmpty ? 'warning' : 'success'}>{capabilityEmpty ? '待填写' : '已填写'}</Tag>}>
            <Paragraph className="settings-intro">只写已经确认的能力。暂不确定的产品、适配关系和交期，可以直接注明需要核实。</Paragraph>
            <Form layout="vertical">
              <Form.Item label={<label htmlFor="business-company-name">公司名称</label>}><Input id="business-company-name" value={draft.business.company_name} onChange={(event) => updateBusiness('company_name', event.target.value)} placeholder="填写公司真实名称" /></Form.Item>
              <Form.Item label={<label htmlFor="business-scope">业务范围</label>}><Input.TextArea id="business-scope" value={draft.business.scope_description} onChange={(event) => updateBusiness('scope_description', event.target.value)} rows={3} placeholder="主营业务，以及能够承接的项目范围" /></Form.Item>
              <Row gutter={18}>
                <Col xs={24} md={12}><Form.Item label={<label htmlFor="business-products">支持的产品 / 设备</label>}><Input.TextArea id="business-products" value={draft.business.supported_products} onChange={(event) => updateBusiness('supported_products', event.target.value)} rows={4} placeholder="设备、型号、备件或服务；未确认的请注明" /></Form.Item></Col>
                <Col xs={24} md={12}><Form.Item label={<label htmlFor="business-capabilities">明确能力</label>}><Input.TextArea id="business-capabilities" value={draft.business.capabilities} onChange={(event) => updateBusiness('capabilities', event.target.value)} rows={4} placeholder="供货、适配、改造和交付的能力与限制" /></Form.Item></Col>
              </Row>
              <Form.Item label={<label htmlFor="business-solutions">方案例子</label>} extra="已有的真实做法，有几份就先填几份"><Input.TextArea id="business-solutions" value={draft.business.solution_examples} onChange={(event) => updateBusiness('solution_examples', event.target.value)} rows={4} placeholder="做过什么，适用什么情况，有哪些取舍" /></Form.Item>
              <Form.Item label={<label htmlFor="business-regions">区域偏好</label>}><Input.TextArea id="business-regions" value={draft.business.region_preferences} onChange={(event) => updateBusiness('region_preferences', event.target.value)} rows={2} placeholder="优先服务的地区，以及需要先确认的现场服务条件" /></Form.Item>
            </Form>
            <div className="settings-help"><p>{capabilityEmpty ? '先补充业务范围、支持产品和能力说明，再判断我们是否适合项目。' : '能力资料变更后，旧 AI 判断会提示复核；人工选择与历史记录继续保留。'}</p></div>
          </Card>
          <Card id="rule-config" title="筛选门槛" className="settings-panel" extra={<Text type="secondary">明确不合适才排除</Text>}>
            <Row gutter={18}>
              <Col xs={24} md={12}><Form layout="vertical"><Form.Item label={<label htmlFor="rule-target-keywords">目标关键词</label>} extra="每行一个，或用逗号分隔；未命中仍保留待核实"><Input.TextArea id="rule-target-keywords" value={keywordText.target} onChange={(event) => { setKeywordText(current => ({ ...current, target: event.target.value })); setDirty(true) }} rows={4} placeholder={'破碎机\n智能矿山\n备件'} /></Form.Item></Form></Col>
              <Col xs={24} md={12}><Form layout="vertical"><Form.Item label={<label htmlFor="rule-exclude-keywords">明确排除关键词</label>} extra="只填写确定不承接的品类或服务"><Input.TextArea id="rule-exclude-keywords" value={keywordText.exclude} onChange={(event) => { setKeywordText(current => ({ ...current, exclude: event.target.value })); setDirty(true) }} rows={4} placeholder="完全不承接的业务" /></Form.Item></Form></Col>
            </Row>
            <Paragraph className="settings-bottom-note">设备字段为空、资质不明和疑似重复，都会先保留待核实。</Paragraph>
          </Card>
        </div>
        <div className="settings-side">
          <Card id="model-config" title="模型接口" className="settings-panel model-panel" extra={<Tag color={savedModelReady ? 'success' : 'default'}>{savedModelReady ? '已配置' : '待配置'}</Tag>}>
            <div className="settings-switch-row"><div><label htmlFor="ai-enabled">启用模型</label><p>保存后，符合条件的项目开始分析</p></div><Switch id="ai-enabled" checked={draft.ai.enabled} onChange={checked => updateAi('enabled', checked)} /></div>
            <Form layout="vertical">
              <Form.Item label={<label htmlFor="ai-base-url">兼容 OpenAI 的 Base URL</label>} extra="填写服务商提供的接口地址，通常以 /v1 结尾"><Input id="ai-base-url" value={draft.ai.base_url} onChange={event => updateAi('base_url', event.target.value)} placeholder="https://api.example.com/v1" /></Form.Item>
              <Form.Item label={<label htmlFor="ai-model">模型名称</label>}><Input id="ai-model" value={draft.ai.model} onChange={event => updateAi('model', event.target.value)} placeholder="填写服务商的实际模型名称" /></Form.Item>
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
  return {
    version: value.version ?? 0,
    business: {
      company_name: business.company_name ?? '', scope_description: business.scope_description ?? '', target_keywords: Array.isArray(business.target_keywords) ? business.target_keywords : [], exclude_keywords: Array.isArray(business.exclude_keywords) ? business.exclude_keywords : [], supported_products: typeof business.supported_products === 'string' ? business.supported_products : JSON.stringify(business.supported_products ?? ''), capabilities: typeof business.capabilities === 'string' ? business.capabilities : JSON.stringify(business.capabilities ?? ''), solution_examples: typeof business.solution_examples === 'string' ? business.solution_examples : JSON.stringify(business.solution_examples ?? ''), region_preferences: typeof business.region_preferences === 'string' ? business.region_preferences : JSON.stringify(business.region_preferences ?? ''),
    },
    ai: { base_url: value.ai?.base_url ?? '', model: value.ai?.model ?? '', enabled: Boolean(value.ai?.enabled), has_api_key: Boolean(value.ai?.has_api_key) },
    import: { path: value.import?.path ?? '', auto_import: Boolean(value.import?.auto_import), poll_seconds: Number(value.import?.poll_seconds ?? 300) },
  }
}
