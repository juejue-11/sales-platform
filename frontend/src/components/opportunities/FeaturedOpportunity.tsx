import { CalendarOutlined } from '@ant-design/icons'
import { Link } from 'react-router-dom'
import type { ProjectSummary } from '../../types'
import { formatDate } from '../Common'
import { compactOpportunityTitle, isDeadlineWithinDays, isTodayShanghai } from './text'

interface FeaturedOpportunityProps {
  project: ProjectSummary | null
  onOpen: (id: number) => void
}

function deadlineCopy(value: string | null): { label: string; date: string } {
  if (!value) return { label: '截止日期未提供', date: '暂未识别，请展开原文核对。' }
  return { label: isTodayShanghai(value) ? '今天截止' : '截止日期', date: formatDate(value, true) }
}

export function FeaturedOpportunity({ project, onOpen }: FeaturedOpportunityProps) {
  if (!project || !isDeadlineWithinDays(project.deadline_at, 7)) return null
  const deadline = deadlineCopy(project.deadline_at)
  const title = compactOpportunityTitle(project.title) || '未命名采购项目'
  return (
    <Link
      className="opportunity-highlight"
      data-testid="featured-opportunity"
      to={`/projects/${project.id}`}
      onClick={() => onOpen(project.id)}
      aria-label={`查看临近截止项目：${title}`}
    >
      <span className="opportunity-highlight-label"><CalendarOutlined aria-hidden="true" /> 临近截止</span>
      <span className="opportunity-highlight-project">
        <strong>{title}</strong>
      </span>
      <time dateTime={project.deadline_at ?? undefined}>{deadline.label} {deadline.date}</time>
      <span className="opportunity-highlight-cue">查看详情</span>
    </Link>
  )
}
