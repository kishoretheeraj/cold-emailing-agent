import Link from 'next/link'
import { getServerSupabase } from '@/lib/supabase-server'
import StatusChip from '@/components/pipeline/StatusChip'
import ReplyFeed, { PipelineReply } from '@/components/pipeline/ReplyFeed'

export const dynamic = 'force-dynamic'

interface JobRow {
  id: string
  company: string | null
  role: string | null
  platform: string | null
  state: string | null
  queued_at: string | null
}
interface AttemptRow {
  job_id: string | null
  status: string | null
  started_at: string | null
}
interface EventRow {
  application_id: string
  status: string
  at: string
}

function formatTime(value: string | null | undefined): string {
  if (!value) return '-'
  const d = new Date(value)
  if (Number.isNaN(d.getTime())) return value
  return d.toISOString().replace('T', ' ').slice(0, 16) + ' UTC'
}

const cell = { padding: '6px 10px', textAlign: 'left' as const, borderBottom: '1px solid #e5e7eb', fontSize: 14 }

function thirtyDaysAgo(): string {
  return new Date(Date.now() - 30 * 24 * 3600 * 1000).toISOString()
}

export default async function PipelinePage() {
  const supabase = getServerSupabase()
  const since = thirtyDaysAgo()

  const [attemptsRes, jobsRes, recentAttemptsRes, eventsRes, reviewRes] = await Promise.all([
    supabase.from('pipeline_attempts').select('status').gte('started_at', since).limit(10000),
    supabase
      .from('pipeline_jobs')
      .select('id, company, role, platform, state, queued_at')
      .order('queued_at', { ascending: false, nullsFirst: false })
      .limit(200),
    supabase
      .from('pipeline_attempts')
      .select('job_id, status, started_at')
      .order('started_at', { ascending: false, nullsFirst: false })
      .limit(2000),
    supabase
      .from('pipeline_status_events')
      .select('application_id, status, at')
      .order('at', { ascending: false })
      .limit(2000),
    supabase
      .from('pipeline_replies')
      .select('message_id, application_id, job_id, from_domain, subject, received_at, kind, match_method, needs_review, confidence')
      .eq('needs_review', true)
      .order('received_at', { ascending: false })
      .limit(100),
  ])

  const error = attemptsRes.error || jobsRes.error || recentAttemptsRes.error || eventsRes.error || reviewRes.error
  if (error) {
    return (
      <main style={{ padding: 24 }}>
        <h1>Pipeline</h1>
        <p>Could not load pipeline data.</p>
      </main>
    )
  }

  const tally = new Map<string, number>()
  for (const row of (attemptsRes.data || []) as { status: string | null }[]) {
    const key = row.status || 'unknown'
    tally.set(key, (tally.get(key) || 0) + 1)
  }
  const tiles = Array.from(tally.entries()).sort((a, b) => b[1] - a[1])

  const latestAttempt = new Map<string, AttemptRow>()
  for (const row of (recentAttemptsRes.data || []) as AttemptRow[]) {
    if (row.job_id && !latestAttempt.has(row.job_id)) latestAttempt.set(row.job_id, row)
  }
  const latestEvent = new Map<string, EventRow>()
  for (const row of (eventsRes.data || []) as EventRow[]) {
    if (!latestEvent.has(row.application_id)) latestEvent.set(row.application_id, row)
  }

  const jobs = (jobsRes.data || []) as JobRow[]
  const review = (reviewRes.data || []) as PipelineReply[]

  return (
    <main style={{ padding: 24, display: 'grid', gap: 28 }}>
      <h1 style={{ fontSize: 24, fontWeight: 600 }}>Pipeline</h1>

      <section>
        <h2 style={{ fontSize: 16, fontWeight: 600, marginBottom: 8 }}>Attempts, last 30 days</h2>
        {tiles.length === 0 ? (
          <p style={{ color: '#6b7280', fontSize: 14 }}>No attempts recorded.</p>
        ) : (
          <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap' }}>
            {tiles.map(([status, count]) => (
              <div
                key={status}
                style={{ border: '1px solid #e5e7eb', borderRadius: 8, padding: '10px 14px', minWidth: 110 }}
              >
                <div style={{ fontSize: 24, fontWeight: 600 }}>{count}</div>
                <StatusChip status={status} />
              </div>
            ))}
          </div>
        )}
      </section>

      <section>
        <h2 style={{ fontSize: 16, fontWeight: 600, marginBottom: 8 }}>Applications</h2>
        <table style={{ width: '100%', borderCollapse: 'collapse' }}>
          <thead>
            <tr>
              <th style={cell}>Company</th>
              <th style={cell}>Role</th>
              <th style={cell}>Status</th>
              <th style={cell}>Last event</th>
            </tr>
          </thead>
          <tbody>
            {jobs.map((job) => {
              const attempt = latestAttempt.get(job.id)
              const event = latestEvent.get(job.id)
              return (
                <tr key={job.id}>
                  <td style={cell}>
                    <Link href={`/pipeline/${encodeURIComponent(job.id)}`}>{job.company || job.id}</Link>
                  </td>
                  <td style={cell}>{job.role || '-'}</td>
                  <td style={cell}>
                    <StatusChip status={event?.status || attempt?.status || job.state} />
                  </td>
                  <td style={cell}>{formatTime(event?.at || attempt?.started_at)}</td>
                </tr>
              )
            })}
            {jobs.length === 0 ? (
              <tr>
                <td style={cell} colSpan={4}>
                  No jobs synced yet.
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      </section>

      <section>
        <h2 style={{ fontSize: 16, fontWeight: 600, marginBottom: 8 }}>Needs review ({review.length})</h2>
        {review.length === 0 ? (
          <p style={{ color: '#6b7280', fontSize: 14 }}>Nothing waiting.</p>
        ) : (
          <div style={{ display: 'grid', gap: 12 }}>
            {review.map((reply) => (
              <div key={reply.message_id}>
                <ReplyFeed replies={[reply]} />
                <form
                  action="/api/pipeline/link-reply"
                  method="post"
                  style={{ display: 'flex', gap: 8, marginTop: 6 }}
                >
                  <input type="hidden" name="reply_message_id" value={reply.message_id} />
                  <input
                    type="text"
                    name="application_id"
                    placeholder="application id"
                    required
                    style={{ border: '1px solid #d1d5db', borderRadius: 6, padding: '4px 8px', fontSize: 14 }}
                  />
                  <button
                    type="submit"
                    style={{ border: '1px solid #d1d5db', borderRadius: 6, padding: '4px 10px', fontSize: 14 }}
                  >
                    Link to application
                  </button>
                </form>
              </div>
            ))}
          </div>
        )}
      </section>
    </main>
  )
}
