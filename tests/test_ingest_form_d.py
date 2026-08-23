"""
Tests for ingest_form_d -- SEC Form D funding-signal ingestion.

Fixtures reproduce the real 2025Q4 DERA layout (three tab-separated tables
joined on ACCESSIONNUMBER). No network access.
"""

import io
import zipfile

import pytest

import ingest_form_d


# ── Fixture construction ───────────────────────────────────────────────────────

_SUBMISSION_COLS = ["ACCESSIONNUMBER", "FILE_NUM", "FILING_DATE", "SIC_CODE",
                    "SCHEMAVERSION", "SUBMISSIONTYPE", "TESTORLIVE"]
_ISSUER_COLS = ["ACCESSIONNUMBER", "IS_PRIMARYISSUER_FLAG", "ISSUER_SEQ_KEY",
                "CIK", "ENTITYNAME", "ENTITYTYPE"]
_OFFERING_COLS = ["ACCESSIONNUMBER", "INDUSTRYGROUPTYPE",
                  "ISPOOLEDINVESTMENTFUNDTYPE", "SALE_DATE",
                  "TOTALOFFERINGAMOUNT", "TOTALAMOUNTSOLD"]


def _write_tsv(path, columns, rows):
    lines = ["\t".join(columns)]
    for row in rows:
        lines.append("\t".join(str(row.get(c, "")) for c in columns))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _build_quarter(tmp_path, submissions, issuers, offerings):
    d = tmp_path / "2025Q4_d"
    d.mkdir(exist_ok=True)
    _write_tsv(d / "FORMDSUBMISSION.tsv", _SUBMISSION_COLS, submissions)
    _write_tsv(d / "ISSUERS.tsv", _ISSUER_COLS, issuers)
    _write_tsv(d / "OFFERING.tsv", _OFFERING_COLS, offerings)
    return d


def _filing(acc, name, date, amount, pooled_flag="", industry="Other Technology",
            testorlive="LIVE", primary="YES", subtype="D"):
    return (
        {"ACCESSIONNUMBER": acc, "FILING_DATE": date,
         "SUBMISSIONTYPE": subtype, "TESTORLIVE": testorlive},
        {"ACCESSIONNUMBER": acc, "IS_PRIMARYISSUER_FLAG": primary,
         "ENTITYNAME": name, "CIK": "0001", "ENTITYTYPE": "Corporation"},
        {"ACCESSIONNUMBER": acc, "INDUSTRYGROUPTYPE": industry,
         "ISPOOLEDINVESTMENTFUNDTYPE": pooled_flag,
         "TOTALAMOUNTSOLD": amount, "TOTALOFFERINGAMOUNT": amount},
    )


def _parse(tmp_path, filings):
    subs, isss, offs = [], [], []
    for s, i, o in filings:
        subs.append(s); isss.append(i); offs.append(o)
    return ingest_form_d.parse_form_d_quarter(_build_quarter(tmp_path, subs, isss, offs))


# ── Date parsing ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("31-DEC-2025", "2025-12-31"),
    ("01-JAN-2026", "2026-01-01"),
    ("09-DEC-2025", "2025-12-09"),
])
def test_parse_filing_date_handles_dd_mon_yyyy(raw, expected):
    assert ingest_form_d.parse_filing_date(raw) == expected


@pytest.mark.parametrize("raw", ["", None, "2025-12-31", "garbage", "31-XXX-2025"])
def test_parse_filing_date_degrades_to_none(raw):
    assert ingest_form_d.parse_filing_date(raw) is None


# ── Amount parsing ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("4082050250", 4082050250),
    ("1000", 1000),
    (" 250 ", 250),
])
def test_parse_amount_valid(raw, expected):
    assert ingest_form_d.parse_amount(raw) == expected


@pytest.mark.parametrize("raw", ["", None, "0", "-5", "abc", "1.5e9"])
def test_parse_amount_rejects_non_positive_integers(raw):
    assert ingest_form_d.parse_amount(raw) is None


# ── Row filtering ──────────────────────────────────────────────────────────────

def test_keeps_a_real_operating_company_raise(tmp_path):
    records = _parse(tmp_path, [
        _filing("a1", "Databricks, Inc.", "31-DEC-2025", "4082050250"),
    ])
    assert len(records) == 1
    assert records[0]["issuer_name"] == "Databricks, Inc."
    assert records[0]["amount"] == 4082050250
    assert records[0]["filing_date"] == "2025-12-31"


def test_excludes_pooled_fund_by_boolean_flag(tmp_path):
    records = _parse(tmp_path, [
        _filing("a1", "Some Venture Fund LP", "31-DEC-2025", "9000", pooled_flag="true"),
    ])
    assert records == []


def test_excludes_pooled_fund_by_industry_group_when_boolean_blank(tmp_path):
    """Real 2025Q4 data: 222 rows leave the boolean blank but declare the industry."""
    records = _parse(tmp_path, [
        _filing("a1", "McCarthy Investment Company, LLC", "20-OCT-2025", "9000",
                pooled_flag="", industry="Pooled Investment Fund"),
    ])
    assert records == []


def test_excludes_non_primary_issuer(tmp_path):
    records = _parse(tmp_path, [
        _filing("a1", "Subsidiary Co", "31-DEC-2025", "9000", primary="NO"),
    ])
    assert records == []


def test_primary_issuer_flag_is_yes_not_y(tmp_path):
    """Regression: a `== 'Y'` test would silently drop every row."""
    records = _parse(tmp_path, [
        _filing("a1", "Real Co", "31-DEC-2025", "9000", primary="YES"),
    ])
    assert len(records) == 1


def test_excludes_test_filings(tmp_path):
    records = _parse(tmp_path, [
        _filing("a1", "Test Co", "31-DEC-2025", "9000", testorlive="TEST"),
    ])
    assert records == []


def test_excludes_unparseable_amounts(tmp_path):
    records = _parse(tmp_path, [
        _filing("a1", "No Amount Co", "31-DEC-2025", ""),
        _filing("a2", "Zero Co", "31-DEC-2025", "0"),
    ])
    assert records == []


def test_row_with_unparseable_date_is_skipped(tmp_path):
    records = _parse(tmp_path, [
        _filing("a1", "Bad Date Co", "not-a-date", "9000"),
    ])
    assert records == []


# ── Aggregation ────────────────────────────────────────────────────────────────

def test_latest_filing_wins_for_same_issuer():
    acc = {}
    ingest_form_d.fold_issuer(acc, {"issuer_name": "Acme Inc",
                                    "filing_date": "2025-01-01", "amount": 100})
    ingest_form_d.fold_issuer(acc, {"issuer_name": "Acme Inc",
                                    "filing_date": "2025-12-31", "amount": 500})
    rows = ingest_form_d.build_rows_for_upsert(acc)
    assert len(rows) == 1
    assert rows[0]["last_funding_date"] == "2025-12-31"
    assert rows[0]["last_funding_amount"] == 500


def test_amendment_supersedes_earlier_filing():
    """D/A amendments update a prior raise; latest-filing-wins handles it."""
    acc = {}
    ingest_form_d.fold_issuer(acc, {"issuer_name": "Acme Inc",
                                    "filing_date": "2025-06-01", "amount": 100})
    ingest_form_d.fold_issuer(acc, {"issuer_name": "ACME, INC.",
                                    "filing_date": "2025-07-01", "amount": 750})
    rows = ingest_form_d.build_rows_for_upsert(acc)
    assert len(rows) == 1, "normalized issuer names must collapse to one row"
    assert rows[0]["last_funding_amount"] == 750


def test_tie_on_date_breaks_toward_larger_amount():
    acc = {}
    ingest_form_d.fold_issuer(acc, {"issuer_name": "Acme Inc",
                                    "filing_date": "2025-12-31", "amount": 100})
    ingest_form_d.fold_issuer(acc, {"issuer_name": "Acme Inc",
                                    "filing_date": "2025-12-31", "amount": 900})
    rows = ingest_form_d.build_rows_for_upsert(acc)
    assert rows[0]["last_funding_amount"] == 900


def test_upsert_rows_carry_source_and_normalized_name():
    acc = {}
    ingest_form_d.fold_issuer(acc, {"issuer_name": "Acme, Inc.",
                                    "filing_date": "2025-12-31", "amount": 100})
    row = ingest_form_d.build_rows_for_upsert(acc)[0]
    assert row["last_funding_source"] == "sec_form_d"
    assert row["issuer_name"] == "Acme, Inc."
    assert row["normalized_name"] == ingest_form_d.entity_resolution.normalize("Acme, Inc.")


# ── Link discovery ─────────────────────────────────────────────────────────────

_INDEX_HTML = """
<a href="/files/datastandardsinnovation/data/form-d-data-sets/2026q2_d.zip">2026 Q2</a>
<a href="/files/structureddata/data/form-d-data-sets/2026q1_d.zip">2026 Q1</a>
<a href="/files/structureddata/data/form-d-data-sets/2025q4_d.zip">2025 Q4</a>
<a href="/files/other/unrelated.zip">not form d</a>
"""


def test_link_discovery_tolerates_both_observed_path_prefixes():
    """
    The newest quarter lives under /files/datastandardsinnovation/ while older
    ones use /files/structureddata/. Hardcoding either prefix breaks ingestion.
    """
    found = ingest_form_d.parse_form_d_links(_INDEX_HTML)
    assert found["2026q2"].endswith(
        "/files/datastandardsinnovation/data/form-d-data-sets/2026q2_d.zip")
    assert found["2025q4"].endswith(
        "/files/structureddata/data/form-d-data-sets/2025q4_d.zip")
    assert all(k.endswith(("q1", "q2", "q3", "q4")) for k in found)


def test_link_discovery_returns_absolute_urls():
    for url in ingest_form_d.parse_form_d_links(_INDEX_HTML).values():
        assert url.startswith("https://www.sec.gov/")


def test_link_discovery_ignores_unrelated_zips():
    assert not any("unrelated" in u
                   for u in ingest_form_d.parse_form_d_links(_INDEX_HTML).values())


def test_link_discovery_never_raises_on_garbage():
    assert ingest_form_d.parse_form_d_links("<html>nothing here</html>") == {}
    assert ingest_form_d.parse_form_d_links("") == {}


# ── Download + extract ────────────────────────────────────────────────────────

def _tsv_text(columns, rows):
    lines = ["\t".join(columns)]
    for row in rows:
        lines.append("\t".join(str(row.get(c, "")) for c in columns))
    return "\n".join(lines) + "\n"


def _quarter_zip_bytes(prefix=""):
    submission, issuer, offering = _filing("a1", "Databricks, Inc.", "31-DEC-2025", "4082050250")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(prefix + "FORMDSUBMISSION.tsv", _tsv_text(_SUBMISSION_COLS, [submission]))
        zf.writestr(prefix + "ISSUERS.tsv", _tsv_text(_ISSUER_COLS, [issuer]))
        zf.writestr(prefix + "OFFERING.tsv", _tsv_text(_OFFERING_COLS, [offering]))
    return buf.getvalue()


def _mock_urlopen(mocker, payload):
    mock_resp = mocker.MagicMock()
    mock_resp.read.return_value = payload
    mock_urlopen = mocker.patch("urllib.request.urlopen")
    mock_urlopen.return_value.__enter__.return_value = mock_resp
    return mock_urlopen


def test_download_quarter_extracts_nested_members_and_round_trips(tmp_path, mocker):
    """SEC's own archive layout is opaque offline -- the real contract is with
    parse_form_d_quarter, which reads exact filenames from dest_dir. Prove the
    round trip, not just that files exist."""
    _mock_urlopen(mocker, _quarter_zip_bytes(prefix="2025q4_d/"))
    dest = tmp_path / "2025q4"

    ingest_form_d.download_quarter(
        "https://www.sec.gov/files/structureddata/data/form-d-data-sets/2025q4_d.zip", dest)

    records = ingest_form_d.parse_form_d_quarter(dest)
    assert len(records) == 1
    assert records[0]["issuer_name"] == "Databricks, Inc."
    assert records[0]["amount"] == 4082050250


def test_download_quarter_extracts_flat_members(tmp_path, mocker):
    _mock_urlopen(mocker, _quarter_zip_bytes(prefix=""))
    dest = tmp_path / "2025q4"

    ingest_form_d.download_quarter(
        "https://www.sec.gov/files/datastandardsinnovation/data/form-d-data-sets/2025q4_d.zip", dest)

    records = ingest_form_d.parse_form_d_quarter(dest)
    assert len(records) == 1
    assert records[0]["issuer_name"] == "Databricks, Inc."


def test_download_quarter_raises_when_a_table_is_missing(tmp_path, mocker):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("FORMDSUBMISSION.tsv", "ACCESSIONNUMBER\n")
        zf.writestr("ISSUERS.tsv", "ACCESSIONNUMBER\n")
        # OFFERING.tsv omitted
    _mock_urlopen(mocker, buf.getvalue())

    with pytest.raises(ValueError, match="OFFERING.tsv"):
        ingest_form_d.download_quarter("https://www.sec.gov/x/2025q4_d.zip", tmp_path / "2025q4")


# ── Never-raises sweep ─────────────────────────────────────────────────────────

def test_missing_tables_degrade_to_no_signal(tmp_path):
    empty = tmp_path / "empty_q"
    empty.mkdir()
    assert ingest_form_d.parse_form_d_quarter(empty) == []


def test_malformed_tsv_degrades_to_no_signal(tmp_path):
    d = tmp_path / "bad_q"
    d.mkdir()
    for name in ("FORMDSUBMISSION.tsv", "ISSUERS.tsv", "OFFERING.tsv"):
        (d / name).write_text("this is not a tsv header\x00\x01", encoding="utf-8")
    assert ingest_form_d.parse_form_d_quarter(d) == []
