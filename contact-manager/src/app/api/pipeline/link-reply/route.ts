import { NextRequest, NextResponse } from 'next/server'
import { createHash } from 'crypto'
import { getServerSupabase } from '@/lib/supabase-server'

export const dynamic = 'force-dynamic'

// Mirrors reply_repo.KIND_TO_STATUS on the pipeline side.
const KIND_TO_STATUS: Record<string, string> = {
  confirmation: 'confirmed',
  assessment: 'assessment',
  interview: 'interview',
  offer: 'offer',
  rejection: 'rejected',
}

function nonEmpty(value: unknown): value is string {
  return typeof value === 'string' && value.trim().length > 0
}

// Deterministic event id: retries of the same link converge instead of duplicating events.
function linkEventId(messageId: string, appId: string): string {
  return createHash('sha256').update(`link:${messageId}:${appId}`).digest('hex')
}

export async function POST(req: NextRequest) {
  const isForm = !(req.headers.get('content-type') || '').includes('application/json')
  let replyMessageId: unknown
  let applicationId: unknown
  try {
    if (isForm) {
      const form = await req.formData()
      replyMessageId = form.get('reply_message_id')
      applicationId = form.get('application_id')
    } else {
      const body = await req.json()
      replyMessageId = body?.reply_message_id
      applicationId = body?.application_id
    }
  } catch {
    return NextResponse.json({ error: 'invalid request body' }, { status: 400 })
  }

  if (!nonEmpty(replyMessageId) || !nonEmpty(applicationId)) {
    return NextResponse.json(
      { error: 'reply_message_id and application_id are required strings' },
      { status: 400 },
    )
  }
  const messageId = replyMessageId.trim()
  const appId = applicationId.trim()

  try {
    const supabase = getServerSupabase()

    const found = await supabase
      .from('pipeline_replies')
      .select('message_id, kind')
      .eq('message_id', messageId)
      .maybeSingle()
    if (found.error) return NextResponse.json({ error: 'database error' }, { status: 500 })
    if (!found.data) return NextResponse.json({ error: 'reply not found' }, { status: 404 })

    const app = await supabase
      .from('pipeline_jobs')
      .select('id')
      .eq('id', appId)
      .maybeSingle()
    if (app.error) return NextResponse.json({ error: 'database error' }, { status: 500 })
    if (!app.data) return NextResponse.json({ error: 'application not found' }, { status: 404 })

    const updated = await supabase
      .from('pipeline_replies')
      .update({ application_id: appId, needs_review: false, updated_at: new Date().toISOString() })
      .eq('message_id', messageId)
    if (updated.error) return NextResponse.json({ error: 'database error' }, { status: 500 })

    const status = KIND_TO_STATUS[found.data.kind as string]
    let eventWritten = false
    if (status) {
      // Idempotent: the same link retried reuses its event id instead of duplicating.
      const inserted = await supabase.from('pipeline_status_events').upsert(
        {
          id: linkEventId(messageId, appId),
          application_id: appId,
          status,
          reply_id: messageId,
          at: new Date().toISOString(),
          actor: 'user',
        },
        { onConflict: 'id' },
      )
      if (inserted.error) return NextResponse.json({ error: 'database error' }, { status: 500 })
      eventWritten = true
    }

    if (isForm) {
      return NextResponse.redirect(new URL('/pipeline', req.url), 303)
    }
    return NextResponse.json({ ok: true, event: eventWritten })
  } catch {
    return NextResponse.json({ error: 'server error' }, { status: 500 })
  }
}
