import { useEffect, useState } from 'react'
import { Button, Dropdown, Tag, Tooltip, message } from 'antd'
import { EllipsisOutlined, RobotOutlined, StarFilled, StarOutlined } from '@ant-design/icons'
import { Link, useLocation } from 'react-router-dom'
import { api } from '../../api'
import type { ProjectSummary } from '../../types'
import { demandTypeMeta, demandTypeLabel, followupStageLabels, readingCompleteness, readingStageLabel, submissionMilestone } from '../procurement/reading'

interface OpportunityFeedItemProps {
  project: ProjectSummary
  onOpen?: (id: number) => void
  onFollowupChange?: () => void
}

function dateText(value: string | null | undefined, withTime = false): string {
  if (!value) return '待核对'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '待核对'
  const parts = new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit', ...(withTime ? { hour: '2-digit', minute: '2-digit', hour12: false } as const : {}) }).format(date)
  return parts
}

function remainingTime(value: string | null | undefined): string {
  if (!value) return '截止时间待核对'
  const remaining = new Date(value).getTime() - Date.now()
  if (!Number.isFinite(remaining)) return '截止时间待核对'
  if (remaining < 0) return '已截止'
  const hours = Math.ceil(remaining / 3_600_000)
  return hours <= 24 ? `剩余 ${hours} 小时` : `剩余 ${Math.ceil(remaining / 86_400_000)} 天`
}

export function OpportunityFeedItem({ project, onOpen, onFollowupChange }: OpportunityFeedItemProps) {
  const location = useLocation()
  const [following, setFollowing] = useState(Boolean(project.is_following))
  const [saving, setSaving] = useState(false)
  useEffect(() => setFollowing(Boolean(project.is_following)), [project.is_following])
  const reading = project.procurement_reading
  const demandType = project.demand_type ?? reading?.demand_type ?? 'unknown'
  const submission = submissionMilestone(reading)
  const categories = project.category?.length ? project.category : reading?.category?.length ? reading.category : project.equipment
  const region = project.region ?? reading?.region
  const stage = readingStageLabel(project.procurement_stage ?? reading?.stage)
  const completeness = reading?.stale ? '资料待复核' : reading?.status === 'complete' ? '已覆盖当前资料' : reading?.status === 'partial' ? '部分已覆盖' : reading?.status === 'failed' ? '资料读取失败' : reading?.status === 'not_configured' ? '阅读待配置' : '资料待整理'
  const completenessTone = reading?.stale ? 'warning' : reading?.status === 'complete' || reading?.status === 'partial' ? 'covered' : reading?.status === 'failed' ? 'danger' : 'neutral'
  const linkState = { from: location.pathname + location.search }
  const remember = () => onOpen?.(project.id)
  const toggleFollowing = async () => {
    if (saving) return
    setSaving(true)
    try {
      const next = await api.updateFollowup(project.id, { is_following: !following })
      setFollowing(Boolean(next.is_following))
      message.success(next.is_following ? '已加入项目跟进' : '已取消跟进')
      onFollowupChange?.()
    } catch (error) { message.error(error instanceof Error ? error.message : '跟进状态保存失败，请重试') }
    finally { setSaving(false) }
  }

  return <article className="opportunity-feed-item catalog-project-card" data-testid="opportunity-item">
    <div className="catalog-project-art" aria-hidden="true"><ProcurementArtwork variant={project.id % 3} /></div>
    <div className="catalog-project-info">
      <div className="catalog-project-badges"><Tag color={demandTypeMeta[demandType]?.color}>{demandTypeLabel(demandType)}</Tag>{following && <Tag className="catalog-following-tag">{followupStageLabels[project.followup_stage ?? 'evaluating'] ?? '跟进中'}</Tag>}</div>
      <h2 className="catalog-project-title"><Link to={`/projects/${project.id}`} state={linkState} onClick={remember} title={project.title}>{project.title || '未命名项目'}</Link></h2>
      <p className="catalog-project-buyer" title={project.buyer ?? reading?.buyer ?? ''}>{project.buyer ?? reading?.buyer ?? '采购方待核对'}</p>
      <div className="catalog-project-tags">{(categories?.length ? categories.slice(0, 2) : ['设备 / 服务待核对']).map(category => <span key={category} title={category}>{category}</span>)}{region && <span title={region}>{region}</span>}<span className="catalog-stage-tag" title={stage}>{stage}</span></div>
    </div>
    <div className="catalog-project-meta">
      <div className="catalog-project-dates"><div><span>发布时间</span><time dateTime={project.published_at ?? undefined}>{dateText(project.published_at)}</time></div><div className="catalog-submission"><span>提交截止</span><Tooltip title={submission?.at ? dateText(submission.at, true) : '当前资料中暂无可确认的提交截止时间'}><time dateTime={submission?.at ?? undefined}>{submission?.at ? dateText(submission.at) : '待核对'}</time></Tooltip><strong className={submission?.at && new Date(submission.at).getTime() > Date.now() ? 'is-upcoming' : ''}>{remainingTime(submission?.at)}</strong></div><div className="catalog-completeness"><span>资料完整性</span><Tooltip title={readingCompleteness(reading)}><span className={`catalog-completeness-tag ${completenessTone}`}>{completeness}</span></Tooltip></div></div>
      <div className="catalog-project-actions"><Link className="catalog-analysis-button" to={`/projects/${project.id}?tab=analysis`} state={linkState} onClick={remember}><RobotOutlined aria-hidden="true" />AI 研判</Link><Link className="catalog-detail-button" to={`/projects/${project.id}`} state={linkState} onClick={remember}>查看详情</Link></div>
    </div>
    <div className="catalog-card-tools"><Tooltip title={following ? '取消跟进' : '加入跟进'}><Button className={`catalog-follow-button${following ? ' is-following' : ''}`} type="text" size="small" icon={following ? <StarFilled /> : <StarOutlined />} loading={saving} aria-label={`${following ? '取消跟进' : '加入跟进'}：${project.title}`} aria-pressed={following} onClick={() => void toggleFollowing()} /></Tooltip><Dropdown trigger={['click']} menu={{ items: [{ key: 'copy', label: '复制项目链接' }, ...(project.briefing?.original_url ? [{ key: 'source', label: '查看公告原文' }] : [])], onClick: ({ key }) => {
      if (key === 'source' && project.briefing?.original_url) window.open(project.briefing.original_url, '_blank', 'noopener,noreferrer')
      if (key === 'copy') void navigator.clipboard.writeText(new URL(`/projects/${project.id}`, window.location.origin).href).then(() => message.success('项目链接已复制')).catch(() => message.error('复制失败，请进入详情后复制地址栏链接'))
    } }}><Button className="catalog-project-menu" type="text" size="small" icon={<EllipsisOutlined />} aria-label={`项目操作：${project.title}`} /></Dropdown></div>
  </article>
}

function ProcurementArtwork({ variant }: { variant: number }) {
  return <svg viewBox="0 0 120 120" fill="none"><path fill={variant === 1 ? '#bad4e5' : '#a4d5ec'} d="M0 0h120v120H0z" /><circle cx="92" cy="22" r="13" fill="#e8f5fb" opacity=".75" /><path d="m0 72 24-28 18 11 25-24 33 27 20-7v69H0Z" fill="#728f98" /><path d="m0 83 29-18 25 12 27-25 39 18v50H0Z" fill="#a3aaa4" /><path d="M0 93c30-10 60-9 120-1v28H0Z" fill="#bcae96" /><path d="m5 107 38-5 20 4 45-2M1 116l66-2 50 1" stroke="#d0c3ab" strokeWidth="3" />{variant === 1 ? <><path d="m-6 43 126 33v13L-6 56Z" fill="#314b58" /><path d="m-6 43 126 33" stroke="#e0e5e5" strokeWidth="3" /><path d="m11 60-4 50m39-41-3 40m40-29-2 31m31-22 2 24" stroke="#526b73" strokeWidth="5" /><path d="m11 62 34 45m0-36L9 106m73-26 30 28m-1-20-28 21" stroke="#6c8087" strokeWidth="2" /></> : <><path d="M24 84h51v13H24Z" fill="#e9a23a" /><path d="M34 86V61h20l12 25H34Z" fill="#ffbf42" /><path d="M39 65h12l8 15H39Z" fill="#335c6b" /><path d="m61 67 17-37 12 4 16 41-7 3-18-33-12 27Z" fill="#ffc14b" stroke="#d6942c" strokeWidth="2" /><path d="m76 31 6 4-18 38m21-35 16 35" stroke="#fce1a4" strokeWidth="3" /><path d="m98 73 14 2 6 15-20-5Z" fill="#445b61" /><rect x="21" y="94" width="58" height="15" rx="7.5" fill="#344a4e" /><rect x="27" y="98" width="46" height="7" rx="3.5" fill="#758283" /><path d="M33 99v5m9-5v5m10-5v5m10-5v5" stroke="#2f474e" strokeWidth="3" /></>}<path d="M0 0h120v120H0z" stroke="white" strokeOpacity=".28" strokeWidth="2" /></svg>
}
