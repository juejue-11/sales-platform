import type { FollowupEvent } from '../../types'
import { followupStageLabels } from '../procurement/reading'

const labels: Record<string, string> = {
  created: '建立跟进预览', followup_activation_changed: '变更跟进选择',
  stage_changed: '变更办理阶段', disposition_changed: '变更跟进意向',
  owner_changed: '变更负责人', owner_preserved: '保留原负责人',
  next_action_changed: '更新下一步', note_changed: '更新备注', outcome_changed: '记录办理结果',
  submitted: '确认实际提交', source_changed: '采购资料有变化，需复核',
  source_version_changed: '资料版本已更新', plan_generated: '生成计划预览',
  plan_adopted: '采用跟进计划', plan_rejected: '计划待核对',
  task_updated: '更新办理任务', material_updated: '更新材料状态', followup_merged: '归并项目跟进记录',
}

const values: Record<string, string> = {
  ...followupStageLabels, following: '跟进中', paused: '暂缓', abandoned: '放弃',
  won: '已成交', lost: '未成交', other: '其他结果',
  todo: '待办理', doing: '办理中', done: '已完成', skipped: '已跳过',
  not_prepared: '未准备', preparing: '准备中', provided: '已提供', pending_check: '待核对', needs_supplement: '需补充',
}

function text(value: unknown): string {
  return typeof value === 'string' ? values[value] ?? value : ''
}

export function followupEventText(event: FollowupEvent): string {
  if (event.text || event.message) return event.text || event.message || ''
  const payload = event.payload ?? {}
  const details: string[] = []
  if (event.type === 'stage_changed' || event.type === 'disposition_changed') {
    details.push(`${text(payload.from)} → ${text(payload.to)}`)
  }
  if (event.type === 'followup_activation_changed') details.push(payload.is_following ? '加入我的跟进' : '移出我的跟进')
  for (const [key, label] of [['owner', '负责人'], ['next_action', '下一步'], ['reason', '理由'],
    ['outcome', '结果'], ['submission_channel', '提交渠道'], ['submitted_at', '提交时间']] as const) {
    if (text(payload[key])) details.push(`${label}：${text(payload[key])}`)
  }
  const changes = payload.changes
  if (changes && typeof changes === 'object') {
    const patch = changes as Record<string, unknown>
    for (const [key, label] of [['status', '状态'], ['completion_note', '完成说明'], ['skipped_reason', '跳过理由']] as const) {
      if (text(patch[key])) details.push(`${label}：${text(patch[key])}`)
    }
  }
  if (event.type === 'followup_merged' && payload.source_header && typeof payload.source_header === 'object') {
    const header = payload.source_header as Record<string, unknown>
    if (text(header.owner)) details.push(`原负责人：${text(header.owner)}`)
    if (text(header.submitted_at)) details.push(`原提交：${text(header.submitted_at)}，${text(header.submission_channel)}`)
  }
  return [labels[event.type ?? ''] ?? '跟进记录', ...details].join(' · ')
}
