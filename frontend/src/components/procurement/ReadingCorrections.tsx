import { useEffect, useMemo, useState } from 'react'
import { Alert, Button, Collapse, Input, Select, Typography, message } from 'antd'
import { EditOutlined } from '@ant-design/icons'
import { api } from '../../api'
import type { ProcurementReading, ProcurementReadingCorrection } from '../../types'
import { formatDate } from '../Common'
import './ProcurementSupplement.css'

const { Text } = Typography

interface ReadingCorrectionsProps {
  projectId: number
  reading?: ProcurementReading | null
  onCorrected?: () => void
}

export function ReadingCorrections({ projectId, reading, onCorrected }: ReadingCorrectionsProps) {
  const [corrections, setCorrections] = useState<ProcurementReadingCorrection[]>([])
  const [requirementId, setRequirementId] = useState<string>()
  const [text, setText] = useState('')
  const [reason, setReason] = useState('')
  const [loading, setLoading] = useState(false)
  const [loadingHistory, setLoadingHistory] = useState(false)
  const [error, setError] = useState('')
  const requirementOptions = useMemo(() => reading?.sections.flatMap((section) => section.requirements.map((item) => ({ value: item.id, label: `${section.title} · ${item.text.slice(0, 60)}` }))) ?? [], [reading])

  useEffect(() => {
    let active = true
    setLoadingHistory(true)
    void api.readingCorrections(projectId).then((result) => { if (active) setCorrections(Array.isArray(result.corrections) ? result.corrections : []) }).catch((loadError) => {
      // Older backends may not expose this optional endpoint yet. Keep that
      // state visible without treating it as a successful correction.
      if (active) setError(loadError instanceof Error ? loadError.message : '纠正记录读取失败')
    }).finally(() => { if (active) setLoadingHistory(false) })
    return () => { active = false }
  }, [projectId])

  const submit = async () => {
    if (!requirementId || !text.trim() || !reason.trim()) {
      message.warning('请选择要求，并填写纠正内容和理由')
      return
    }
    setLoading(true)
    setError('')
    try {
      const result = await api.addReadingCorrection(projectId, { requirement_id: requirementId, text: text.trim(), reason: reason.trim() })
      setCorrections(result.corrections ?? [])
      setRequirementId(undefined)
      setText('')
      setReason('')
      message.success(result.message || '人工纠正已记录，等待重新整理采购说明')
      onCorrected?.()
    } catch (submitError) {
      const messageText = submitError instanceof Error ? submitError.message : '保存纠正失败'
      setError(messageText)
      message.error(messageText)
    } finally {
      setLoading(false)
    }
  }

  return (
    <section className="reading-corrections" aria-labelledby="corrections-heading">
      <Collapse ghost items={[{ key: 'corrections', label: <span id="corrections-heading"><EditOutlined /> 纠正采购说明（保留留痕）</span>, children: <div className="correction-body">
        <Text type="secondary">仅修改采购事实的呈现。请说明原文依据或纠正原因，系统会保留材料版本与操作记录。</Text>
        {requirementOptions.length === 0 ? <Alert type="info" showIcon message="当前没有可纠正的要求" description="阅读完成后，可对具体要求提交人工纠正。" /> : <>
          <label><span>要纠正的要求</span><Select showSearch value={requirementId} onChange={setRequirementId} options={requirementOptions} placeholder="选择一条采购要求" /></label>
          <label><span>纠正后的内容</span><Input.TextArea value={text} onChange={(event) => setText(event.target.value)} rows={3} placeholder="填写准确的采购要求或补充条件" /></label>
          <label><span>理由与来源</span><Input.TextArea value={reason} onChange={(event) => setReason(event.target.value)} rows={2} placeholder="例如：附件第 4 页明确写明……" /></label>
          <Button type="primary" loading={loading} onClick={() => void submit()}>保存纠正</Button>
        </>}
        {error && <Alert type="error" showIcon message={error} />}
        <div className="correction-history"><div className="correction-history-heading"><Text strong>历史纠正</Text>{loadingHistory && <Text type="secondary">读取中…</Text>}</div>{corrections.length === 0 ? <Text type="secondary">暂无人工纠正记录</Text> : <div className="correction-list">{corrections.map((correction) => <div className="correction-item" key={correction.id}><div><strong>{correction.text}</strong><Text type="secondary">要求 {correction.requirement_id} · {formatDate(correction.created_at, true)} · 材料版本 {correction.material_version ?? '待标记'}</Text></div><p>{correction.reason}</p></div>)}</div>}</div>
      </div> }]} />
    </section>
  )
}
