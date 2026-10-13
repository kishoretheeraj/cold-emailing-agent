import StatusChip from '@/components/pipeline/StatusChip'

export interface PipelineReply {
  message_id: string
  application_id: string | null
  job_id: string | null
  from_domain: string | null
  subject: string | null
  received_at: string | null
  kind: string | null
  match_method: string | null
  needs_review: boolean | null
  confidence: number | null
}

function formatTime(value: string | null | undefined): string {
  if (!value) return '-'
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return value
  return d.toISOString().replace('T', ' ').slice(0, 16) + ' UTC'
}

export default function ReplyFeed({ replies }: { replies: PipelineReply[] }) {
  if (!replies || replies.length === 0) {
    return <p style={{ color: '#6b7280', fontSize: 14 }}>No replies yet.</p>
  }
  return (
    <ul style={{ listStyle: 'none', margin: 0, padding: 0, display: 'grid', gap: 8 }}>
      {replies.map((reply) => (
        <li
          key={reply.message_id}
          style={{ border: '1px solid #e5e7eb', borderRadius: 8, padding: '10px 12px' }}
        >
          <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
            <StatusChip status={reply.kind} />
            <span style={{ fontSize: 13, color: '#374151' }}>{reply.from_domain || 'unknown sender'}</span>
            <span style={{ fontSize: 12, color: '#6b7280' }}>{formatTime(reply.received_at)}</span>
            {reply.needs_review ? <StatusChip status="needs_review" /> : null}
          </div>
          <div style={{ marginTop: 6, fontSize: 14 }}>{reply.subject || '(no subject)'}</div>
          <div style={{ marginTop: 4, fontSize: 12, color: '#6b7280' }}>
            match: {reply.match_method || 'none'}
            {typeof reply.confidence === 'number' ? ` (${reply.confidence.toFixed(2)})` : ''}
          </div>
        </li>
      ))}
    </ul>
  )
}
