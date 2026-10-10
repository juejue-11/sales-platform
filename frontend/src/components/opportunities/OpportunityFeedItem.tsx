import { ArrowRightOutlined } from '@ant-design/icons'
import { Link } from 'react-router-dom'
import type { ProjectSummary } from '../../types'
import { formatDate } from '../Common'
import { compactOpportunityTitle, procurementFacts, procurementTeaser } from './text'

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
  const procurement = project.procurement
  const title = compactOpportunityTitle(project.title) || '未命名项目'
  const scopeText = procurement?.scope_summary || briefing?.scope_excerpt
  const deliveryText = procurement?.delivery_period || procurement?.delivery_location || briefing?.delivery_excerpt
  const teaser = procurementTeaser(scopeText, project.title)
    || procurementTeaser(deliveryText, project.title)
    || '采购范围尚未从正文或附件核对，进入详情查看来源。'
  const facts = procurementFacts(scopeText, deliveryText, project.equipment)

  return (
    <Link
      className="opportunity-feed-item"
      data-testid="opportunity-item"
      to={`/projects/${project.id}`}
      onClick={() => onOpen?.(project.id)}
      aria-label={`查看项目详情：${project.title || title}`}
    >
      <article className="opportunity-feed-card">
        <div className="opportunity-feed-heading">
          <h3 className="opportunity-feed-title" title={project.title}>{title}</h3>
          <p className="opportunity-feed-buyer" title={buyerValue(project.buyer)}>{buyerValue(project.buyer)}</p>
        </div>
        <p className="opportunity-feed-teaser" data-testid="opportunity-scope">{teaser}</p>

        {facts.length > 0 && (
          <div className="opportunity-feed-facts" aria-label="采购事实">
            {facts.map((fact) => <span className={fact.startsWith('数量 ') ? 'opportunity-quantity' : undefined} key={fact}>{fact}</span>)}
          </div>
        )}

        <footer className="opportunity-feed-footer">
          <span className="opportunity-feed-deadline">
            <small>截止时间</small>
            <time dateTime={project.deadline_at ?? undefined}>{deadlineValue(project.deadline_at)}</time>
          </span>
          <span className="opportunity-feed-detail-cue">查看详情 <ArrowRightOutlined aria-hidden="true" /></span>
        </footer>
      </article>
    </Link>
  )
}
