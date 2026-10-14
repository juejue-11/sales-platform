import { ArrowRightOutlined } from '@ant-design/icons'
import { Tag } from 'antd'
import { Link } from 'react-router-dom'
import type { ProjectSummary } from '../../types'
import { formatDate } from '../Common'
import { demandTypeMeta, demandTypeLabel, readingCompleteness, readingStageLabel, submissionMilestone } from '../procurement/reading'

interface OpportunityFeedItemProps {
  project: ProjectSummary
  onOpen?: (id: number) => void
}

function value(value: string | null | undefined, fallback: string): string {
  return value?.trim() || fallback
}

export function OpportunityFeedItem({ project, onOpen }: OpportunityFeedItemProps) {
  const reading = project.procurement_reading ?? null
  const demandType = project.demand_type ?? reading?.demand_type ?? 'unknown'
  const demandMeta = demandTypeMeta[demandType]
  const submission = submissionMilestone(reading)
  const categories = project.category?.length ? project.category : reading?.category ?? project.equipment
  const stage = project.procurement_stage ?? reading?.stage
  const readingNeedsProcessing = reading?.status === 'failed' || reading?.status === 'not_configured'
  const summary = readingNeedsProcessing
    ? (reading?.status === 'not_configured' ? '采购阅读服务待配置，请在业务配置中完成设置。' : '采购资料阅读失败，请在处理状态中查看原因。')
    : reading?.summary || '采购说明待核对，进入详情查看已读取资料。'

  return (
    <Link className="opportunity-feed-item" data-testid="opportunity-item" to={`/projects/${project.id}`} onClick={() => onOpen?.(project.id)} aria-label={`查看项目详情：${project.title || '未命名项目'}`}>
      <article className="opportunity-feed-card">
        <div className="opportunity-feed-heading">
          <div className="opportunity-feed-card-tags"><Tag color={demandMeta.color}>{demandTypeLabel(demandType)}</Tag>{project.is_following && project.followup_stage && <Tag color="blue">跟进：{readingStageLabel(project.followup_stage)}</Tag>}{reading?.stale && <Tag color="warning">资料待复核</Tag>}</div>
          <h3 className="opportunity-feed-title" title={project.title}>{project.title || '未命名项目'}</h3>
          <p className="opportunity-feed-buyer" title={value(project.buyer ?? reading?.buyer, '采购方待核对')}>{value(project.buyer ?? reading?.buyer, '采购方待核对')}</p>
        </div>
        <p className="opportunity-feed-teaser" data-testid="opportunity-scope">{summary}</p>
        <div className="opportunity-feed-facts" aria-label="采购关键信息">
          <span><small>设备 / 服务</small>{categories?.length ? categories.join('、') : '类别待核对'}</span>
          <span><small>地区</small>{value(project.region ?? reading?.region, '地区待核对')}</span>
          <span><small>阶段</small>{readingStageLabel(stage)}</span>
        </div>
        <footer className="opportunity-feed-footer">
          <span className="opportunity-feed-deadline"><small>提交截止</small><time dateTime={submission?.at ?? undefined}>{submission?.at ? formatDate(submission.at, true) : '提交截止待核对'}</time></span>
          <span className="opportunity-feed-completeness">{readingNeedsProcessing ? '资料处理状态待查看' : readingCompleteness(reading)}</span>
          <span className="opportunity-feed-detail-cue">查看详情 <ArrowRightOutlined aria-hidden="true" /></span>
        </footer>
      </article>
    </Link>
  )
}
