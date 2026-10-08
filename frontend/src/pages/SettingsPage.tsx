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
      const body: SettingsData = copySettings({ ...draft, ai: { ...draft.ai, ...(apiKey ? { api_key: apiKey } : {}), ...(clearApiKey ? { clear_api_key: true } : {}) } })
      const saved = normalizeSettings(await api.saveSettings(body))
      const fresh = normalizeSettings(await api.settings())
      setSettings(fresh.version >= saved.version ? fresh : saved)
      setDraft(copySettings(fresh.version >= saved.version ? fresh : saved))
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
  return (
    <div className="page-stack settings-page">
      <div className="page-heading"><div><Title level={2}>业务配置</Title><Paragraph type="secondary">维护规则和 AI 能够引用的公司资料。资料不足时系统会保留待核实状态，不会替公司补写能力。</Paragraph></div><Space><Text type={dirty ? 'warning' : 'secondary'}>{dirty ? '有未保存修改' : `配置版本 v${settings.version}`}</Text><Button icon={<ReloadOutlined />} onClick={() => void loadSettings()} disabled={dirty}>重新读取</Button><Button type="primary" icon={<SaveOutlined />} loading={saving} disabled={!dirty} onClick={() => void save()}>保存配置</Button></Space></div>
      {capabilityEmpty && <Alert type="warning" showIcon message="公司能力卡为空" description="请先补充业务范围、支持产品和能力说明。空资料不会产生可靠的 AI 方案。" />}
      <Alert type="info" showIcon message="配置变更会触发后端复核" description="业务范围或能力资料变化后，后端会把受到影响的旧判断标为待复核；历史判断和人工决定仍会保留。" />
      <Row gutter={[16, 16]}>
        <Col xs={24} xl={15}>
          <Card title="公司能力卡" extra={<Tag color={capabilityEmpty ? 'warning' : 'success'}>{capabilityEmpty ? '待补充资料' : '已填写'}</Tag>}>
            <Form layout="vertical">
              <Form.Item label="公司名称"><Input value={draft.business.company_name} onChange={(event) => updateBusiness('company_name', event.target.value)} placeholder="例如：某某智能装备有限公司" /></Form.Item>
              <Form.Item label="业务范围"><Input.TextArea value={draft.business.scope_description} onChange={(event) => updateBusiness('scope_description', event.target.value)} rows={3} placeholder="我们做什么、服务哪些客户和场景" /></Form.Item>
              <Row gutter={12}><Col xs={24} md={12}><Form.Item label="支持的产品 / 设备"><Input.TextArea value={draft.business.supported_products} onChange={(event) => updateBusiness('supported_products', event.target.value)} rows={5} placeholder="填写已确认可提供的产品、设备或服务" /></Form.Item></Col><Col xs={24} md={12}><Form.Item label="明确能力"><Input.TextArea value={draft.business.capabilities} onChange={(event) => updateBusiness('capabilities', event.target.value)} rows={5} placeholder="供货、改造、实施、交付范围和限制" /></Form.Item></Col></Row>
              <Form.Item label="方案例子"><Input.TextArea value={draft.business.solution_examples} onChange={(event) => updateBusiness('solution_examples', event.target.value)} rows={5} placeholder="填写已确认的方案做法、适用条件和取舍" /></Form.Item>
              <Form.Item label="区域偏好"><Input.TextArea value={draft.business.region_preferences} onChange={(event) => updateBusiness('region_preferences', event.target.value)} rows={2} placeholder="例如：优先华东；需现场服务的地区请先确认" /></Form.Item>
            </Form>
          </Card>
        </Col>
        <Col xs={24} xl={9}>
          <Card title="筛选规则">
            <Form layout="vertical"><Form.Item label="目标关键词" extra="每行一个，也可以用逗号分隔"><Input.TextArea value={toLines(draft.business.target_keywords)} onChange={(event) => updateBusiness('target_keywords', fromLines(event.target.value))} rows={6} placeholder="破碎机\n智能矿山\n备件" /></Form.Item><Form.Item label="明确排除关键词" extra="只有明确不符合时才作为排除规则"><Input.TextArea value={toLines(draft.business.exclude_keywords)} onChange={(event) => updateBusiness('exclude_keywords', fromLines(event.target.value))} rows={6} placeholder="完全不承接的品类或服务" /></Form.Item></Form>
            <Alert type="warning" showIcon message="信息不足不会自动淘汰" description="空的设备字段、解析异常和疑似重复会进入待核实或线索区。" />
          </Card>
          <Card title="模型配置" className="settings-card">
            <Form layout="vertical"><Form.Item label="启用模型"><Switch checked={draft.ai.enabled} onChange={(checked) => updateAi('enabled', checked)} /></Form.Item><Form.Item label="兼容 OpenAI 的 Base URL"><Input value={draft.ai.base_url} onChange={(event) => updateAi('base_url', event.target.value)} placeholder="https://api.example.com/v1" /></Form.Item><Form.Item label="模型名称"><Input value={draft.ai.model} onChange={(event) => updateAi('model', event.target.value)} placeholder="填写实际模型名" /></Form.Item><Form.Item label="API Key" extra="密钥只写入后端设置，读取时不会返回；留空表示保留已有密钥"><Input.Password prefix={<LockOutlined />} value={apiKey} onChange={(event) => { setApiKey(event.target.value); setDirty(true) }} placeholder={settings.ai.has_api_key ? '已配置，输入新密钥可替换' : '尚未配置'} /></Form.Item><Space direction="vertical"><Text type="secondary"><SafetyCertificateOutlined /> 当前状态：{settings.ai.has_api_key ? '已配置密钥' : '未配置密钥'}</Text><label className="clear-key-label"><input type="checkbox" checked={clearApiKey} onChange={(event) => { setClearApiKey(event.target.checked); setDirty(true) }} /> 清除后端已有密钥</label></Space></Form>
            {!draft.ai.enabled || !draft.ai.base_url || !draft.ai.model || (!settings.ai.has_api_key && !apiKey) ? <Alert className="settings-alert" type="info" showIcon message="模型配置不完整" description="保存后项目会显示未配置模型，系统不会伪造 AI 判断。" /> : <Alert className="settings-alert" type="success" showIcon message="模型配置已填写" description="保存后可在项目详情提交分析任务。" />}
          </Card>
          <Card title="导入设置" className="settings-card"><Form layout="vertical"><Form.Item label="records.jsonl 路径"><Input value={draft.import.path} onChange={(event) => updateImport('path', event.target.value)} placeholder="留空使用后端默认路径" /></Form.Item><Form.Item label="自动检查"><Switch checked={draft.import.auto_import} onChange={(checked) => updateImport('auto_import', checked)} /></Form.Item><Form.Item label="检查间隔（秒）"><InputNumber min={30} step={30} value={draft.import.poll_seconds} onChange={(value) => updateImport('poll_seconds', value ?? 300)} style={{ width: '100%' }} /></Form.Item></Form><Paragraph type="secondary">采集密钥、后台日志和原始密钥不会出现在这里。</Paragraph></Card>
        </Col>
      </Row>
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

