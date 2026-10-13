import { Alert, Table, Tag, Typography } from 'antd'
import { useId } from 'react'
import type { ReactNode } from 'react'
import type {
  AgentOpportunity,
  AgentReport as AgentReportData,
  AgentReportBlock,
  AgentReportBlockEvidence,
  AgentReportGap,
  AgentReportGapStatus,
  Judgment,
} from '../../types'
import './AgentReport.css'

const { Paragraph, Text } = Typography

export interface AgentReportProps {
  report: AgentReportData
  /** Let the detail page reuse its existing record/document/source citation renderer. */
  renderEvidence?: (evidence: AgentReportBlockEvidence, key: string) => ReactNode
  className?: string
}

const gapStatusMeta: Record<AgentReportGapStatus, { label: string; color?: string }> = {
  not_stated: { label: '原文未说明' },
  not_read: { label: '尚未读取', color: 'warning' },
  read_failed: { label: '读取失败', color: 'error' },
  not_applicable: { label: '不适用' },
}

const opportunityKindMeta: Record<AgentOpportunity['kind'], { label: string; color: string }> = {
  explicit_demand: { label: '明确采购', color: 'blue' },
  potential_opportunity: { label: '潜在线索', color: 'cyan' },
}

function reportEvidenceLabel(evidence: AgentReportBlockEvidence): string {
  if ('source_ref' in evidence) return evidence.title || evidence.source_ref
  if (evidence.document_name) return evidence.document_name
  return `公告正文 · 记录 #${evidence.record_id}`
}

function DefaultEvidence({ evidence }: { evidence: AgentReportBlockEvidence }) {
  const href = 'source_ref' in evidence ? evidence.source_url : evidence.snapshot_url || evidence.source_url
  return (
    <div className="agent-report-evidence agent-report-evidence-fallback">
      <span>{href ? <a href={href} target="_blank" rel="noreferrer">{reportEvidenceLabel(evidence)}</a> : reportEvidenceLabel(evidence)}</span>
      {evidence.quote && <q>{evidence.quote}</q>}
    </div>
  )
}

function EvidenceList({ evidence, renderEvidence }: { evidence: AgentReportBlockEvidence[]; renderEvidence?: AgentReportProps['renderEvidence'] }) {
  if (!evidence.length) return null
  return (
    <div className="agent-report-evidence-list" aria-label="引用依据">
      <Text className="agent-report-evidence-label" type="secondary">引用依据</Text>
      {evidence.map((item, index) => {
        const key = `${'source_ref' in item ? item.source_ref : item.record_id}-${'source_version' in item ? item.source_version : item.document_id ?? 'record'}-${index}`
        return <div key={key}>{renderEvidence ? renderEvidence(item, key) : <DefaultEvidence evidence={item} />}</div>
      })}
    </div>
  )
}

function ReportBlockView({ block, renderEvidence }: { block: AgentReportBlock; renderEvidence?: AgentReportProps['renderEvidence'] }) {
  if (block.type === 'paragraph') {
    return <div className="agent-report-block agent-report-paragraph"><Paragraph>{block.text || '—'}</Paragraph><EvidenceList evidence={block.evidence ?? []} renderEvidence={renderEvidence} /></div>
  }
  if (block.type === 'list') {
    return <div className="agent-report-block agent-report-list"><ul>{(block.items ?? []).map((item, index) => <li key={`${item.text}-${index}`}><span>{item.text || '—'}</span><EvidenceList evidence={item.evidence ?? []} renderEvidence={renderEvidence} /></li>)}</ul></div>
  }
  const columns = (block.columns ?? []).map((column, index) => ({ title: column || `列 ${index + 1}`, dataIndex: `cell_${index}`, key: `cell_${index}`, render: (value: string) => <span>{value || '—'}</span> }))
  const dataSource = (block.rows ?? []).map((row, rowIndex) => ({ key: `${rowIndex}-${row.cells.join('|')}`, ...Object.fromEntries((row.cells ?? []).map((cell, cellIndex) => [`cell_${cellIndex}`, cell])), evidence: row.evidence ?? [] }))
  return <div className="agent-report-block agent-report-table"><Table size="small" bordered pagination={false} scroll={{ x: 'max-content' }} columns={columns} dataSource={dataSource} locale={{ emptyText: '暂无表格内容' }} /><div className="agent-report-table-evidence">{dataSource.map((row, rowIndex) => row.evidence.length > 0 && <div key={row.key}><Text className="agent-report-evidence-label" type="secondary">第 {rowIndex + 1} 行依据</Text><EvidenceList evidence={row.evidence} renderEvidence={renderEvidence} /></div>)}</div></div>
}

function OpportunityCard({ opportunity, renderEvidence }: { opportunity: AgentOpportunity; renderEvidence?: AgentReportProps['renderEvidence'] }) {
  const meta = opportunityKindMeta[opportunity.kind] ?? opportunityKindMeta.potential_opportunity
  return (
    <article className={`agent-report-opportunity agent-report-opportunity-${opportunity.kind}`}>
      <div className="agent-report-opportunity-heading"><Tag color={meta.color}>{meta.label}</Tag><h3>{opportunity.title || '未命名机会'}</h3></div>
      <Paragraph>{opportunity.description || '暂无说明'}</Paragraph>
      {opportunity.basis && <div className="agent-report-opportunity-field"><Text strong>判断依据</Text><p>{opportunity.basis}</p></div>}
      {opportunity.uncertainties?.length > 0 && <div className="agent-report-opportunity-field"><Text strong>仍需确认</Text><ul>{opportunity.uncertainties.map((item, index) => <li key={`${item}-${index}`}>{item}</li>)}</ul></div>}
      {opportunity.next_actions?.length > 0 && <div className="agent-report-opportunity-field"><Text strong>下一步</Text><ul>{opportunity.next_actions.map((item, index) => <li key={`${item}-${index}`}>{item}</li>)}</ul></div>}
      <EvidenceList evidence={opportunity.evidence ?? []} renderEvidence={renderEvidence} />
    </article>
  )
}

function GapCard({ gap }: { gap: AgentReportGap }) {
  const meta = gapStatusMeta[gap.status] ?? gapStatusMeta.not_stated
  return (
    <article className="agent-report-gap">
      <div className="agent-report-gap-heading"><strong>{gap.description || '待确认信息'}</strong><Tag color={meta.color}>{meta.label}</Tag></div>
      {gap.impact && <p><Text strong>影响：</Text>{gap.impact}</p>}
      {gap.next_action && <p><Text strong>下一步：</Text>{gap.next_action}</p>}
      <Text type="secondary">{gap.worth_researching ? '值得补查' : '暂不建议补查'}</Text>
    </article>
  )
}

export function AgentReport({ report, renderEvidence, className = '' }: AgentReportProps) {
  const reportId = useId().replace(/:/g, '')
  const headingId = `${reportId}-heading`
  const opportunitiesHeadingId = `${reportId}-opportunities-heading`
  const gapsHeadingId = `${reportId}-gaps-heading`
  return (
    <section className={`agent-report ${className}`.trim()} aria-labelledby={headingId}>
      <div className="agent-report-heading"><div><h2 id={headingId}>Agent 研判报告</h2><p>按本项目实际情况组织的章节、机会判断与证据。</p></div></div>
      {report.sections?.length > 0 && <div className="agent-report-sections">{report.sections.map((section, sectionIndex) => <section className="agent-report-section" key={`${section.id || 'section'}-${sectionIndex}`}><h3>{section.title || '未命名章节'}</h3>{(section.blocks ?? []).map((block, blockIndex) => <ReportBlockView block={block} renderEvidence={renderEvidence} key={`${section.id || sectionIndex}-${block.type}-${blockIndex}`} />)}</section>)}</div>}
      {report.opportunities?.length > 0 && <section className="agent-report-opportunities" aria-labelledby={opportunitiesHeadingId}><div className="agent-report-subheading"><h3 id={opportunitiesHeadingId}>机会判断</h3><Text type="secondary">明确采购与潜在线索分开显示</Text></div><div className="agent-report-opportunity-list">{report.opportunities.map((opportunity, index) => <OpportunityCard key={`${opportunity.id || opportunity.title}-${index}`} opportunity={opportunity} renderEvidence={renderEvidence} />)}</div></section>}
      {report.gaps?.length > 0 && <section className="agent-report-gaps" aria-labelledby={gapsHeadingId}><div className="agent-report-subheading"><h3 id={gapsHeadingId}>信息缺口</h3><Text type="secondary">状态来自资料读取与适用性判断</Text></div><div className="agent-report-gap-list">{report.gaps.map((gap, index) => <GapCard gap={gap} key={`${gap.id || gap.description}-${index}`} />)}</div></section>}
      {!report.sections?.length && !report.opportunities?.length && !report.gaps?.length && <Alert type="info" showIcon message="本次报告没有可展示的内容" />}
    </section>
  )
}

export function LegacyJudgmentReport({ judgment, renderEvidence }: { judgment: Judgment; renderEvidence?: AgentReportProps['renderEvidence'] }) {
  return (
    <div className="legacy-judgment-report">
      {judgment.demand_summary && <div className="legacy-judgment-field"><Text strong>需求摘要</Text><Paragraph>{judgment.demand_summary}</Paragraph></div>}
      <div className="legacy-judgment-field"><Text strong>AI 理由与证据</Text>{judgment.reasons.length > 0 ? <div className="legacy-judgment-reasons">{judgment.reasons.map((reason, index) => <article key={`${reason.text}-${index}`}><Paragraph>{reason.text}</Paragraph><EvidenceList evidence={reason.evidence ?? []} renderEvidence={renderEvidence} /></article>)}</div> : <Text type="secondary">暂无结构化理由</Text>}</div>
      {judgment.unknowns.length > 0 && <div className="legacy-judgment-field"><Text strong>待确认信息</Text><ul>{judgment.unknowns.map((item, index) => <li key={`${item}-${index}`}>{item}</li>)}</ul></div>}
      {judgment.next_actions.length > 0 && <div className="legacy-judgment-field"><Text strong>建议下一步</Text><ul>{judgment.next_actions.map((item, index) => <li key={`${item}-${index}`}>{item}</li>)}</ul></div>}
    </div>
  )
}
