import { Button, Skeleton } from 'antd'
import { ArrowRightOutlined, CalendarOutlined, SafetyCertificateOutlined } from '@ant-design/icons'
import type { ProjectSummary } from '../../types'
import { formatDate, kindLabel, sourceLabel } from '../Common'
import { cleanOpportunityTitle, cleanScopeExcerpt, isTodayShanghai, qualificationHighlights } from './text'

interface FeaturedOpportunityProps {
  project: ProjectSummary | null
  basis: string
  loading: boolean
  onOpen: (id: number) => void
}

function missing(value: string | null | undefined): string {
  return value?.trim() || '暂未识别，请展开原文核对。'
}

function scopeFor(project: ProjectSummary): string {
  const cleaned = cleanScopeExcerpt(project.briefing?.scope_excerpt, project.title)
  const text = cleaned || missing(null)
  return text.length > 240 ? `${text.slice(0, 240)}…` : text
}

function deadlineCopy(value: string | null): { label: string; date: string } {
  if (!value) return { label: '截止日期未提供', date: '暂未识别，请展开原文核对。' }
  return { label: isTodayShanghai(value) ? '今天截止' : '截止日期', date: formatDate(value, true) }
}

export function FeaturedOpportunity({ project, basis, loading, onOpen }: FeaturedOpportunityProps) {
  const deadline = project ? deadlineCopy(project.deadline_at) : null
  return (
    <section className="opportunity-feature" data-testid="featured-opportunity" aria-labelledby="featured-opportunity-heading">
      {loading && !project ? (
        <div className="opportunity-feature-loading"><Skeleton active paragraph={{ rows: 3 }} /></div>
      ) : project ? (
        <>
          <div className="opportunity-feature-main">
            <div className="opportunity-feature-kicker"><span>最新采购</span><span>{basis}</span></div>
            <h1 id="featured-opportunity-heading">{cleanOpportunityTitle(project.title) || '未命名采购项目'}</h1>
            <div className="opportunity-feature-meta">
              <span>{project.buyer || '采购方未标明'}</span>
              <span>{kindLabel(project.kind)}</span>
              <span>{sourceLabel(project.source_id)}</span>
            </div>
            {deadline && <div className="opportunity-feature-mobile-deadline"><span className="opportunity-label">截止时间</span><strong>{deadline.label}</strong><time dateTime={project.deadline_at ?? undefined}>{deadline.date}</time></div>}
            <div className="opportunity-feature-scope">
              <span className="opportunity-label">采购范围</span>
              <p>{scopeFor(project)}</p>
            </div>
            {project.briefing?.delivery_excerpt && <div className="opportunity-feature-delivery"><span className="opportunity-label">交付要求</span><p>{project.briefing.delivery_excerpt}</p></div>}
            <Button className="opportunity-feature-action" type="primary" onClick={() => onOpen(project.id)} icon={<ArrowRightOutlined />}>
              进入研判
            </Button>
          </div>
          <aside className="opportunity-feature-aside" aria-label="采购截止与资格要求">
            <div className="opportunity-feature-fact opportunity-feature-aside-deadline">
              <div className="opportunity-feature-deadline-block">
                <span className="opportunity-label"><CalendarOutlined aria-hidden="true" /> {deadline?.label ?? '截止日期'}</span>
                <time dateTime={project.deadline_at ?? undefined}>{deadline?.date ?? '暂未识别，请展开原文核对。'}</time>
              </div>
            </div>
            <div className="opportunity-feature-fact">
              <span className="opportunity-label"><SafetyCertificateOutlined aria-hidden="true" /> 参与要求</span>
              <p>{qualificationHighlights(project.briefing?.qualification_excerpt) || missing(null)}</p>
            </div>
            <div className="opportunity-feature-fact opportunity-feature-published">
              <span className="opportunity-label">发布时间</span>
              <time dateTime={project.published_at ?? undefined}>{formatDate(project.published_at, true)}</time>
            </div>
          </aside>
        </>
      ) : (
        <div className="opportunity-feature-empty">
          <span className="opportunity-label">当前采购</span>
          <h1 id="featured-opportunity-heading">等待采购数据</h1>
          <p>尚未读取到候选或待核实项目，数据进入后会在这里展示最新采购需求。</p>
        </div>
      )}
    </section>
  )
}
