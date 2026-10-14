import { Alert, Button, Collapse, Empty, Tag, Typography } from 'antd'
import { FileSearchOutlined, ReloadOutlined } from '@ant-design/icons'
import type {
  ProcurementData,
  ProcurementDocument,
  ProjectBriefing,
  ProcurementReading,
  ProcurementReadingRequirement,
  ProcurementReadingSection,
  ProjectRecord,
} from '../../types'
import { formatDate } from '../Common'
import { ProcurementBriefing } from './ProcurementBriefing'
import {
  demandTypeMeta,
  demandTypeLabel,
  readingCompleteness,
  readingStageLabel,
  readingStatusMeta,
  readableCoverage,
  sourceLabel,
  submissionMilestone,
} from './reading'
import './ProcurementReading.css'

const { Text } = Typography

const QUESTION_TITLES: Record<string, string> = {
  buyer: '谁在采购',
  scope: '买什么',
  delivery: '怎样交付',
  qualification: '谁能参加',
  schedule: '什么时候办理',
  materials: '需要什么费用与材料',
  participation: '怎样参与',
  special: '特殊要求与待确认事项',
}

interface ProcurementReadingProps {
  projectId: number
  reading?: ProcurementReading | null
  legacyProcurement?: ProcurementData | null
  briefing?: ProjectBriefing | null
  records?: ProjectRecord[]
  title?: string
  onRefresh?: () => void
  refreshing?: boolean
  error?: unknown
  onOpenOperations?: () => void
}

function sourceText(source: ProcurementReadingRequirement['sources'][number]): string {
  return source.quote?.trim() || '来源已登记，原文引文暂未提供。'
}

function requirementStatusLabel(status: string): string {
  return ({ confirmed: '已确认', extracted: '已读取', uncertain: '待核对', conflict: '来源冲突', missing: '待补充' } as Record<string, string>)[status] ?? status
}

function RequirementSource({ requirement, records, documents }: { requirement: ProcurementReadingRequirement; records: ProjectRecord[]; documents: ProcurementDocument[] }) {
  if (!requirement.sources?.length) return <Text type="secondary" className="reading-source-missing">来源待补充</Text>
  return (
    <div className="reading-source-list">
      {requirement.sources.map((source, index) => (
        <details className="reading-source" key={`${source.record_id}-${source.document_id ?? 'record'}-${source.page ?? ''}-${index}`}>
          <summary><FileSearchOutlined aria-hidden="true" /> {sourceLabel(source, records, documents)}</summary>
          <blockquote>{sourceText(source)}</blockquote>
          {source.sha256 && <Text type="secondary">资料版本已记录</Text>}
        </details>
      ))}
    </div>
  )
}

const milestoneKindLabels: Record<string, string> = {
  document_access: '文件获取',
  registration: '报名 / 注册',
  question: '答疑 / 澄清',
  submission: '提交',
  opening: '开标 / 评审',
  other: '其他节点',
}

function MilestoneList({ reading, records, documents }: { reading: ProcurementReading; records: ProjectRecord[]; documents: ProcurementDocument[] }) {
  if (!reading.milestones?.length) return null
  return (
    <section className="reading-milestones" aria-labelledby="reading-milestones-heading">
      <div className="reading-section-header"><div><h2 id="reading-milestones-heading">办理节点</h2><Text type="secondary">按阅读结果列出全部时间节点；来源和不确定性保留在节点内。</Text></div><Text type="secondary">{reading.milestones.length} 个节点</Text></div>
      <ol className="reading-milestone-list">
        {reading.milestones.map((milestone) => (
          <li className="reading-milestone" key={milestone.id}>
            <div className="reading-milestone-main"><Tag>{milestoneKindLabels[milestone.kind] ?? milestone.kind}</Tag><strong>{milestone.label || '未命名节点'}</strong></div>
            <div className="reading-milestone-time">{milestone.at ? formatDate(milestone.at, true) : '时间待核对'}</div>
            {milestone.sources?.length ? <div className="reading-milestone-sources">{milestone.sources.map((source, index) => <details key={`${source.record_id}-${source.document_id ?? 'record'}-${source.page ?? ''}-${index}`}><summary>{sourceLabel(source, records, documents)}</summary><blockquote>{source.quote?.trim() || '来源原文待补充'}</blockquote></details>)}</div> : <Text type="secondary">节点来源待补充</Text>}
          </li>
        ))}
      </ol>
    </section>
  )
}

function RequirementRow({ requirement, sectionTitle }: { requirement: ProcurementReadingRequirement; sectionTitle: string }) {
  return (
    <tr>
      <td>{requirement.lot == null || requirement.lot === '' ? '—' : String(requirement.lot)}</td>
      <td><strong>{sectionTitle}</strong><div className="reading-table-text">{requirement.text}</div>{requirement.explanation && <Text type="secondary">{requirement.explanation}</Text>}</td>
      <td>{requirement.status ? requirementStatusLabel(requirement.status) : '已读取'}</td>
      <td>{requirement.sources?.length ? `${requirement.sources.length} 条` : '待补来源'}</td>
    </tr>
  )
}

function SectionBlock({ section, records, documents }: { section: ProcurementReadingSection; records: ProjectRecord[]; documents: ProcurementDocument[] }) {
  return (
    <section className="reading-question" aria-labelledby={`reading-question-${section.id}`}>
      <div className="reading-question-heading"><h3 id={`reading-question-${section.id}`}>{QUESTION_TITLES[section.id] ?? section.title}</h3><Text type="secondary">{section.requirements.length} 项</Text></div>
      {section.requirements.length === 0 ? <Text type="secondary">暂无已读取要求，查看待确认事项。</Text> : (
        <div className="reading-requirements">
          {section.requirements.map((requirement) => (
            <article className="reading-requirement" key={requirement.id}>
              <div className="reading-requirement-main"><div className="reading-requirement-title"><strong>{requirement.text || '要求正文待补充'}</strong>{requirement.lot != null && requirement.lot !== '' && <Tag>包 / 标段 {requirement.lot}</Tag>}{requirement.status && requirement.status !== 'confirmed' && <Tag color="warning">{requirementStatusLabel(requirement.status)}</Tag>}</div>{requirement.explanation && <p>{requirement.explanation}</p>}</div>
              <RequirementSource requirement={requirement} records={records} documents={documents} />
            </article>
          ))}
        </div>
      )}
    </section>
  )
}

export function ProcurementReadingPanel({ projectId, reading, legacyProcurement, briefing, records = [], title = '采购项目', onRefresh, refreshing = false, error, onOpenOperations }: ProcurementReadingProps) {
  if (!reading) {
    return (
      <div className="procurement-reading-panel" data-testid="procurement-reading">
        {legacyProcurement || briefing ? <ProcurementBriefing projectId={projectId} briefing={briefing} procurement={legacyProcurement} records={records} title={title} onRefresh={onRefresh ?? (() => undefined)} refreshing={refreshing} error={error} /> : <div className="reading-empty"><Empty description="采购说明尚未建立" />{Boolean(error) && <Alert type="error" showIcon message="采购资料更新失败" description={error instanceof Error ? error.message : '资料更新失败，请稍后重试。'} />}{onOpenOperations && <Button type="link" onClick={onOpenOperations}>查看处理状态</Button>}</div>}
      </div>
    )
  }
  const displayReading = reading
  const submission = submissionMilestone(displayReading)
  const statusMeta = displayReading ? readingStatusMeta[displayReading.status] : null
  const demandMeta = displayReading ? demandTypeMeta[displayReading.demand_type] : demandTypeMeta.unknown
  const allRequirements = displayReading?.sections.flatMap((section) => section.requirements.map((requirement) => ({ requirement, sectionTitle: section.title }))) ?? []
  const isFailure = displayReading?.status === 'failed' || displayReading?.status === 'not_configured'
  const recordCount = records.length
  const documents = legacyProcurement?.documents ?? []

  return (
    <div className="procurement-reading-panel" data-testid="procurement-reading">
      <header className="reading-header">
        <div><div className="reading-kicker">采购信息</div><h2>采购说明</h2><p className="reading-summary">{displayReading?.summary || (displayReading ? '这份采购说明暂未形成一句话摘要。' : '采购说明待核对，暂无可引用的完整阅读结果。')}</p></div>
        <div className="reading-header-actions">{onRefresh && <Button size="small" icon={<ReloadOutlined />} loading={refreshing} onClick={onRefresh}>更新公开资料</Button>}</div>
      </header>

      {Boolean(error) && <Alert className="reading-error" type="error" showIcon message="采购资料更新失败" description={error instanceof Error ? error.message : '资料更新失败，请稍后重试。'} />}
      {isFailure && <Alert className="reading-processing-alert" type={displayReading?.status === 'not_configured' ? 'info' : 'warning'} showIcon message="资料处理状态需要查看" description={displayReading?.status === 'not_configured' ? '阅读服务尚未配置，采购事实不会由页面自行推断。' : '阅读或资料获取没有完成，前台保留当前已知事实；请在处理状态查看原因。'} action={onOpenOperations ? <Button type="link" onClick={onOpenOperations}>查看处理状态</Button> : undefined} />}

      <section className="reading-facts-strip" aria-label="采购说明摘要">
        <div><span>需求类型</span><strong><Tag color={demandMeta.color}>{demandTypeLabel(displayReading?.demand_type)}</Tag></strong></div>
        <div><span>采购阶段</span><strong>{readingStageLabel(displayReading?.stage)}</strong></div>
        <div><span>采购方</span><strong>{displayReading?.buyer || '采购方待核对'}</strong></div>
        <div><span>地区</span><strong>{displayReading?.region || '地区待核对'}</strong></div>
        <div><span>提交截止</span><strong>{submission?.at ? formatDate(submission.at, true) : '提交截止待核对'}</strong>{submission?.label && <Text type="secondary">{submission.label}</Text>}</div>
        <div><span>资料完整性</span><strong>{readingCompleteness(displayReading)}</strong>{statusMeta && <Tag color={statusMeta.color}>{statusMeta.label}</Tag>}</div>
      </section>

      {displayReading?.category && displayReading.category.length > 0 && <div className="reading-category-line"><Text type="secondary">设备 / 服务</Text>{displayReading.category.map((category) => <Tag key={category}>{category}</Tag>)}</div>}

      {displayReading ? <>
        <MilestoneList reading={displayReading} records={records} documents={documents} />
        <section className="reading-questions" aria-labelledby="reading-questions-heading">
          <div className="reading-section-header"><div><h2 id="reading-questions-heading">按 8 个问题查看要求</h2><Text type="secondary">每条要求都保留来源原文；包 / 标段和冲突不会合并隐藏。</Text></div><Text type="secondary">{allRequirements.length} 项要求</Text></div>
          <div className="reading-question-grid">{displayReading.sections.map((section) => <SectionBlock key={section.id} section={section} records={records} documents={documents} />)}</div>
          {displayReading.sections.length === 0 && <Empty description="尚未形成问题清单" />}
        </section>

        <section className="reading-complete-table" aria-labelledby="reading-complete-table-heading">
          <Collapse ghost items={[{ key: 'complete-table', label: <div className="reading-complete-table-label"><strong id="reading-complete-table-heading">完整要求表</strong><Text type="secondary">{allRequirements.length} 行；展开查看全部要求与来源</Text></div>, children: allRequirements.length === 0 ? <Text type="secondary">当前没有可展示的要求明细。</Text> : <div className="reading-table-wrap"><table><thead><tr><th>包 / 标段</th><th>要求</th><th>状态</th><th>来源</th></tr></thead><tbody>{allRequirements.map(({ requirement, sectionTitle }) => <RequirementRow key={requirement.id} requirement={requirement} sectionTitle={sectionTitle} />)}</tbody></table></div> }]}/>
        </section>

        {(displayReading.conflicts.length > 0 || displayReading.unresolved.length > 0 || displayReading.coverage.length > 0) && <Collapse className="reading-disclosures" ghost items={[{
          key: 'limits',
          label: '冲突、缺口与阅读覆盖',
          children: <div className="reading-disclosure-content">
            {displayReading.conflicts.length > 0 && <div><Text strong>冲突</Text><ul>{displayReading.conflicts.map((item) => <li key={item}>{item}</li>)}</ul></div>}
            {displayReading.unresolved.length > 0 && <div><Text strong>待确认</Text><ul>{displayReading.unresolved.map((item) => <li key={item}>{item}</li>)}</ul></div>}
            {displayReading.coverage.length > 0 && <div><Text strong>覆盖范围</Text><ul>{readableCoverage(displayReading.coverage).map((item, index) => <li key={`${item}-${index}`}>{item}</li>)}</ul></div>}
          </div>,
        }]} />}
      {(legacyProcurement || briefing) && <Collapse className="reading-legacy-materials" ghost items={[{ key: 'legacy-materials', label: '原始资料与完整物料', children: <ProcurementBriefing projectId={projectId} briefing={briefing} procurement={legacyProcurement} records={records} title={title} onRefresh={onRefresh ?? (() => undefined)} refreshing={refreshing} error={error} /> }]} />}
      </> : <div className="reading-empty"><Empty description="采购说明尚未建立" /><Text type="secondary">页面不会从采集截止时间或标题推断提交要求。</Text></div>}

      <details className="reading-original-records"><summary>原始资料入口（{recordCount} 条记录）</summary><div className="reading-record-list">{records.length === 0 ? <Text type="secondary">暂无原始记录。</Text> : records.map((record) => <div className="reading-record" key={record.id}><div><strong>{record.title || `记录 #${record.id}`}</strong><Text type="secondary">记录 #{record.id} · {record.parse_status || '已接入'}</Text></div>{record.original_url && <a href={record.original_url} target="_blank" rel="noreferrer">打开来源</a>}</div>)}</div></details>
    </div>
  )
}
