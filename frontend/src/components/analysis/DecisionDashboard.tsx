import { useEffect, useMemo, useRef, useState } from 'react'
import { Button, Checkbox, message } from 'antd'
import { CheckCircleOutlined, DownOutlined, UpOutlined } from '@ant-design/icons'
import { api } from '../../api'
import type { FollowupData, ProjectDetail } from '../../types'
import { EvidenceBasisDrawer } from './EvidenceBasisDrawer'
import type { DecisionBasis } from './EvidenceBasisDrawer'
import { buildDecisionView, concise, sameTask } from './decisionView'
import './DecisionDashboard.css'

interface Props {
  detail: ProjectDetail
  onFollowup: () => void
  onFollowupChange: (data: FollowupData) => void
}

export function DecisionDashboard({ detail, onFollowup, onFollowupChange }: Props) {
  const view = useMemo(() => buildDecisionView(detail), [detail])
  const [allRisks, setAllRisks] = useState(false)
  const [allActions, setAllActions] = useState(false)
  const [basis, setBasis] = useState<DecisionBasis | null>(null)
  const [saving, setSaving] = useState('')
  const mounted = useRef(true)
  useEffect(() => { mounted.current = true; return () => { mounted.current = false } }, [])
  const storageKey = `procurement-ai-actions:${detail.id}`
  const [completed, setCompleted] = useState<string[]>(() => {
    try {
      const saved: unknown = JSON.parse(localStorage.getItem(storageKey) ?? '[]')
      return Array.isArray(saved) ? saved.filter((value): value is string => typeof value === 'string') : []
    } catch { return [] }
  })
  const tasks = detail.followup?.tasks ?? []
  const taskFor = (text: string) => tasks.find(task => sameTask(task.title || task.name || task.description || '', text))
  const isDone = (text: string) => {
    const task = taskFor(text)
    return task ? task.status === 'done' : completed.includes(text)
  }
  const toggle = async (text: string, checked: boolean) => {
    const task = taskFor(text)
    setSaving(text)
    try {
      if (task) {
        const result = await api.updateFollowupTask(detail.id, task.id, { status: checked ? 'done' : 'todo' })
        if (mounted.current) onFollowupChange(result)
      } else {
        const next = checked ? [...completed.filter(item => item !== text), text] : completed.filter(item => item !== text)
        localStorage.setItem(storageKey, JSON.stringify(next))
        setCompleted(next)
      }
    } catch (error) { if (mounted.current) message.error(error instanceof Error ? error.message : '完成状态保存失败，请重试') }
    finally { if (mounted.current) setSaving('') }
  }
  const doneCount = view.actions.filter(item => isDone(item.text)).length
  const emptySummary = view.stale ? '采购资料已变化，原结论需重新核对，请通过项目操作重新研判。'
    : detail.ai_status === 'not_configured' ? '尚未配置 AI，请通过项目操作配置 AI 研判。'
    : detail.ai_status === 'failed' ? '本次研判失败，请通过右侧「重新研判」重试。'
    : ['running', 'queued'].includes(detail.ai_status) ? '研判正在进行，结论形成后将自动更新。'
    : detail.ai_status === 'skipped' ? '当前项目未生成 AI 结论，请先核对项目状态与采购资料。'
    : '尚未形成研判结论，请先补充采购资料并开始研判。'
  const matchText = view.match === 'assessed' ? '企业匹配已按现有资料评估' : view.match === 'partial' ? '企业匹配部分评估' : '企业匹配待评估'

  return <div className="decision-dashboard" aria-label="AI 决策工作台">
    <section className={`decision-conclusion decision-tone-${view.tone}`} aria-labelledby="decision-heading">
      <header className="decision-section-heading"><h2 id="decision-heading">AI 研判结论</h2>
        {(view.stale || ['running', 'queued', 'failed'].includes(detail.ai_status)) && <span className="decision-update-status" role="status">{view.stale ? '资料已变化，需重新研判' : detail.ai_status === 'failed' ? '本次研判失败' : '研判中，完成后自动更新'}</span>}
      </header>
      <div className="decision-verdict"><span>跟进建议</span><strong>{view.recommendation}</strong><span className="decision-match">{matchText}</span></div>
      <p className="decision-summary">{view.summary || emptySummary}</p>
      <p className="decision-match-note">{view.match === 'assessed' ? '企业资料为自行提供，参与前仍需核验资格与供货能力。' : view.match === 'partial' ? '仅部分条件有企业资料支持，其余条件仍需核实。' : '当前建议基于采购机会；尚不能确认本企业是否具备参与条件。'}</p>
      {view.facts.length > 0 && <div className="decision-facts"><h3>关键依据</h3><ul>{view.facts.map((fact, index) => <li key={index}><span>{concise(fact.text, 64)}</span><Button type="link" size="small" aria-label={`查看依据：关键事实 ${index + 1}`} onClick={() => setBasis({ title: '关键事实依据', ...fact })}>查看依据</Button></li>)}</ul></div>}
    </section>

    <section className="decision-section" aria-labelledby="decision-risks-heading">
      <header className="decision-section-heading"><h2 id="decision-risks-heading">核心风险与待确认事项</h2><span>{view.risks.length ? `${view.risks.length} 项` : '待识别'}</span></header>
      {view.risks.length ? <ul className="decision-risk-list" id="decision-risk-list">{(allRisks ? view.risks : view.risks.slice(0, 3)).map((risk, index) => <li key={`${risk.title}-${index}`}>
        <span className={`decision-impact decision-impact-${risk.priority}`}>{['高影响', '中影响', '低影响'][risk.priority]}</span>
        <div className="decision-risk-copy"><strong>{risk.title}</strong><p>{concise(risk.text, 45)}</p></div>
        <Button type="link" size="small" aria-label={`查看依据：${risk.title}`} onClick={() => setBasis({ title: risk.title, text: risk.text, evidence: risk.evidence, impact: risk.impact })}>查看依据</Button>
      </li>)}</ul> : <p className="decision-empty">{view.hasResult ? '当前资料未提取出关键风险，仍需结合招标文件核对参与条件。' : '研判完成后，在此列出影响参与决策的关键风险。'}</p>}
      {view.risks.length > 3 && <Button type="text" className="decision-expand" icon={allRisks ? <UpOutlined /> : <DownOutlined />} aria-expanded={allRisks} aria-controls="decision-risk-list" onClick={() => setAllRisks(!allRisks)}>{allRisks ? '收起风险' : `展开其余 ${view.risks.length - 3} 项风险`}</Button>}
    </section>

    <section className="decision-section" aria-labelledby="decision-actions-heading">
      <header className="decision-section-heading"><h2 id="decision-actions-heading">下一步行动建议</h2>{view.actions.length > 0 && <span className="decision-completion"><CheckCircleOutlined /> {doneCount}/{view.actions.length} 已完成</span>}</header>
      {view.actions.length ? <ul className="decision-action-list" id="decision-action-list">{(allActions ? view.actions : view.actions.slice(0, 3)).map((action, index) => <li key={action.text} className={isDone(action.text) ? 'is-complete' : ''}>
        <Checkbox checked={isDone(action.text)} disabled={Boolean(saving)} onChange={event => void toggle(action.text, event.target.checked)}><span className="decision-action-text">{concise(action.text, 110)}</span></Checkbox>
        <span className="decision-action-priority">{isDone(action.text) ? '已完成' : index === 0 ? '优先办理' : '待办理'}</span>
      </li>)}</ul> : <p className="decision-empty">{view.hasResult ? '暂无明确行动建议，可先核对采购内容与资格要求。' : '研判完成后，在此列出优先办理的行动。'}</p>}
      {view.actions.length > 0 && <footer className="decision-action-footer">{view.actions.length > 3 && <Button type="text" className="decision-expand" icon={allActions ? <UpOutlined /> : <DownOutlined />} aria-expanded={allActions} aria-controls="decision-action-list" onClick={() => setAllActions(!allActions)}>{allActions ? '收起行动' : `展开其余 ${view.actions.length - 3} 项行动`}</Button>}<span className="decision-save-note">勾选自动保存{tasks.length ? '' : '于本机'}</span><Button size="small" onClick={onFollowup}>{detail.is_following ? '查看跟进任务' : '生成跟进任务'}</Button></footer>}
    </section>

    <EvidenceBasisDrawer basis={basis} detail={detail} onClose={() => setBasis(null)} />
  </div>
}
