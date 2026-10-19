import type { ReactNode } from 'react'
import { Button, Empty, Tag, Typography } from 'antd'
import { CalendarOutlined, CheckCircleFilled, EnvironmentOutlined, FileTextOutlined, FolderOpenOutlined, LinkOutlined, ProfileOutlined, ReloadOutlined, DownloadOutlined, FormOutlined, SafetyCertificateOutlined, AppstoreOutlined, ScheduleOutlined } from '@ant-design/icons'
import type { ProjectDetail, ProcurementReadingRequirement, ProcurementReadingSource } from '../../types'
import { formatDate } from '../Common'
import { readingCompleteness, readingStageLabel, sourceLabel, submissionMilestone } from './reading'
import { qualificationEmphasis, qualificationSections } from './qualificationPresentation'

const { Text } = Typography

type View = 'overview' | 'qualification' | 'schedule'
export function DetailCard({ title, icon, children, extra }: { title: string; icon: ReactNode; children: ReactNode; extra?: ReactNode }) {
  return <section className="project-card"><div className="project-card-heading"><h2><span className="project-section-icon">{icon}</span>{title}</h2>{extra}</div>{children}</section>
}

function Sources({ sources, detail, compact = false }: { sources: ProcurementReadingSource[]; detail: ProjectDetail; compact?: boolean }) {
  if (compact && sources?.length) return <div className="project-sources"><details><summary>来源依据（{sources.length}）</summary>{sources.map((source, index) => <div className="project-qualification-source" key={index}><span>{sourceLabel(source, detail.records, detail.procurement?.documents)}</span><blockquote>{source.quote || '原文待补充'}</blockquote></div>)}</details></div>
  return <div className="project-sources">{sources?.length ? sources.map((source, index) => <details key={index}><summary>{sourceLabel(source, detail.records, detail.procurement?.documents)}</summary><blockquote>{source.quote || '原文待补充'}</blockquote></details>) : <span>来源待补充</span>}</div>
}

function QualificationText({ text }: { text: string }) {
  return <>{text.split(qualificationEmphasis).map((part, index) => index % 2 ? <strong className={/[\d一二三四五六七八九十百]/.test(part) ? 'project-qualification-value' : undefined} key={index}>{part}</strong> : part)}</>
}

function QualificationContent({ detail, requirements }: { detail: ProjectDetail; requirements: ProcurementReadingRequirement[] }) {
  if (!requirements.length) return <p className="project-empty-note">资格要求待补充，请查阅项目公告和招标文件。</p>
  return <div className="project-qualification-content">{requirements.map((requirement, index) => <div className="project-qualification-paragraph" key={`${requirement.id}-${index}`}>
      {!requirement.text && requirement.explanation && <span className="project-qualification-note">说明 · 原文待补充</span>}
      <p className="project-qualification-text"><span className="project-qualification-copy"><QualificationText text={requirement.text || requirement.explanation || '要求待补充'} /></span>{requirement.lot != null && requirement.lot !== '' && <Tag>标段 {requirement.lot}</Tag>}{requirement.status && !['confirmed', 'extracted'].includes(requirement.status) && <Tag color={requirement.status === 'conflict' ? 'error' : 'warning'}>{requirement.status === 'conflict' ? '来源冲突' : '待核对'}</Tag>}</p>
      {requirement.text && requirement.explanation && <p className="project-qualification-explanation"><QualificationText text={requirement.explanation} /></p>}
    </div>)}<Sources sources={requirements.flatMap(requirement => requirement.sources || [])} detail={detail} compact /></div>
}

function Timeline({ detail, expanded = false }: { detail: ProjectDetail; expanded?: boolean }) {
  const reading = detail.procurement_reading
  const milestones = reading?.milestones ?? []
  const emptyNote = reading?.status === 'pending' ? '正在整理采购资料，时间节点尚未生成。'
    : reading?.status === 'failed' ? '采购资料处理失败，请到采购内容查看处理状态。'
    : reading?.unresolved?.some(note => /节点.*(?:日期|原文)/.test(note)) ? '时间节点未通过校验，请到采购内容核对公告原文。'
    : '当前资料未提供可确认的时间节点，请核对公告原文。'
  if (!milestones.length) return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={emptyNote} />
  return <ol className={`project-timeline${expanded ? ' project-timeline-expanded' : ''}`}>{milestones.map((milestone, index) => <li key={milestone.id} className={index === 0 ? 'timeline-first' : ''}><span className="timeline-dot" /><strong>{milestone.label}</strong><time>{milestone.at ? formatDate(milestone.at, true) : '时间待核对'}</time>{expanded && <Sources sources={milestone.sources} detail={detail} />}</li>)}</ol>
}

export function ProcurementOverview({ detail, view = 'overview', onTab }: { detail: ProjectDetail; view?: View; onTab: (key: string) => void }) {
  const reading = detail.procurement_reading
  const procurement = detail.procurement
  const submission = submissionMilestone(reading)
  const requirements = qualificationSections(reading?.sections ?? [])
  const qualificationItems = requirements.flatMap(section => section.requirements)
  const qualificationRequirements = qualificationItems.length ? qualificationItems : detail.briefing?.qualification_excerpt ? [{ id: 'qualification-excerpt', text: detail.briefing.qualification_excerpt, sources: [] }] : []
  if (view === 'schedule') return <DetailCard title="关键时间节点" icon={<CalendarOutlined />}><Timeline detail={detail} expanded />{reading?.sections.filter(section => section.id === 'schedule').map(section => <div key={section.id} className="project-requirements">{section.requirements.map(item => <article key={item.id}><strong>{item.text || item.explanation || '要求待补充'}</strong><Sources sources={item.sources} detail={detail} /></article>)}</div>)}</DetailCard>
  if (view === 'qualification') return <DetailCard title="资格要求" icon={<SafetyCertificateOutlined />}><QualificationContent detail={detail} requirements={qualificationRequirements} /></DetailCard>
  const facts = [
    { label: '需求类型', value: reading?.demand_type === 'lead' ? '潜在线索' : reading?.demand_type === 'confirmed' ? '明确采购' : '待核对', icon: <AppstoreOutlined /> },
    { label: '采购类型', value: (detail.category?.length ? detail.category : reading?.category?.length ? reading.category : detail.equipment).join(' / ') || '设备 / 服务待核对', icon: <ProfileOutlined /> },
    { label: '采购阶段', value: readingStageLabel(reading?.stage ?? detail.procurement_stage), icon: <FileTextOutlined />, tone: 'green' },
    { label: '地区', value: detail.region || reading?.region || '待核对', icon: <EnvironmentOutlined /> },
    { label: '提交截止', value: submission?.at ? formatDate(submission.at, true) : '待核对', icon: <CalendarOutlined />, tone: 'orange' },
    { label: '资料完整性', value: readingCompleteness(reading), icon: <CheckCircleFilled />, tone: 'green' },
  ]
  return <div className="project-overview">
    <DetailCard title="采购说明" icon={<ProfileOutlined />}>
      <p className="project-description">{reading?.summary || procurement?.scope_summary || detail.briefing?.scope_excerpt || '采购说明尚未建立，请查看采购内容或更新公开资料。'}</p>
      {detail.briefing?.delivery_excerpt && <p className="project-description">{detail.briefing.delivery_excerpt}</p>}
      {(reading?.stale || reading?.conflicts?.length || reading?.unresolved?.length) ? <div className="project-review-note"><SafetyCertificateOutlined /> {reading.stale ? '资料已更新，当前说明待复核。' : `有 ${reading.conflicts.length + reading.unresolved.length} 项信息待核对`}<Button type="link" size="small" onClick={() => onTab('procurement')}>查看详情</Button></div> : null}
      <div className="project-fact-grid">{facts.map(fact => <div className="project-fact" key={fact.label}><span className={`project-fact-icon ${fact.tone || ''}`}>{fact.icon}</span><div><span>{fact.label}</span><strong>{fact.value}</strong></div></div>)}</div>
    </DetailCard>
    <DetailCard title="关键时间节点" icon={<ScheduleOutlined />} extra={<Button type="link" size="small" onClick={() => onTab('schedule')}>查看全部</Button>}>
      <Timeline detail={detail} />
      <div className="project-table-heading"><h3><span className="project-section-icon"><FileTextOutlined /></span>采购内容{procurement?.items?.length ? `（${procurement.items.length} 项）` : ''}</h3><Button size="small" onClick={() => onTab('procurement')}>查看详细技术规格 →</Button></div>
      <div className="project-table-scroll"><table className="project-items-table"><thead><tr><th>序号</th><th>采购内容</th><th>数量</th><th>交货地点</th><th>技术要求</th></tr></thead><tbody>{procurement?.items?.length ? procurement.items.slice(0, 6).map((item, index) => <tr key={index}><td>{String(index + 1).padStart(3, '0')}</td><td>{item.name || '待核对'}</td><td>{item.quantity ? `${item.quantity} ${item.unit || ''}` : '待核对'}</td><td>{item.delivery_location || procurement.delivery_location || '待核对'}</td><td>{item.specification || item.standard || '详见采购内容'}</td></tr>) : <tr><td colSpan={5}>{detail.briefing?.scope_excerpt || '暂无已确认的采购明细，请查看采购内容。'}</td></tr>}</tbody></table></div>
    </DetailCard>
    <DetailCard title="资格与参与要求" icon={<SafetyCertificateOutlined />} extra={<Button type="link" size="small" onClick={() => onTab('qualification')}>查看要求</Button>}><QualificationContent detail={detail} requirements={qualificationRequirements} /></DetailCard>
  </div>
}

export function ProjectInfoRail({ detail, refreshing, onRefresh, onFollowup, onAttachments, children, compact = false }: { detail: ProjectDetail; refreshing: boolean; onRefresh: () => void; onFollowup: () => void; onAttachments: () => void; children?: ReactNode; compact?: boolean }) {
  const reading = detail.procurement_reading
  const stage = detail.followup?.stage ?? detail.followup_stage
  const following = detail.followup?.is_following ?? detail.is_following
  const index = stage === 'finished' || detail.gate_status === 'closed' ? 3 : following ? 2 : detail.ai_status === 'succeeded' ? 1 : 0
  const status = index === 3 ? '已结束' : following ? detail.followup?.disposition === 'paused' ? '暂缓' : detail.followup?.disposition === 'abandoned' ? '已放弃' : '跟进中' : detail.needs_review || reading?.stale || reading?.unresolved?.length || reading?.conflicts?.length || detail.gate_status === 'review' ? '待核实' : '待评估'
  const originalUrl = detail.briefing?.original_url || detail.records.find(record => record.original_url)?.original_url
  const exportProject = () => {
    const blob = new Blob([JSON.stringify(detail, null, 2)], { type: 'application/json;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement('a'); anchor.href = url; anchor.download = `项目资料-${detail.id}.json`; anchor.click()
    window.setTimeout(() => URL.revokeObjectURL(url), 1000)
  }
  if (compact) return <aside className="project-info-rail project-info-rail-compact" aria-label="项目信息与相关资料">
    <section className="project-reading-info">
      <h2>项目信息</h2>
      <dl className="project-info-list">
        {detail.published_at && <div><dt>发布时间</dt><dd>{formatDate(detail.published_at, true)}</dd></div>}
      </dl>
      <div className="project-reading-original">{originalUrl ? <a href={originalUrl} target="_blank" rel="noreferrer"><FileTextOutlined />采购公告原文<LinkOutlined /></a> : <span className="project-link-unavailable">项目公告链接待补充</span>}</div>
    </section>
    <section className="project-reading-attachments" aria-labelledby="reading-attachments-heading">
      <h2 id="reading-attachments-heading">相关附件<span>{detail.procurement?.documents?.length || 0}</span></h2>
      <div className="project-reading-attachment-list">{detail.procurement?.documents?.length ? detail.procurement.documents.map(document => document.url ? <a key={document.id} href={document.url} target="_blank" rel="noreferrer"><FileTextOutlined /><span>{document.name || '采购附件'}</span><LinkOutlined /></a> : <button type="button" key={document.id} onClick={onAttachments}><FileTextOutlined /><span>{document.name || '采购附件'}</span></button>) : <p>暂无已获取附件</p>}</div>
      <Button type="link" size="small" icon={<FolderOpenOutlined />} onClick={onAttachments}>查看 / 补充附件</Button>
    </section>
  </aside>
  return <aside className="project-info-rail" aria-label="项目状态与信息">
    <DetailCard title="项目状态" icon={null} extra={<Tag color={index === 3 ? 'default' : following ? 'blue' : 'orange'}>{status}</Tag>}>
      <ol className="project-progress">{['信息收集', '初步评估', '跟进中', '已结束'].map((label, step) => <li className={step <= index ? 'is-reached' : ''} key={label}><span>{step === index ? <i /> : null}</span><strong>{label}</strong>{step === 0 && <time>{formatDate(detail.published_at)}</time>}</li>)}</ol>
      <Button className="project-refresh-button" type="primary" block icon={<ReloadOutlined />} loading={refreshing} onClick={onRefresh}>更新公开资料</Button>
      <div className="project-rail-buttons"><Button icon={<FormOutlined />} onClick={onFollowup}>{following ? '查看跟进任务' : '生成跟进任务'}</Button><Button icon={<DownloadOutlined />} onClick={exportProject}>导出项目资料</Button></div>
      {children && <div className="project-analysis-actions">{children}</div>}
    </DetailCard>
    <DetailCard title="项目信息" icon={<ProfileOutlined />}><dl className="project-info-list">
      <div><dt>项目编号</dt><dd><Text copyable={{ text: String(detail.id) }}>#{detail.id}</Text></dd></div>
      <div><dt>采购方</dt><dd>{detail.buyer || reading?.buyer || '待核对'}</dd></div>
      <div><dt>需求类型</dt><dd>{reading?.demand_type === 'lead' ? '潜在线索' : reading?.demand_type === 'confirmed' ? '明确采购' : '待核对'}</dd></div>
      <div><dt>采购阶段</dt><dd>{readingStageLabel(reading?.stage ?? detail.procurement_stage)}</dd></div>
      <div><dt>地区</dt><dd>{detail.region || reading?.region || '待核对'}</dd></div>
      <div><dt>信息来源</dt><dd>{detail.source_id || '待核对'}</dd></div>
      <div><dt>发布时间</dt><dd>{formatDate(detail.published_at, true)}</dd></div>
      <div><dt>资料完整性</dt><dd><CheckCircleFilled className={reading?.status === 'complete' && !reading.stale ? 'project-complete' : 'project-incomplete'} /> {readingCompleteness(reading)}</dd></div>
    </dl></DetailCard>
    <DetailCard title="相关链接" icon={<LinkOutlined />}><div className="project-related-links">{originalUrl ? <a href={originalUrl} target="_blank" rel="noreferrer"><span className="project-link-icon"><FileTextOutlined /></span><span><strong>项目公告原文</strong><small>查看原始采购公告</small></span><LinkOutlined /></a> : <div className="project-link-unavailable">项目公告链接待补充</div>}<button onClick={onAttachments}><span className="project-link-icon orange"><FolderOpenOutlined /></span><span><strong>招标文件（如有）</strong><small>查看 / 补充相关文件</small></span><LinkOutlined /></button></div></DetailCard>
  </aside>
}
