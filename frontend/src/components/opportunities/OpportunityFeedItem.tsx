import { ArrowRightOutlined } from '@ant-design/icons'
import { Link } from 'react-router-dom'
import type { ProjectSummary } from '../../types'
import { formatDate } from '../Common'
import { cleanOpportunityTitle, procurementFacts, procurementTeaser } from './text'

interface OpportunityFeedItemProps {
  project: ProjectSummary
  onOpen?: (id: number) => void
}

function buyerValue(value: string | null | undefined): string {
  return value?.trim() || '采购方未标明'
}

function deadlineValue(value: string | null): string {
  return value ? formatDate(value, true) : '截止日期未提供'
}

/**
 * A catalogue teaser. The Link is the card so pointer, keyboard, and
 * ctrl-click navigation all have the same destination.
 */
export function OpportunityFeedItem({ project, onOpen }: OpportunityFeedItemProps) {
  const briefing = project.briefing
  const title = cleanOpportunityTitle(project.title) || '未命名项目'
  const teaser = procurementTeaser(briefing?.scope_excerpt, project.title)
    || procurementTeaser(briefing?.delivery_excerpt, project.title)
    || '具体采购范围暂未识别，进入详情核对原文。'
  const facts = procurementFacts(briefing?.scope_excerpt, briefing?.delivery_excerpt, project.equipment)

  return (
    <Link
      className="opportunity-feed-item"
      data-testid="opportunity-item"
      to={`/projects/${project.id}`}
      onClick={() => onOpen?.(project.id)}
      aria-label={`查看项目详情：${title}`}
    >
      <article className="opportunity-feed-card">
        <div className="opportunity-feed-heading">
          <h3 className="opportunity-feed-title">{title}</h3>
          <p className="opportunity-feed-buyer" title={buyerValue(project.buyer)}>{buyerValue(project.buyer)}</p>
        </div>
        <p className="opportunity-feed-teaser" data-testid="opportunity-scope">{teaser}</p>

        {facts.length > 0 && (
          <div className="opportunity-feed-facts" aria-label="采购事实">
            {facts.map((fact) => <span key={fact}>{fact}</span>)}
          </div>
        )}

        <footer className="opportunity-feed-footer">
          <span className="opportunity-feed-deadline">
            <small>投标截止</small>
            <time dateTime={project.deadline_at ?? undefined}>{deadlineValue(project.deadline_at)}</time>
          </span>
          <span className="opportunity-feed-detail-cue">查看详情 <ArrowRightOutlined aria-hidden="true" /></span>
        </footer>
      </article>
    </Link>
  )
}
