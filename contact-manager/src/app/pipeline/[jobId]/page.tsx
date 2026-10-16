import Link from 'next/link'
import { notFound } from 'next/navigation'
import { getServerSupabase } from '@/lib/supabase-server'
import StatusChip from '@/components/pipeline/StatusChip'
import ReplyFeed, { PipelineReply } from '@/components/pipeline/ReplyFeed'

export const dynamic = 'force-dynamic'

interface AttemptRow {
  attempt_id: string
  started_at: string | null
  finished_at: string | null
  status: string | null
  detail: string | null
  latency_s: number | null
  dry_run: boolean | null
}
interface EventRow {
  id: string
  status: string
  reply_id: string | null
  at: string
  actor: string | null
}

function formatTime(value: string | null | undefined): string {
  if (!value) return '-'
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return value
  return d.toISOString().replace('T', ' ').slice(0, 16) + ' UTC'
}

const cell = { padding: '6px 10px', textAlign: 'left' as const, borderBottom: '1px solid #e5e7eb', fontSize: 14 }
const REPLY_COLUMNS =
  'id, message_id, application_id, job_id, from_domain, subject, received_at, kind, match_method, needs_review, confidence'

export default async function PipelineJobPage({
  params,
}: {
  params: Promise<{ jobId: string }>
}) {
  const { jobId: rawId } = await params
  const jobId = decodeURIComponent(rawId)
  const supabase = getServerSupabase()

  const jobRes = await supabase
    .from('pipeline_jobs')
    .select('id, company, role, url, platform, state')
    .eq('id', jobId)
    .maybeSingle()
  if (jobRes.error) {
    return (
      <main style={{ padding: 24 }}>
        <p>Could not load this job.</p>
      </main>
    )
  }
  if (!jobRes.data) notFound()
  const job = jobRes.data as {
    id: string
    company: string | null
    role: string | null
    url: string | null
    platform: string | null
    state: string | null
  }

  const [attemptsRes, byJobRes, byAppRes, eventsRes] = await Promise.all([
    supabase
      .from('pipeline_attempts')
      .select('attempt_id, started_at, finished_at, status, detail, latency_s, dry_run')
      .eq('job_id', jobId)
      .order('started_at', { ascending: false, nullsFirst: false }),
    supabase.from('pipeline_replies').select(REPLY_COLUMNS).eq('job_id', jobId),
    supabase.from('pipeline_replies').select(REPLY_COLUMNS).eq('application_id', jobId),
    supabase
      .from('pipeline_status_events')
      .select('id, status, reply_id, at, actor')
      .eq('application_id', jobId)
      .order('at', { ascending: false }),
  ])

  const attempts = (attemptsRes.data || []) as AttemptRow[]
  const events = (eventsRes.data || []) as EventRow[]
  const merged = new Map<string, PipelineReply>()
  for (const row of [...((byJobRes.data || []) as PipelineReply[]), ...((byAppRes.data || []) as PipelineReply[])]) {
    merged.set(row.id, row)
  }
  const replies = Array.from(merged.values()).sort((a, b) =>
    (b.received_at || '').localeCompare(a.received_at || ''),
  )

  return (
    <main style={{ padding: 24, display: 'grid', gap: 28 }}>
      <div>
        <Link href="/pipeline" style={{ fontSize: 14 }}>
          Back to pipeline
        </Link>
        <h1 style={{ fontSize: 24, fontWeight: 600, marginTop: 8 }}>
          {job.company || job.id}
          {job.role ? ` - ${job.role}` : ''}
        </h1>
        <div style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap', fontSize: 14 }}>
          <StatusChip status={job.state} />
          <span>platform: {job.platform || '-'}</span>
          {job.url ? (
            <a href={job.url} target="_blank" rel="noopener noreferrer">
              posting
            </a>
          ) : null}
        </div>
      </div>

      <section>
        <h2 style={{ fontSize: 16, fontWeight: 600, marginBottom: 8 }}>Attempts</h2>
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead>
            <tr>
              <th style={cell}>Started</th>
              <th style={cell}>Finished</th>
              <th style={cell}>Status</th>
              <th style={cell}>Latency</th>
              <th style={cell}>Detail</th>
            </tr>
          </thead>
          <tbody>
            {attempts.map((a) => (
              <tr key={a.attempt_id}>
                <td style={cell}>{formatTime(a.started_at)}</td>
                <td style={cell}>{formatTime(a.finished_at)}</td>
                <td style={cell}>
                  <StatusChip status={a.status} />
                </td>
                <td style={cell}>{a.latency_s != null ? `${Number(a.latency_s).toFixed(1)} s` : '-'}</td>
                <td style={{ ...cell, wordBreak: 'break-word' }}>{a.detail || '-'}</td>
              </tr>
            ))}
            {attempts.length === 0 ? (
              <tr>
                <td style={cell} colSpan={5}>
                  No attempts yet.
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </section>

      <section>
        <h2 style={{ fontSize: 16, fontWeight: 600, marginBottom: 8 }}>Replies</h2>
        <ReplyFeed replies={replies} />
      </section>

      <section>
        <h2 style={{ fontSize: 16, fontWeight: 600, marginBottom: 8 }}>Status events</h2>
        {events.length === 0 ? (
          <p style={{ color: '#6b7280', fontSize: 14 }}>No status events.</p>
        ) : (
          <ul style={{ listStyle: 'none', margin: 0, padding: 0, display: 'grid', gap: 6 }}>
            {events.map((e) => (
              <li key={e.id} style={{ display: 'flex', gap: 10, alignItems: 'center', fontSize: 14 }}>
                <StatusChip status={e.status} />
                <span>{formatTime(e.at)}</span>
                <span style={{ color: '#6b7280' }}>{e.actor || ''}</span>
              </li>
            ))}
          </ul>
        )}
      </section>
    </main>
  )
}
