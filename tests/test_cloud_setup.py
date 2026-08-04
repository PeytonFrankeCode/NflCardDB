"""One-shot API setup.

Nothing here talks to Cloudflare -- wrangler is replaced. What is tested is the
part that actually breaks: reading ids and URLs out of wrangler's output, and
patching them into wrangler.toml.
"""

import pytest

from nflcarddb import cloud_setup as cs


def test_reads_the_database_id_from_a_create(tmp_path):
    """wrangler prints a TOML block it expects you to paste by hand."""
    output = """
 ⛅️ wrangler 3.90.0
✅ Successfully created DB 'nflcarddb' in region ENAM

[[d1_databases]]
binding = "DB"
database_name = "nflcarddb"
database_id = "a1b2c3d4-5678-90ab-cdef-1234567890ab"
"""
    assert cs.extract_database_id(output) == "a1b2c3d4-5678-90ab-cdef-1234567890ab"


def test_reads_the_database_id_from_info_json():
    """`d1 info` reports it as uuid instead, so both shapes must be handled."""
    output = '{"name":"nflcarddb","uuid":"11112222-3333-4444-5555-666677778888"}'
    assert cs.extract_database_id(output) == "11112222-3333-4444-5555-666677778888"


def test_no_database_id_returns_none():
    assert cs.extract_database_id("some unrelated error") is None
    assert cs.extract_database_id("") is None


def test_reads_the_worker_url_from_a_deploy():
    output = """
Total Upload: 12.34 KiB / gzip: 4.56 KiB
Uploaded nflcarddb-api (2.11 sec)
Published nflcarddb-api (0.98 sec)
  https://nflcarddb-api.peyton.workers.dev
Current Deployment ID: abcdef
"""
    assert cs.extract_worker_url(output) == "https://nflcarddb-api.peyton.workers.dev"


def test_no_worker_url_returns_none():
    assert cs.extract_worker_url("deploy failed") is None


def test_writes_the_id_over_the_placeholder(tmp_path):
    toml = tmp_path / "wrangler.toml"
    toml.write_text(
        'name = "nflcarddb-api"\n\n[[d1_databases]]\n'
        'binding = "DB"\ndatabase_id = "PASTE_YOUR_DATABASE_ID_HERE"\n'
    )
    assert cs.write_database_id("abc-123", toml) is True
    text = toml.read_text()
    assert 'database_id = "abc-123"' in text
    assert cs.PLACEHOLDER not in text


def test_replaces_an_existing_id_on_a_rerun(tmp_path):
    """Setup is re-runnable, so a second run must overwrite, not duplicate."""
    toml = tmp_path / "wrangler.toml"
    toml.write_text('[[d1_databases]]\ndatabase_id = "old-id-here"\n')

    assert cs.write_database_id("new-id-here", toml) is True
    text = toml.read_text()
    assert 'database_id = "new-id-here"' in text
    assert "old-id-here" not in text
    assert text.count("database_id") == 1


def test_writing_the_same_id_twice_reports_no_change(tmp_path):
    toml = tmp_path / "wrangler.toml"
    toml.write_text('database_id = "same-id"\n')
    assert cs.write_database_id("same-id", toml) is False


def test_existing_database_is_reused_rather_than_recreated(monkeypatch):
    """Re-running setup must not try to create a database that is already there."""
    calls = []

    class Proc:
        def __init__(self, out=""):
            self.stdout, self.stderr, self.returncode = out, "", 0

    def fake_run(args, cwd=None, interactive=False, check=True):
        calls.append(args)
        if args[:2] == ["d1", "info"]:
            return Proc('{"uuid":"existing-1111-2222-3333-444455556666"}')
        raise AssertionError(f"should not have run {args}")

    monkeypatch.setattr(cs, "run_wrangler", fake_run)
    result = cs.SetupResult()
    db_id = cs.ensure_database(result)

    assert db_id == "existing-1111-2222-3333-444455556666"
    assert ["d1", "create", "nflcarddb"] not in calls
    assert "already exists" in result.steps[0]


def test_missing_database_is_created(monkeypatch):
    class Proc:
        def __init__(self, out="", code=0):
            self.stdout, self.stderr, self.returncode = out, "", code

    def fake_run(args, cwd=None, interactive=False, check=True):
        if args[:2] == ["d1", "info"]:
            return Proc("could not find nflcarddb", 1)
        if args[:2] == ["d1", "create"]:
            return Proc('database_id = "brand-new-1111-2222-3333-444455556666"')
        raise AssertionError(args)

    monkeypatch.setattr(cs, "run_wrangler", fake_run)
    result = cs.SetupResult()
    assert cs.ensure_database(result) == "brand-new-1111-2222-3333-444455556666"
    assert "created database" in result.steps[0]


def test_create_without_a_readable_id_is_an_error(monkeypatch):
    class Proc:
        def __init__(self, out="", code=0):
            self.stdout, self.stderr, self.returncode = out, "", code

    def fake_run(args, cwd=None, interactive=False, check=True):
        return Proc("something unexpected", 0)

    monkeypatch.setattr(cs, "run_wrangler", fake_run)
    with pytest.raises(cs.SetupError, match="could not find its id"):
        cs.ensure_database(cs.SetupResult())


def test_setup_result_serialises():
    r = cs.SetupResult(database_id="d", worker_url="https://x.workers.dev",
                       api_key="nfl_abc", rows_uploaded=10, steps=["a"])
    payload = r.as_dict()
    assert payload["worker_url"] == "https://x.workers.dev"
    assert payload["rows_uploaded"] == 10


def test_the_unedited_placeholder_is_not_mistaken_for_an_id():
    """wrangler.toml ships with a placeholder that is itself id-shaped."""
    shipped = 'database_id = "PASTE_YOUR_DATABASE_ID_HERE"'
    assert cs.extract_database_id(shipped) is None


def test_non_hex_ids_are_still_read():
    """Ids are UUIDs today; a hex-only pattern would break silently if not."""
    assert cs.extract_database_id('database_id = "db-prod-westcoast-01x"') \
        == "db-prod-westcoast-01x"
