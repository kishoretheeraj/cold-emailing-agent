type Tone = { background: string; color: string }

const NEUTRAL: Tone = { background: '#e5e7eb', color: '#374151' }

const TONES: Record<string, Tone> = {
  submitted: { background: '#dbeafe', color: '#1e40af' },
  submitted_unconfirmed: { background: '#fef3c7', color: '#92400e' },
  confirmed: { background: '#d1fae5', color: '#065f46' },
  confirmation: { background: '#d1fae5', color: '#065f46' },
  assessment: { background: '#ede9fe', color: '#5b21b6' },
  interview: { background: '#cffafe', color: '#155e75' },
  offer: { background: '#bbf7d0', color: '#14532d' },
  rejected: { background: '#fee2e2', color: '#991b1b' },
  rejection: { background: '#fee2e2', color: '#991b1b' },
  failed: { background: '#fee2e2', color: '#991b1b' },
  needs_review: { background: '#fef3c7', color: '#92400e' },
  in_progress: { background: '#e0e7ff', color: '#3730a3' },
  claimed: { background: '#e0e7ff', color: '#3730a3' },
  dry_run_ok: { background: '#f3f4f6', color: '#065f46' },
  dry_run_incomplete: { background: '#f3f4f6', color: '#92400e' },
}

export default function StatusChip({ status }: { status: string | null | undefined }) {
  const label = status || 'unknown'
  const tone = TONES[label] || NEUTRAL
  const dry = label.startsWith('dry_run')
  return (
    <span
      style={{
        display: 'inline-block',
        padding: '2px 8px',
        borderRadius: 9999,
        fontSize: 12,
        fontWeight: 500,
        whiteSpace: 'nowrap',
        background: tone.background,
        color: tone.color,
        border: dry ? '1px dashed #9ca3af' : '1px solid transparent',
      }}
    >
      {label.replace(/_/g, ' ')}
    </span>
  )
}
