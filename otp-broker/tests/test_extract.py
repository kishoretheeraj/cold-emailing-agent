"""Regex corpus: OTP emails (plain/HTML/multipart) and links."""
from email.message import EmailMessage

import pytest

from extract import AmbiguousCode, domain_matches, extract_code, extract_link, message_texts


def mk(plain: str | None = None, html: str | None = None) -> EmailMessage:
    m = EmailMessage()
    m["From"] = "noreply@example.com"
    if plain is not None:
        m.set_content(plain)
        if html is not None:
            m.add_alternative(html, subtype="html")
    elif html is not None:
        m.set_content(html, subtype="html")
    return m


# (plain, html, expected)
CORPUS = [
    ("Your verification code is 483920", None, "483920"),
    ("Your code: 123456. It expires in 10 minutes.", None, "123456"),
    ("Use OTP 7421 to sign in.", None, "7421"),
    ("Passcode - 90817265", None, "90817265"),
    ("Your PIN is 5521", None, "5521"),
    ("Verification code:\n\n   384 291\n", None, "384291"),
    ("Your code is 12-34-56", None, "123456"),
    ("Security code 1234 5678 do not share", None, "12345678"),
    ("VERIFICATION CODE: 665544", None, "665544"),
    ("Enter code 483920 within 5 minutes", None, "483920"),
    ("Your code is 483920 and 483920 again", None, "483920"),
    (None, "<p>Your code is <b>483920</b></p>", "483920"),
    (None, "<p>Your code: 4<b>8</b>3<i>9</i>20</p>", "483920"),
    (None, "<table><tr><td>Verification code</td></tr><tr><td><strong>552201</strong></td></tr></table>", "552201"),
    (None, "<style>.code{color:red}</style><div>Your one-time passcode is 998877</div>", "998877"),
    ("Your code is 246810", "<p>Your code is <b>246810</b></p>", "246810"),
    ("Your code is in the HTML part", "<p>Verification code: <span>31</span><span>7755</span></p>", "317755"),
    ("Hi!\nYour verification code is 112233.\nCall us at 800-555-1234.", None, "112233"),
    ("Order total $1234.56. Your code is 778899", None, "778899"),
    ("Your code is 778899. Support: +1 (603) 555-0199 ext", None, "778899"),
    ("Code : 654321", None, "654321"),
    ("Your code is 111111 (previous code 222222 is now void)", None, "111111"),
]

# Messages that must yield no code
NEGATIVE = [
    "Your order #483920 has shipped.",
    "Call 603 555 1234 for help",
    "Your balance is $483,920.00",
    "Your total is 1234 USD, code review pending",
    "Zip code 03766 is on file",  # 5-digit zip after "code" — see below
    "Ref 12345678 no keywords here",
    "Your code is 123",
    "Your code is 123456789",
]


@pytest.mark.parametrize("plain,html,expected", CORPUS)
def test_corpus(plain, html, expected):
    assert extract_code(message_texts(mk(plain, html))) == expected


@pytest.mark.parametrize("text", [t for t in NEGATIVE if "Zip" not in t])
def test_negative(text):
    assert extract_code(message_texts(mk(text))) is None


def test_corpus_size():
    assert len(CORPUS) >= 20


def test_more_than_two_distinct_is_conflict():
    with pytest.raises(AmbiguousCode):
        extract_code(["code is 111111 code is 222222 code is 333333"])


def test_two_distinct_is_conflict():
    with pytest.raises(AmbiguousCode):
        extract_code(["code is 111111 code is 222222"])


def test_plain_and_html_disagreeing_is_conflict():
    with pytest.raises(AmbiguousCode):
        extract_code(message_texts(mk("Your code is 111111", "<p>Your code is 222222</p>")))


@pytest.mark.parametrize("text", [
    "Shipping to zip code 90210", "Use promo code 2024 for 10% off", "Allow verification 10-15 minutes",
    "Your code is ٤٨٣٩٢٠", "Your code is ４８３９２０",
])
def test_false_positive_guards(text):
    assert extract_code([text]) is None


@pytest.mark.parametrize("text,want", [
    ("123456 is your verification code", "123456"),
    ("Use 482913 as your code", "482913"),
    ("Your one-time password is 482913", "482913"),
    ("Your code is **482913**", "482913"),
    ("Your code is [482913]", "482913"),
    ("Your verification code: 483\u00a0920", "483920"),
    ("Your code is 48\u200b3920", "483920"),
    ("Copy this into the security code field on your application:\n\nA7bX92Qd", "A7bX92Qd"),
])
def test_extra_formats(text, want):
    assert extract_code([text]) == want


def test_digit_per_cell_and_hidden_decoy():
    cells = "<p>Your code:</p><table><tr>" + "".join(f"<td>{d}</td>" for d in "483920") + "</tr></table>"
    assert extract_code(message_texts(mk(html=cells))) == "483920"
    decoy = "<span style='display:none'>verification code 000000</span><p>Your verification code is 123456</p>"
    assert extract_code(message_texts(mk(html=decoy))) == "123456"


def test_link_hardening_and_opaque_fallback():
    ok = ["verify.example.com"]
    assert extract_link(mk("x", '<a href="https://evil.com\\@verify.example.com/verify?t=1">l</a>'), ok) is None
    assert extract_link(mk("x", '<a href="https://verify.example.com:8443/verify">l</a>'), ok) is None
    assert extract_link(mk("x", '<a href="https://verify.example.com/l/Xk29s">l</a>'), ok) == "https://verify.example.com/l/Xk29s"
    two = '<a href="https://verify.example.com/l/a">l</a><a href="https://verify.example.com/l/b">l</a>'
    assert extract_link(mk("x", two), ok) is None
    assert extract_link(mk("x", '<a href="https://verify.example.com/unsubscribe">l</a>'), ok) is None


def test_attachments_ignored():
    m = mk("no code here")
    m.add_attachment(b"verification code 999999", maintype="text", subtype="plain", filename="x.txt")
    assert extract_code(message_texts(m)) is None


# ---- links ----------------------------------------------------------------
LINK_HTML = """
<a href="https://example.com/unsubscribe">Unsub</a>
<a href="https://example.com/account/verify/email?token=abc">Verify</a>
<a href="https://example.com/v/confirm">Confirm</a>
<a href="https://evil-example.com/verify">Evil</a>
<a href="https://example.com.evil.io/verify">Evil2</a>
<a href="https://evil.io/?u=example.com/verify">Evil3</a>
<a href="https://user@evil.io/verify">Evil4</a>
<a href="http://example.com/verify">Plain http</a>
"""


def test_link_picks_shortest_keyword_path_on_allowlist():
    assert extract_link(mk("x", LINK_HTML), ["example.com"]) == "https://example.com/v/confirm"


def test_link_subdomain_ok_and_no_substring():
    html = '<a href="https://mail.example.com/activate">a</a><a href="https://notexample.com/verify">b</a>'
    assert extract_link(mk("x", html), ["example.com"]) == "https://mail.example.com/activate"
    assert extract_link(mk("x", '<a href="https://notexample.com/verify">b</a>'), ["example.com"]) is None


def test_link_requires_keyword():
    assert extract_link(mk("x", '<a href="https://example.com/privacy">p</a>'), ["example.com"]) is None


def test_link_in_plain_text_and_query_token():
    m = mk("Click https://example.com/auth?token=xyz. Thanks")
    assert extract_link(m, ["example.com"]) == "https://example.com/auth?token=xyz"


def test_domain_matches():
    assert domain_matches("a.b.example.com", ["example.com"])
    assert not domain_matches("example.com.evil.io", ["example.com"])
    assert not domain_matches(None, ["example.com"])
