"""One-shot setup of the hosted API.

Doing this by hand is six commands, two files to edit and a value to copy
between them, which is a lot of places to go wrong for something that only
happens once. This drives wrangler and does the copying itself.

Safe to re-run: the database is created only if missing, the schema and the data
import are both upserts, and re-running deploy just replaces the Worker.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

API_DIR = Path("api")
DB_NAME = "nflcarddb"

# wrangler prints the id inside a TOML block it wants you to paste.
# Ids are UUIDs today, but the pattern accepts any long id-shaped token rather
# than hex only -- a stricter regex would fail silently if Cloudflare ever
# changed the format, and silently is the worst way for setup to break.
PLACEHOLDER = "PASTE_YOUR_DATABASE_ID_HERE"

DATABASE_ID_RE = re.compile(r'database_id\s*=\s*"([0-9A-Za-z-]{16,})"')
# ...and reports it as JSON from `d1 info`, where the key is uuid.
UUID_RE = re.compile(r'"uuid"\s*:\s*"([0-9A-Za-z-]{16,})"')
WORKER_URL_RE = re.compile(r"https://[A-Za-z0-9._-]+\.workers\.dev")


class SetupError(RuntimeError):
    """A step failed in a way the user has to resolve."""


@dataclass
class SetupResult:
    database_id: Optional[str] = None
    worker_url: Optional[str] = None
    api_key: Optional[str] = None
    key_hash: Optional[str] = None
    rows_uploaded: int = 0
    steps: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "database_id": self.database_id,
            "worker_url": self.worker_url,
            "api_key": self.api_key,
            "rows_uploaded": self.rows_uploaded,
            "steps": self.steps,
        }


def _npx() -> str:
    exe = shutil.which("npx") or shutil.which("npx.cmd")
    if not exe:
        raise SetupError(
            "Node.js is needed to talk to Cloudflare.\n"
            "Install it from https://nodejs.org/ then run this again."
        )
    return exe


def run_wrangler(args: list[str], cwd: Path = API_DIR, interactive: bool = False,
                 check: bool = True) -> subprocess.CompletedProcess:
    """Run a wrangler command inside api/."""
    cmd = [_npx(), "wrangler", *args]
    log.debug("running %s", " ".join(cmd))
    if interactive:
        # `wrangler login` opens a browser and waits; it needs the real console.
        proc = subprocess.run(cmd, cwd=cwd)
        return subprocess.CompletedProcess(cmd, proc.returncode, "", "")

    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise SetupError(
            f"`wrangler {' '.join(args)}` failed:\n"
            f"{(proc.stderr or proc.stdout).strip()[:800]}"
        )
    return proc


def extract_database_id(text: str) -> Optional[str]:
    """Pull a database id out of wrangler's output, whichever shape it used."""
    for pattern in (DATABASE_ID_RE, UUID_RE):
        m = pattern.search(text or "")
        # The unedited config still contains the placeholder, which is
        # id-shaped enough to match; treating it as an id would point the
        # Worker at nothing.
        if m and m.group(1) != PLACEHOLDER:
            return m.group(1)
    return None


def extract_worker_url(text: str) -> Optional[str]:
    m = WORKER_URL_RE.search(text or "")
    return m.group(0) if m else None


def ensure_database(result: SetupResult) -> str:
    """Create the D1 database, or find the existing one's id."""
    existing = run_wrangler(["d1", "info", DB_NAME], check=False)
    db_id = extract_database_id(existing.stdout + existing.stderr)
    if db_id:
        result.steps.append(f"database {DB_NAME} already exists")
        return db_id

    created = run_wrangler(["d1", "create", DB_NAME])
    db_id = extract_database_id(created.stdout + created.stderr)
    if not db_id:
        raise SetupError(
            "Created the database but could not find its id in wrangler's "
            "output. Run `npx wrangler d1 info nflcarddb` in the api folder and "
            "paste the id into api/wrangler.toml by hand."
        )
    result.steps.append(f"created database {DB_NAME}")
    return db_id


def write_database_id(db_id: str, toml_path: Path = API_DIR / "wrangler.toml") -> bool:
    """Put the id into wrangler.toml. Returns True if the file changed."""
    text = toml_path.read_text(encoding="utf-8")
    if PLACEHOLDER in text:
        updated = text.replace(PLACEHOLDER, db_id)
    else:
        updated = re.sub(r'(database_id\s*=\s*")[^"]*(")', rf"\g<1>{db_id}\g<2>", text)
    if updated == text:
        return False
    toml_path.write_text(updated, encoding="utf-8")
    return True


def setup(db_path: str, label: str = "website", skip_login: bool = False) -> SetupResult:
    """Create everything and return the values the website needs."""
    from .api_export import export_api_sql, new_api_key

    if not API_DIR.exists():
        raise SetupError("Run this from the project folder -- no api/ directory here.")

    result = SetupResult()

    if not skip_login:
        # Not fatal: an already-authenticated machine returns non-zero on some
        # versions, and the next command will fail clearly if login truly failed.
        run_wrangler(["login"], interactive=True, check=False)
        result.steps.append("signed in to Cloudflare")

    result.database_id = ensure_database(result)
    if write_database_id(result.database_id):
        result.steps.append("wrote the database id into api/wrangler.toml")

    run_wrangler(["d1", "execute", DB_NAME, "--remote", "--file=schema.sql", "-y"],
                 check=False)
    result.steps.append("created the tables")

    key, key_hash = new_api_key()
    result.api_key, result.key_hash = key, key_hash
    result.steps.append("minted an API key")

    stats = export_api_sql(db_path, API_DIR / "import.sql",
                           key_hashes=[(key_hash, label)])
    result.rows_uploaded = stats["rows"]
    result.steps.append(f"prepared {stats['rows']} rows for upload")

    run_wrangler(["d1", "execute", DB_NAME, "--remote", "--file=import.sql", "-y"])
    result.steps.append("uploaded the data")

    deployed = run_wrangler(["deploy"])
    result.worker_url = extract_worker_url(deployed.stdout + deployed.stderr)
    result.steps.append("deployed the API")

    if not result.worker_url:
        raise SetupError(
            "Deployed, but could not find the URL in wrangler's output. "
            "Look under Workers & Pages in the Cloudflare dashboard -- the "
            "Worker is called nflcarddb-api."
        )
    return result
