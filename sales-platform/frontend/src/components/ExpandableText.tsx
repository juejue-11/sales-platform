import { useId, useState } from 'react'
import { Button, Typography } from 'antd'

/** Disclosure is for narrative text only. Uncertain facts and errors stay visible. */
export function ExpandableText({ text, className = '', limit = 180 }: { text: string; className?: string; limit?: number }) {
  const [expanded, setExpanded] = useState(false)
  const contentId = useId()
  const canExpand = text.length > limit
  return (
    <div className={`detail-disclosure ${className}`.trim()}>
      <Typography.Paragraph id={contentId} className={canExpand && !expanded ? 'detail-disclosure-preview' : undefined}>{text}</Typography.Paragraph>
      {canExpand && <Button type="link" size="small" aria-expanded={expanded} aria-controls={contentId} onClick={() => setExpanded((value) => !value)}>{expanded ? '收起全文' : '展开全文'}</Button>}
    </div>
  )
}
