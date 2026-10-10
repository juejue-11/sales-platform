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

function uniformItemDelivery(items: NonNullable<ProjectSummary['procurement']>['items'], field: 'delivery_period' | 'delivery_location'): string {
  const values = Array.from(new Set(items.map((item) => item[field]?.trim()).filter(Boolean)))
  return values.length === 1 ? values[0] : ''
}

/**
 * A catalogue teaser. The Link is the card so pointer, keyboard, and
 * ctrl-click navigation all have the same destination.
 */
export function OpportunityFeedItem({ project, onOpen }: OpportunityFeedItemProps) {
  const briefing = project.briefing
  const procurement = project.procurement
  const hasParsedItems = Boolean(procurement?.items?.length)
  const title = compactOpportunityTitle(project.title) || '未命名项目'
  const scopeText = procurement?.scope_summary || briefing?.scope_excerpt
  const deliveryPeriod = procurement?.delivery_period || (procurement?.items ? uniformItemDelivery(procurement.items, 'delivery_period') : '')
  const deliveryLocation = procurement?.delivery_location || (procurement?.items ? uniformItemDelivery(procurement.items, 'delivery_location') : '')
  const deliveryText = deliveryPeriod || deliveryLocation || briefing?.delivery_excerpt
  const deliveryFactText = hasParsedItems
    ? [
        deliveryPeriod && `交货期：${deliveryPeriod}`,
        deliveryLocation && `交货地点：${deliveryLocation}`,
      ].filter(Boolean).join('；')
    : deliveryText
  const teaser = procurementTeaser(scopeText, project.title)
    || procurementTeaser(deliveryText, project.title)
    || '采购范围尚未从正文或附件核对，进入详情查看来源。'
  const facts = procurementFacts(scopeText, deliveryFactText, hasParsedItems ? [] : project.equipment)

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
