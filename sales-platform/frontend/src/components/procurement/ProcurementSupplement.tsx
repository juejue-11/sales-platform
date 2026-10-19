import { useRef, useState } from 'react'
import { Alert, Button, Select, Tag, Typography, message } from 'antd'
import { FileTextOutlined, UploadOutlined } from '@ant-design/icons'
import { api } from '../../api'
import type { ProcurementDocumentSnapshot, ProjectRecord } from '../../types'
import './ProcurementSupplement.css'

const MAX_BYTES = 12 * 1024 * 1024
const ALLOWED_EXTENSIONS = new Set(['pdf', 'docx', 'doc', 'txt'])
const { Text } = Typography

interface ProcurementSupplementProps {
  projectId: number
  records: ProjectRecord[]
  onUploaded?: () => void
}

function extensionOf(name: string): string {
  const parts = name.toLowerCase().split('.')
  return parts.length > 1 ? parts[parts.length - 1] : ''
}

function readBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onerror = () => reject(new Error('文件读取失败，请重试'))
    reader.onload = () => {
      const result = typeof reader.result === 'string' ? reader.result : ''
      resolve(result.includes(',') ? result.slice(result.indexOf(',') + 1) : result)
    }
    reader.readAsDataURL(file)
  })
}

export function ProcurementSupplement({ projectId, records, onUploaded }: ProcurementSupplementProps) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [recordId, setRecordId] = useState<number | undefined>(records[0]?.id)
  const [uploading, setUploading] = useState(false)
  const [error, setError] = useState('')
  const [lastFile, setLastFile] = useState('')
  const [lastDocument, setLastDocument] = useState<ProcurementDocumentSnapshot | null>(null)

  const chooseFile = () => inputRef.current?.click()

  const upload = async (file: File) => {
    setError('')
    if (!recordId) {
      setError('请先选择要补充的原始记录。')
      return
    }
    if (!ALLOWED_EXTENSIONS.has(extensionOf(file.name))) {
      setError('仅支持 PDF、DOCX、DOC 或 TXT 文件。')
      return
    }
    if (file.size > MAX_BYTES) {
      setError('文件不能超过 12 MiB。')
      return
    }
    setUploading(true)
    try {
      const contentBase64 = await readBase64(file)
      const result = await api.supplementDocument(projectId, { record_id: recordId, name: file.name, content_base64: contentBase64 })
      setLastFile(file.name)
      setLastDocument(result.document)
      message.success(result.message || '补充资料已提交，等待阅读处理')
      onUploaded?.()
    } catch (uploadError) {
      const text = uploadError instanceof Error ? uploadError.message : '补充资料失败，请稍后重试'
      setError(text)
      message.error(text)
    } finally {
      setUploading(false)
      if (inputRef.current) inputRef.current.value = ''
    }
  }

  return (
    <section className="procurement-supplement" aria-labelledby="supplement-heading">
      <div className="supplement-heading"><div><h2 id="supplement-heading">补充采购资料</h2><Text type="secondary">仅补充受限、缺失或需要核对的采购文件，不会把上传文件当成外部提交凭证。</Text></div><Tag>最多 12 MiB</Tag></div>
      {records.length === 0 ? <Alert type="info" showIcon message="暂无可关联的原始记录" description="请先让平台接入这项采购的公告记录。" /> : <div className="supplement-form">
        <label><span>关联记录</span><Select value={recordId} onChange={setRecordId} options={records.map((record) => ({ value: record.id, label: `${record.title || '未命名记录'} · #${record.id}` }))} /></label>
        <input ref={inputRef} type="file" accept=".pdf,.docx,.doc,.txt,application/pdf,text/plain,application/msword,application/vnd.openxmlformats-officedocument.wordprocessingml.document" hidden onChange={(event) => { const file = event.target.files?.[0]; if (file) void upload(file) }} />
        <Button type="primary" icon={<UploadOutlined />} loading={uploading} onClick={chooseFile}>选择并上传文件</Button>
        {lastFile && <span className="supplement-last"><FileTextOutlined /> 已提交：{lastFile}{lastDocument?.id != null && <a href={api.uploadedFileUrl(projectId, lastDocument.id)} target="_blank" rel="noreferrer">下载补充文件</a>}</span>}
      </div>}
      {error && <Alert className="supplement-error" type="error" showIcon message={error} />}
    </section>
  )
}
