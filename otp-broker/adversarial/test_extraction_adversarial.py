"""Area 3: extract.py called directly (no network, no live service)."""
from email.message import EmailMessage

from common import Rec
from extract import AmbiguousCode, domain_matches, extract_code, extract_link, find_codes, message_texts

rec = Rec("extraction")


def code_of(plain=None, html=None):
    """Build a real multipart message and run the same path broker._evaluate uses."""
    m = EmailMessage()
    m["From"] = "noreply@example.com"; m["Subject"] = "x"
    if plain is not None and html is not None:
        m.set_content(plain); m.add_alternative(html, subtype="html")
    elif html is not None:
        m.set_content(html, subtype="html")
    else:
        m.set_content(plain)
    try:
        return extract_code(message_texts(m))
    except AmbiguousCode:
        return "CONFLICT"


def check(cid, name, got, want, sev="P0", fix="", wrong_is_p0=True):
    ok = got == want
    if not ok and got is not None and got != "CONFLICT" and want is not None:
        sev = "P0"  # returned a *different* code => wrong-code
    elif not ok and want is not None and got is None:
        sev = "P1" if sev == "P0" else sev  # false negative: caller times out
    rec.case(cid, name, ok, f"got={got!r} want={want!r}", sev, fix)


# ---- user-specified adversarial cases ------------------------------------------------
check("E01", "multiple 6-digit numbers -> verification code, not order/zip",
      code_of("Your order #123456 shipped to zip 90210. Your verification code is 789012."), "789012")
check("E02", "code split across HTML tags 4<b>8</b>3<i>9</i>20",
      code_of(html="<p>Your code is 4<b>8</b>3<i>9</i>20</p>"), "483920")
check("E03", "phone number does not pollute", code_of("+1 (555) 123-4567, your code is 998877"), "998877")
check("E03b", "phone number AFTER keyword is ignored",
      code_of("Call code line: +1 (555) 123-4567. Your verification code is 998877"), "998877")
check("E04", "currency $48.39 near code 112233", code_of("Charged $48.39. Your code 112233"), "112233")
check("E04b", "currency directly after keyword ('code $483920 refund')", code_of("Promo code $483920 applied. Your code is 112233"), "112233")
check("E05", "no code at all -> None (graceful)", code_of("Thanks for signing up!"), None)
check("E06", "two distinct codes -> 409 (CONFLICT) or documented behaviour",
      code_of("Your code 111111. If that fails use backup code 222222"), "CONFLICT", "P1",
      "BUILD_BRIEF says '>2 candidates' => 409, so exactly 2 distinct codes silently returns the first. Make MAX_DISTINCT_CODES=1 (any 2 distinct codes => 409) or prefer the candidate nearest an 'is/:' cue")
check("E07a", "spaces: '48 39 20'", code_of("Your code is 48 39 20"), "483920")
check("E07b", "hyphens: '48-39-20'", code_of("Your code is 48-39-20"), "483920")
check("E07c", "grouped 3-3: '483 920'", code_of("Your verification code: 483 920"), "483920")
check("E07d", "non-breaking space '483\\u00a0920'", code_of("Your verification code: 483 920"), "483920", "P1",
      "Normalise \\u00a0/\\u2009/\\u202f to ' ' before matching (HTML &nbsp; commonly renders as \\u00a0)")
check("E07e", "html &nbsp; group '483&nbsp;920'", code_of(html="<p>Your verification code: 483&nbsp;920</p>"), "483920", "P1",
      "Same: normalise Unicode spaces")

# ---- additional adversarial extraction ---------------------------------------------------
check("E10", "zip CODE then real code ('zip code 90210 ... verification code is 789012')",
      code_of("Shipping to zip code 90210. Your verification code is 789012."), "789012", "P0",
      "Require keyword set to be code-ish (verification|security|login|one-time|otp|passcode) and drop bare 'code'/'pin' unless adjacent to is/:")
check("E11", "promo code with digits then OTP", code_of("Use promo code 2024 for 10% off. Your verification code is 654321."), "654321", "P0",
      "Same as E10")
check("E12", "OTP before keyword ('123456 is your verification code')", code_of("123456 is your verification code"), "123456", "P1",
      "Add reverse pattern: (\\d{4,8})\\s+(?:is|as)\\s+your\\s+(?:\\w+\\s+){0,2}(?:code|otp|passcode) (Google, Microsoft, Apple style)")
check("E13", "'Use 482913 as your code'", code_of("Use 482913 as your code to sign in."), "482913", "P1", "Same reverse pattern")
check("E14", "'one-time password is 482913' (keyword 'password')", code_of("Your one-time password is 482913"), "482913", "P1",
      "Add 'password' and 'one-time'/'security code' variants to the keyword set")
check("E15", "digit-per-table-cell OTP", code_of(html="<table><tr><td>4</td><td>8</td><td>3</td><td>9</td><td>2</td><td>0</td></tr></table>Your verification code is shown above"), None,
      "P2", "")  # expectation None: documents inability; real-world emails put the code in cells
check("E15b", "'Your code:' then one digit per <td>", code_of(html="<p>Your code:</p><table><tr><td>4</td><td>8</td><td>3</td><td>9</td><td>2</td><td>0</td></tr></table>"), "483920", "P1",
      "html_to_text turns <td> into newlines, fragmenting digit-per-cell codes. Fallback: if no match, collapse whitespace between single digits")
check("E16", "alphanumeric code (Greenhouse-style 'security code: Ab3dE9xZ')", code_of("Copy and paste this code into the security code field on your application: Ab3dE9xZ"), "Ab3dE9xZ", "P1",
      "numeric-only: Greenhouse/Workable-style alphanumeric codes can never be extracted. Add optional alphanumeric mode (hint-gated) with length 6-10 + mixed charset")
check("E17", "Unicode (Arabic-Indic) digits not accepted as a code", code_of("Your code is ٤٨٣٩٢٠"), None, "P2",
      "Use [0-9] instead of \\d in _CODE_RE (str \\d matches all Unicode Nd digits; int() normalises them silently)")
check("E18", "fullwidth digits not accepted", code_of("Your code is ４８３９２０"), None, "P2", "Use [0-9]")
check("E19", "zero-width space inside code '48\\u200b3920'", code_of("Your code is 48​3920"), "483920", "P1", "Strip \\u200b\\u200c\\u200d\\ufeff before matching")
check("E20", "HTML comment decoy not extracted", code_of(html="<p>Hello</p><!-- your code 000000 --><p>Your code is 123456</p>"), "123456")
check("E21", "display:none decoy ignored", code_of(html="<span style='display:none'>verification code 000000</span><p>Your verification code is 123456</p>"), "123456", "P2",
      "Attacker-controlled sender can inject hidden candidates; since first match wins hidden text placed first wins. Drop elements with display:none/visibility:hidden/font-size:0/opacity:0 and preheader spans")
check("E22", "<style>/<script> content ignored", code_of(html="<style>.code{content:'code 999999'}</style><script>var x='code 888888'</script><p>Your code is 123456</p>"), "123456")
check("E23", "11-digit run is not truncated to 8", code_of("Your code is 12345678901"), None)
check("E24", "9-digit run not truncated", code_of("verification code 123456789"), None)
check("E25", "3-digit run rejected", code_of("Your code is 123"), None)
check("E26", "plain+html alternatives with same code (spaced vs not) -> single code",
      code_of(plain="Your code is 483920", html="<p>Your code is 483 920</p>"), "483920")
check("E27", "plain and html disagree (plain 111111 / html 222222)", code_of(plain="Your code is 111111", html="<p>Your code is 222222</p>"), "CONFLICT", "P1",
      "Disagreeing alternative bodies are suspicious; treat as conflict. Currently first (plain) wins silently")
check("E28", "time-like text '10:30' / years / 'code expires in 10 minutes' ignored", code_of("Your code expires in 10 minutes (until 10:30 2026). Your code is 482913."), "482913")
check("E29", "'PIN' inside word ('pinterest 123456') not a keyword", code_of("Welcome to Pinterest 123456 members. Your code is 482913."), "482913")
check("E30", "'verification 10-15 minutes' not parsed as 1015", code_of("Allow verification 10-15 minutes. Your code is 482913."), "482913", "P0",
      "Grouped-number branch accepts any 2-4 digit groups; require groups to be equal-sized (3-3, 2-2-2, 4-4) and not followed by unit words")
check("E31", "case-insensitive 'CODE' / 'Otp'", code_of("YOUR OTP: 482913"), "482913")
check("E32", "code on next line after keyword", code_of("Your verification code:\n\n482913\n\nIt expires in 10 minutes"), "482913")
check("E33", "code in bold markdown-ish '**482913**'", code_of("Your code is **482913**"), "482913", "P1", "Allow [*_`\"'<>()\\[\\]] between keyword/is and digits")
check("E34", "code in quotes or brackets 'code is [482913]'", code_of("Your code is [482913]"), "482913", "P1", "Same as E33")
check("E35", "three distinct codes -> CONFLICT", code_of("code 111111, code 222222, code 333333"), "CONFLICT")
check("E36", "message-quoted/forwarded old code after new one (reply chain)", code_of("Your code is 222222\n\n> On Mon wrote:\n> Your code is 111111"), "CONFLICT", "P1",
      "Cannot tell newest from quoted history; strip lines starting with '>' and everything after 'On ... wrote:' / '-----Original Message-----'")

# ---- 24 realistic samples -----------------------------------------------------------------
SAMPLES = [
    ("S01 Workday", "Your verification code is 482913. This code expires in 10 minutes. If you did not request this, contact support.", None, "482913"),
    ("S02 Workday-html", None, "<div style='font-family:Arial'><p>Hello Kishore,</p><p>Your Workday verification code is: <b style='font-size:24px'>482913</b></p><p>Valid for 15 minutes.</p></div>", "482913"),
    ("S03 Greenhouse", "Hi Kishore,\n\nCopy and paste this code into the security code field on your application:\n\nA7bX92Qd\n\nAfter you enter the code, resubmit your application.", None, "A7bX92Qd"),
    ("S04 Lever", "Your Lever verification code is 837261. Do not share.", None, "837261"),
    ("S05 iCIMS", "Use this code to verify your email address: Verification Code: 556677", None, "556677"),
    ("S06 Chase", "Chase: Your one-time passcode is 774411. Do not share this code with anyone. Chase will never call you for it.", None, "774411"),
    ("S07 BofA", "Bank of America: Your Authorization Code is 208154. Don't share it with anyone.", None, "208154"),
    ("S08 Wells Fargo", "Wells Fargo: Your temporary access code is 913377. Never share it.", None, "913377"),
    ("S09 Citi", "Citi: Use security code 650021 to complete your sign on. It expires in 5 minutes.", None, "650021"),
    ("S10 Google", "G-123456 is your Google verification code.", None, "123456"),
    ("S11 Microsoft", "Use 4711 as your Microsoft account security code (account ki*****@gmail.com). Never share.", None, "4711"),
    ("S12 Apple", "Your Apple ID Code is: 872305. Don't share it with anyone.", None, "872305"),
    ("S13 LinkedIn", "Here's your LinkedIn verification PIN: 193846. This PIN expires in 10 minutes.", None, "193846"),
    ("S14 Indeed", "Your Indeed verification code is 308122. Enter this code to continue. Indeed, 6433 Champion Grandview Way, Austin TX 78750", None, "308122"),
    ("S15 Ashby", "Your Ashby login code: 904412 (expires in 10 minutes)", None, "904412"),
    ("S16 Okta", "Your Okta verification code: 663301", None, "663301"),
    ("S17 Dartmouth Duo-ish", "Your passcode is 40291847", None, "40291847"),
    ("S18 Amazon", "Amazon: Your OTP is 385920. Do not share it, even with Amazon associates. Order #112-3344556-7788990.", None, "385920"),
    ("S19 PayPal", "PayPal: Your security code is: 782 345. It expires in 10 minutes.", None, "782345"),
    ("S20 Uber html", None, "<table><tr><td><h1>Enter 4-digit code</h1><p>Your Uber code is <strong>3 8 4 1</strong></p></td></tr></table>", "3841"),
    ("S21 Zoom", "Your Zoom verification code is 249 018. Zip: 03766. Phone: 603-555-0142.", None, "249018"),
    ("S22 Stripe", "Your Stripe verification code is 482-913. $1,234.56 payout pending.", None, "482913"),
    ("S23 Robinhood", "Your Robinhood code is 129478. Robinhood will never call you or ask for this code.", None, "129478"),
    ("S24 Substack magic", "Sign in to Substack\nYour code is 551209.\nOr click: https://substack.com/verify?token=abc", None, "551209"),
    ("S25 Handshake", "Your Handshake verification code: 77 22 91", None, "772291"),
    ("S26 Coinbase html-split", None, "<p>Your verification code is&nbsp;<span>48</span><span>29</span><span>13</span></p>", "482913"),
    ("S27 Marketing noise", "Flash sale! Use code SAVE20 at checkout, 20% off orders over $50. Customer service 1-800-555-0199.", None, None),
    ("S28 Receipt no OTP", "Thanks for your order #4829134. Total $48.39. Tracking 1Z999AA10123456784. Reply to this verification email if issues.", None, None),
]
for name, plain, html, want in SAMPLES:
    got = code_of(plain=plain, html=html)
    sev = "P1" if want else "P0"
    fix = "see E10-E36 recommendations" if want else "false positive => wrong code"
    rec.case(name.split()[0], f"realistic sample: {' '.join(name.split()[1:])}", got == want, f"got={got!r} want={want!r}", sev, fix)

# ---- link extraction ----------------------------------------------------------------------------
def link_msg(html=None, plain=None):
    m = EmailMessage(); m["From"] = "noreply@verify.example.com"; m["Subject"] = "Verify"
    if html is not None and plain is not None:
        m.set_content(plain); m.add_alternative(html, subtype="html")
    elif html is not None:
        m.set_content("see html", subtype="plain"); m.add_alternative(html, subtype="html")
    else:
        m.set_content(plain)
    return m


ALLOW = ["verify.example.com"]
H = lambda *u: "".join(f'<a href="{x}">l</a>' for x in u)
GOOD = "https://verify.example.com/verify?token=abc"


def lcheck(cid, name, html=None, plain=None, allow=ALLOW, want=None, sev="P0", fix=""):
    got = extract_link(link_msg(html=html, plain=plain), allow)
    rec.case(cid, name, got == want, f"got={got!r} want={want!r}", sev, fix)


lcheck("L01", "only exact allowlisted host picked among evil lookalikes",
       html=H("https://evil-verify.example.com/verify?t=1", "https://verify.example.com.evil.com/verify?t=1", GOOD, "https://evil.com/verify?host=verify.example.com"), want=GOOD)
lcheck("L02", "evil-verify.example.com alone rejected", html=H("https://evil-verify.example.com/verify?t=1"), want=None)
lcheck("L03", "verify.example.com.evil.com alone rejected", html=H("https://verify.example.com.evil.com/verify?t=1"), want=None)
lcheck("L04", "userinfo trick https://verify.example.com@evil.com/ rejected", html=H("https://verify.example.com@evil.com/verify"), want=None)
lcheck("L05", "backslash differential https://evil.com\\@verify.example.com/verify rejected (browsers treat \\ as /)",
       html=H("https://evil.com\\@verify.example.com/verify?token=1"), want=None, sev="P0",
       fix="Reject any URL containing backslash, control chars, whitespace or '@' in the authority before parsing; re-parse with a WHATWG-compliant parser or compare urlsplit(url.replace('\\\\','/')).hostname too")
lcheck("L06", "http:// (non-TLS) rejected", html=H("http://verify.example.com/verify?token=1"), want=None)
lcheck("L07", "javascript:/data: schemes rejected", html=H("javascript:alert('verify')", "data:text/html,verify"), want=None)
lcheck("L08", "uppercase scheme/host accepted & returned", html=H("HTTPS://VERIFY.EXAMPLE.COM/Verify?x=1"), want="HTTPS://VERIFY.EXAMPLE.COM/Verify?x=1", sev="P2")
lcheck("L09", "shortest keyword path wins", html=H("https://verify.example.com/a/b/c/verify/long", "https://verify.example.com/verify"), want="https://verify.example.com/verify")
lcheck("L10", "&amp; in href decoded", html=H("https://verify.example.com/confirm?a=1&amp;token=2"), want="https://verify.example.com/confirm?a=1&token=2")
lcheck("L11", "no-keyword magic link (opaque path /l/Xk29s) ignored => None", html=H("https://verify.example.com/l/Xk29s"), want="https://verify.example.com/l/Xk29s", sev="P1",
       fix="Many ATS magic links have no verify|confirm|activate|token keyword (e.g. /l/<opaque>, /auth/<jwt>). Fall back to the single allowlisted https link that isn't unsubscribe/privacy/help")
lcheck("L12", "unsubscribe link with keyword-less path never returned", html=H("https://verify.example.com/unsubscribe"), want=None)
lcheck("L13", "plain-text link with trailing punctuation", plain=f"Verify here: {GOOD}.", want=GOOD)
lcheck("L14", "subdomain of allowlisted host accepted (a.verify.example.com)", html=H("https://a.verify.example.com/verify?t=1"), want="https://a.verify.example.com/verify?t=1", sev="P2",
       fix="Intended per domain_matches, but note: allowlisting a shared parent (example.com) also trusts every user-content subdomain on it. Prefer exact hosts in allowed_link_hosts")
lcheck("L15", "percent-encoded dot host https://verify%2eexample%2ecom/ rejected", html=H("https://verify%2eexample%2ecom.evil.com/verify"), want=None)
lcheck("L16", "IDN homograph host rejected", html=H("https://vеrify.example.com/verify?t=1"), want=None)
lcheck("L17", "trailing-dot FQDN accepted", html=H("https://verify.example.com./verify?t=1"), want="https://verify.example.com./verify?t=1", sev="P2")
lcheck("L18", "port on allowed host accepted (https://verify.example.com:8443/verify)", html=H("https://verify.example.com:8443/verify?t=1"), want=None, sev="P2",
       fix="Non-default ports on a verification link are unusual; consider rejecting")
lcheck("L19", "empty href / whitespace / newline-split href", html=H("", "  https://verify.example.com/verify?t=1\n  "), want="https://verify.example.com/verify?t=1")
lcheck("L20", "empty allowlist -> nothing returned", html=H(GOOD), allow=[], want=None)
lcheck("L21", "allowlist entry '*'/'.' never matches everything", html=H("https://evil.com/verify?t=1"), allow=["*", "."], want=None)
lcheck("L22", "open redirect param inside allowlisted link flagged", html=H("https://verify.example.com/verify?next=https://evil.com"), want="https://verify.example.com/verify?next=https://evil.com", sev="P2",
       fix="Returned as-is by design (never fetched); agent must not follow `next=`/redirect params to other hosts")
rec.case("D01", "domain_matches: substring/suffix confusion (notexample.com vs example.com)", not domain_matches("notexample.com", ["example.com"]), "", "P0")
rec.case("D02", "domain_matches: wildcard-only allowlist entries match nothing", not domain_matches("evil.com", ["*", ".", ""]), "", "P0")
rec.done()
