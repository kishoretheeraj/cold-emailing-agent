-- Sprint 2 — seed 8 instruction-level prompt rows
-- Run in Supabase SQL editor once.
-- Sort orders 11–18 sit in the gap between sender_profile (10) and outreach_prompt (20).
-- Values are verbatim copies of config.py constants; default_value mirrors value so
-- the "Reset to default" button in /prompts restores the original agent behavior.

INSERT INTO prompts (key, value, display_title, description, default_value, sort_order, updated_at)
VALUES

  ('outreach_first_touch_instruction',
   'First-touch cold email. End with a soft ask for a 15-min chat.',
   'Outreach: First Touch Instruction',
   'Injected as {template_instruction} in the outreach prompt for cold_intro emails.',
   'First-touch cold email. End with a soft ask for a 15-min chat.',
   11,
   NOW()),

  ('outreach_followup1_instruction',
   'Gentle follow-up (day 5). Reference something new — a recent company announcement, article, or product launch. Not pushy.',
   'Outreach: Follow-up 1 Instruction',
   'Injected as {template_instruction} for follow_up_1 emails.',
   'Gentle follow-up (day 5). Reference something new — a recent company announcement, article, or product launch. Not pushy.',
   12,
   NOW()),

  ('outreach_followup2_instruction',
   'Second follow-up (day 10). Shorter, mild urgency. Mention you''ll stop reaching out if now isn''t the right time.',
   'Outreach: Follow-up 2 Instruction',
   'Injected as {template_instruction} for follow_up_2 emails.',
   'Second follow-up (day 10). Shorter, mild urgency. Mention you''ll stop reaching out if now isn''t the right time.',
   13,
   NOW()),

  ('outreach_breakup_instruction',
   'Final email. 2-3 sentences max. Respectful close. Leave door open.',
   'Outreach: Breakup Instruction',
   'Injected as {template_instruction} for breakup emails.',
   'Final email. 2-3 sentences max. Respectful close. Leave door open.',
   14,
   NOW()),

  ('tier_1_instruction',
   'DREAM company. Deep personalization. Reference specific company news, products, or the contact''s background. Show you''ve done real research.',
   'Tier 1 Instruction',
   'Injected as {tier_instruction} for Tier 1 contacts. Demands deep personalization.',
   'DREAM company. Deep personalization. Reference specific company news, products, or the contact''s background. Show you''ve done real research.',
   15,
   NOW()),

  ('tier_2_instruction',
   'STRONG FIT. Moderate personalization. Reference the company and why you''re interested, but don''t overdo it.',
   'Tier 2 Instruction',
   'Injected as {tier_instruction} for Tier 2 contacts.',
   'STRONG FIT. Moderate personalization. Reference the company and why you''re interested, but don''t overdo it.',
   16,
   NOW()),

  ('tier_3_instruction',
   'WORTH A SHOT. Keep it efficient. Brief, professional, to the point.',
   'Tier 3 Instruction',
   'Injected as {tier_instruction} for Tier 3 contacts.',
   'WORTH A SHOT. Keep it efficient. Brief, professional, to the point.',
   17,
   NOW()),

  ('dartmouth_instruction',
   'ALUMNI CONNECTION DETECTED: Treat this as warm alumni outreach, not cold. Reference the shared Dartmouth/Tuck/Thayer connection naturally and early. Use a peer-to-peer tone, not a stranger-to-stranger tone. Soften the ask.',
   'Dartmouth Alumni Instruction',
   'Injected as {dartmouth_instruction} when a contact is detected as a Dartmouth/Tuck/Thayer alumnus. Applied to both outreach and applied email prompts.',
   'ALUMNI CONNECTION DETECTED: Treat this as warm alumni outreach, not cold. Reference the shared Dartmouth/Tuck/Thayer connection naturally and early. Use a peer-to-peer tone, not a stranger-to-stranger tone. Soften the ask.',
   18,
   NOW())

ON CONFLICT (key) DO NOTHING;
