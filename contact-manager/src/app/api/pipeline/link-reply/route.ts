import { NextRequest, NextResponse } from 'next/server'
import { getServerSupabase } from '@/lib/supabase-server'

export const dynamic = 'force-dynamic'

// Links an unmatched reply (pipeline_replies.id) to a job_applications row. The link and its status
// event are one transaction inside the pipeline_link_reply RPC (migration 20261014000000), which also
// owns the version bump and the deterministic event id; this route only validates and maps errors.

function nonEmpty(value: unknown): value is string {
  return typeof value === 'string' && value.trim().length > 0
}

export async function POST(req: NextRequest) {
  const isForm = !(req.headers.get('content-type') || '').includes('application/json')
  let replyId: unknown
  let applicationId: unknown
  try {
    if (isForm) {
      const form = await req.formData()
      replyId = form.get('reply_id')
      applicationId = form.get('application_id')
    } else {
      const body = await req.json()
      replyId = body?.reply_id
      applicationId = body?.application_id
    }
  } catch {
    return NextResponse.json({ error: 'invalid request body' }, { status: 400 })
  }

  const appText = typeof applicationId === 'number' ? String(applicationId) : applicationId
  if (!nonEmpty(replyId) || !nonEmpty(appText) || !/^\d{1,18}$/.test(appText.trim())) {
    return NextResponse.json(
      { error: 'reply_id is required and application_id must be a numeric application id' },
      { status: 400 },
    )
  }

  try {
    const supabase = getServerSupabase()
    const { data, error } = await supabase.rpc('pipeline_link_reply', {
      p_reply_id: replyId.trim(),
      p_application_id: Number(appText.trim()),
    })
    if (error) {
      if (error.code === 'P0002') return NextResponse.json({ error: 'reply or application not found' }, { status: 404 })
      if (error.code === 'P0001') return NextResponse.json({ error: 'reply is already linked elsewhere' }, { status: 409 })
      return NextResponse.json({ error: 'database error' }, { status: 500 })
    }
    if (isForm) {
      return NextResponse.redirect(new URL('/pipeline', req.url), 303)
    }
    return NextResponse.json({ ok: true, event: Boolean(data?.event_written) })
  } catch {
    return NextResponse.json({ error: 'server error' }, { status: 500 })
  }
}
