import { ArrowRightOutlined } from '@ant-design/icons'
import { Tag } from 'antd'
import { Link } from 'react-router-dom'
import type { ProjectSummary } from '../../types'
import { AiDecisionTag, AnalysisScopeTag, formatDate } from '../Common'
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

const researchStatusMeta: Record<NonNullable<ProjectSummary['research_summary']>['status'], { label: string; color?: string }> = {
  not_started: { label: '未开始' },
  running: { label: '补查中', color: 'processing' },
  completed: { label: '已调查', color: 'success' },
  limited: { label: '受限停止', color: 'warning' },
  failed: { label: '补查失败', color: 'error' },
  not_performed: { label: '未执行主动补查' },
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
  // Equipment is an extracted subject clue. Keep it separate from quantities and
  // delivery facts so a keyword cannot be mistaken for a confirmed purchase.
  const facts = procurementFacts(scopeText, deliveryFactText)
  const hasAiContext = project.ai_decision !== null || project.analysis_meta != null
  const researchMeta = project.research_summary ? researchStatusMeta[project.research_summary.status] : undefined
  const showResearchStatus = Boolean(researchMeta && !['not_started', 'not_performed'].includes(project.research_summary?.status ?? ''))
  const opportunitySignals = project.opportunity_signals ?? []
  const hasExplicitDemand = opportunitySignals.some((signal) => signal.kind === 'explicit_demand')
  const hasPotentialOpportunity = opportunitySignals.some((signal) => signal.kind === 'potential_opportunity') || project.gate_status === 'signal'

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
          {(hasAiContext || showResearchStatus || project.ai_status === 'stale' || hasExplicitDemand || hasPotentialOpportunity) && <div className="opportunity-feed-ai" aria-label="判断与调查状态">{hasExplicitDemand && <Tag color="blue">明确采购</Tag>}{hasPotentialOpportunity && <Tag color="cyan">潜在线索</Tag>}{project.ai_decision && <AiDecisionTag decision={project.ai_decision} analysisMeta={project.analysis_meta} />}{project.analysis_meta && <AnalysisScopeTag analysisMeta={project.analysis_meta} />}{showResearchStatus && researchMeta && <Tag color={researchMeta.color}>{researchMeta.label}</Tag>}{project.ai_status === 'stale' && <Tag color="warning">待复核</Tag>}</div>}
        </div>
        <p className="opportunity-feed-teaser" data-testid="opportunity-scope">{teaser}</p>

        {facts.length > 0 && (
          <div className="opportunity-feed-facts" aria-label="采购事实">
            {facts.map((fact) => <span className={fact.startsWith('数量 ') ? 'opportunity-quantity' : undefined} key={fact}>{fact}</span>)}
          </div>
        )}
        {project.equipment.length > 0 && <div className="opportunity-feed-equipment" aria-label="涉及设备词"><span>涉及设备词</span><span>{project.equipment.join('、')}</span></div>}

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
