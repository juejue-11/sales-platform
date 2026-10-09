import { Button, Spin } from 'antd'
import { LinkOutlined, ReloadOutlined, RobotOutlined } from '@ant-design/icons'
import type { ProjectSummary } from '../../types'
import type { LoadedProjectDetail } from '../../hooks/useOpportunityDesk'
import { AiDecisionTag, AiStatusTag, DecisionTag, formatDate, GateTag, kindLabel, sourceLabel } from '../Common'
import { cleanQualificationExcerpt, cleanScopeExcerpt } from './text'

interface OpportunityFeedItemProps {
  project: ProjectSummary
  detailState?: LoadedProjectDetail
  expanded: boolean
  onOpen: (id: number) => void
  onToggleOriginal: (id: number, updatedAt: string) => void
  onRetryOriginal: (id: number, updatedAt: string) => void
}

function fieldValue(value: string | null | undefined): string {
  return value?.trim() || '暂未识别，请展开原文核对'
}

function bodyValue(value: string | null | undefined): string {
  return value?.trim() || '原文正文未返回。'
}

function scopeValue(project: ProjectSummary): string {
  return cleanScopeExcerpt(project.briefing?.scope_excerpt) || '暂未识别，请展开原文核对'
}

function SourcePanel({ project, detailState, onRetry }: { project: ProjectSummary; detailState?: LoadedProjectDetail; onRetry: () => void }) {
  if (!detailState || detailState.updatedAt !== project.updated_at || detailState.status === 'loading') {
    return <div className="opportunity-source-state"><Spin size="small" /><span>正在读取原文…</span></div>
  }
  if (detailState.status === 'error') {
    const message = detailState.error instanceof Error ? detailState.error.message : '原文读取失败，请重试。'
    return <div className="opportunity-source-state opportunity-source-error"><span>{message}</span><Button size="small" icon={<ReloadOutlined />} onClick={onRetry}>重试</Button></div>
  }
  const records = detailState.detail?.records ?? []
  const fallbackUrl = project.briefing?.original_url
  return (
    <div className="opportunity-source-panel" data-testid="opportunity-source">
      {records.length > 0 ? records.map((record) => (
        <article className="opportunity-source-record" key={record.id}>
          <div className="opportunity-source-record-meta">
            <strong>{record.title || '原始记录'}</strong>
            <span>记录 #{record.id}</span>
          </div>
          <p>{bodyValue(record.body_text)}</p>
          {(record.original_url || record.canonical_url) && <a href={record.original_url || record.canonical_url || undefined} target="_blank" rel="noreferrer"><LinkOutlined aria-hidden="true" /> 打开来源页面</a>}
        </article>
      )) : (
        <p>{bodyValue(null)}</p>
      )}
      {records.length === 0 && fallbackUrl && <a href={fallbackUrl} target="_blank" rel="noreferrer"><LinkOutlined aria-hidden="true" /> 打开来源页面</a>}
    </div>
  )
}

export function OpportunityFeedItem({ project, detailState, expanded, onOpen, onToggleOriginal, onRetryOriginal }: OpportunityFeedItemProps) {
  const briefing = project.briefing
  const aiActions = project.ai_next_actions?.filter((action) => action.trim()) ?? []
  return (
    <article className="opportunity-feed-item" data-testid="opportunity-item">
      <header className="opportunity-feed-header">
        <div className="opportunity-feed-heading">
          <h3><button type="button" className="opportunity-feed-title" onClick={() => onOpen(project.id)}>{project.title || '未命名项目'}</button></h3>
          <div className="opportunity-feed-meta">
            <span>{project.buyer || '采购方未标明'}</span>
            <span>{kindLabel(project.kind)}</span>
            <span>{sourceLabel(project.source_id)}</span>
            <span>发布 {formatDate(project.published_at, true)}</span>
            <span>更新 {formatDate(project.updated_at, true)}</span>
          </div>
        </div>
        <div className="opportunity-feed-status" aria-label="项目状态">
          <GateTag status={project.gate_status} />
          {project.ai_status !== 'not_configured' && project.ai_status !== 'idle' && <AiStatusTag status={project.ai_status} />}
          {project.ai_decision && <AiDecisionTag decision={project.ai_decision} />}
          <DecisionTag status={project.decision.status} />
        </div>
      </header>

      <div className="opportunity-facts" aria-label="采购信息">
        <section className="opportunity-fact opportunity-fact-scope">
          <h3>采购范围</h3>
          <p data-testid="opportunity-scope">{scopeValue(project)}</p>
        </section>
        <div className="opportunity-fact-column">
          <section className="opportunity-fact opportunity-fact-qualification">
            <h3>资格条件</h3>
            <p data-testid="opportunity-qualification">{cleanQualificationExcerpt(briefing?.qualification_excerpt) || fieldValue(null)}</p>
          </section>
          <section className="opportunity-fact opportunity-fact-delivery">
            <h3>交付 / 服务</h3>
            <p>{fieldValue(briefing?.delivery_excerpt)}</p>
          </section>
        </div>
        <div className="opportunity-fact-bottom">
          <section className="opportunity-fact opportunity-fact-contact">
            <h3>联系方式</h3>
            <p>{fieldValue(briefing?.contact_excerpt)}</p>
          </section>
          <section className="opportunity-fact opportunity-fact-deadline">
            <h3>截止日期</h3>
            <time dateTime={project.deadline_at ?? undefined}>{formatDate(project.deadline_at, true)}</time>
          </section>
        </div>
      </div>

      {(project.ai_summary || project.demand_summary || aiActions.length > 0) && (
        <section className="opportunity-ai-summary" aria-label="AI摘要">
          <div className="opportunity-ai-heading"><RobotOutlined aria-hidden="true" /><strong>AI 摘要</strong></div>
          {project.ai_summary && <p>{project.ai_summary}</p>}
          {project.demand_summary && <p><span className="opportunity-ai-label">需求概括</span>{project.demand_summary}</p>}
          {aiActions.length > 0 && <div className="opportunity-ai-actions"><span className="opportunity-ai-label">下一步</span><ul>{aiActions.map((action) => <li key={action}>{action}</li>)}</ul></div>}
        </section>
      )}

      <footer className="opportunity-feed-footer">
        <span>{project.record_ids.length > 0 ? `来源记录 ${project.record_ids.join('、')}` : '暂无来源记录编号'}</span>
        <div className="opportunity-feed-actions">
          <Button type="link" onClick={() => onToggleOriginal(project.id, project.updated_at)}>{expanded ? '收起原文' : '展开原文'}</Button>
          <Button type="primary" ghost onClick={() => onOpen(project.id)}>进入研判</Button>
        </div>
      </footer>
      {expanded && <SourcePanel project={project} detailState={detailState} onRetry={() => onRetryOriginal(project.id, project.updated_at)} />}
    </article>
  )
}
