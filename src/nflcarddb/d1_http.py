"""Push data into Cloudflare D1 over its HTTP API.

No Node, no wrangler, no npx. D1 has a plain REST endpoint that takes SQL and an
API token, which is all this needs -- and Python is already installed, because
the collector runs on it.

The only genuinely delicate part is splitting a SQL file into statements. Card
titles are seller-written free text and contain semicolons and apostrophes, so
splitting on ";" would cut a statement in half mid-string and corrupt the upload.
The splitter tracks quote state instead.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Iterator, Optional

log = logging.getLogger(__name__)

API_ROOT = "https://api.cloudflare.com/client/v4"

# D1's HTTP endpoint rejects oversized bodies. Statements are grouped well under
# the limit rather than at it, because one row is far cheaper to retry than one
# enormous batch.
MAX_BATCH_BYTES = 90_000
MAX_BATCH_STATEMENTS = 40


class D1Error(RuntimeError):
    """Cloudflare rejected a request."""


@dataclass
class PushResult:
    statements: int = 0
    batches: int = 0
    retries: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "statements": self.statements,
            "batches": self.batches,
            "retries": self.retries,
            "errors": self.errors[:10],
        }


def split_statements(sql: str) -> Iterator[str]:
    """Yield complete SQL statements, respecting quoted strings and comments.

    Seller titles routinely contain both `;` and `'` -- "Lot of 3; Ja'Marr Chase"
    is an ordinary listing name. Splitting naively on semicolons would break the
    statement there and upload nonsense, so quote state is tracked.
    """
    buf: list[str] = []
    in_string = False
    in_comment = False
    i = 0
    n = len(sql)

    while i < n:
        ch = sql[i]
        nxt = sql[i + 1] if i + 1 < n else ""

        if in_comment:
            buf.append(ch)
            if ch == "\n":
                in_comment = False
            i += 1
            continue

        if in_string:
            buf.append(ch)
            # '' is an escaped quote inside a SQL string, not the end of one.
            if ch == "'":
                if nxt == "'":
                    buf.append(nxt)
                    i += 2
                    continue
                in_string = False
            i += 1
            continue

        if ch == "-" and nxt == "-":
            in_comment = True
            buf.append(ch)
            i += 1
            continue

        if ch == "'":
            in_string = True
            buf.append(ch)
            i += 1
            continue

        if ch == ";":
            statement = "".join(buf).strip()
            if statement:
                yield statement
            buf = []
            i += 1
            continue

        buf.append(ch)
        i += 1

    tail = "".join(buf).strip()
    if tail:
        yield tail


def batch_statements(
    statements: Iterator[str],
    max_bytes: int = MAX_BATCH_BYTES,
    max_count: int = MAX_BATCH_STATEMENTS,
) -> Iterator[list[str]]:
    """Group statements into request-sized batches."""
    batch: list[str] = []
    size = 0
    for statement in statements:
        length = len(statement.encode("utf-8")) + 2
        if batch and (size + length > max_bytes or len(batch) >= max_count):
            yield batch
            batch, size = [], 0
        batch.append(statement)
        size += length
    if batch:
        yield batch


def _post(url: str, token: str, payload: dict, timeout: float = 60.0) -> dict:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:600]
        # 401/403 are worth naming, because the fix differs from a retryable error.
        if exc.code in (401, 403):
            raise D1Error(
                f"Cloudflare refused the token (HTTP {exc.code}). It needs the "
                f"permission Account -> D1 -> Edit.\n{detail}"
            ) from exc
        raise D1Error(f"HTTP {exc.code} from Cloudflare:\n{detail}") from exc
    except urllib.error.URLError as exc:
        raise D1Error(f"Could not reach Cloudflare: {exc.reason}") from exc


def run_sql(account_id: str, database_id: str, token: str, sql: str) -> dict:
    """Send one batch of SQL to D1."""
    url = f"{API_ROOT}/accounts/{account_id}/d1/database/{database_id}/query"
    result = _post(url, token, {"sql": sql})
    if not result.get("success", False):
        messages = "; ".join(
            e.get("message", str(e)) for e in (result.get("errors") or [])
        ) or "unknown error"
        raise D1Error(messages)
    return result


def push_sql(
    account_id: str,
    database_id: str,
    token: str,
    sql: str,
    dry_run: bool = False,
    on_progress=None,
    max_retries: int = 3,
) -> PushResult:
    """Upload a SQL script to D1, in batches, with retries."""
    result = PushResult()
    batches = list(batch_statements(split_statements(sql)))
    total = len(batches)

    for index, batch in enumerate(batches, start=1):
        result.batches += 1
        result.statements += len(batch)
        if on_progress:
            on_progress(index, total, len(batch))
        if dry_run:
            continue

        payload = ";\n".join(batch) + ";"
        for attempt in range(max_retries + 1):
            try:
                run_sql(account_id, database_id, token, payload)
                break
            except D1Error as exc:
                message = str(exc)
                # A refused token or bad SQL will not improve with another go.
                if "refused the token" in message or attempt == max_retries:
                    result.errors.append(f"batch {index}: {message}")
                    raise
                result.retries += 1
                wait = min(30.0, 2 ** attempt)
                log.warning("batch %s failed (%s); retrying in %.0fs",
                            index, message[:120], wait)
                time.sleep(wait)

    return result


def verify(account_id: str, database_id: str, token: str) -> dict:
    """Ask D1 what it now holds, so success is confirmed rather than assumed."""
    out = run_sql(
        account_id, database_id, token,
        "SELECT COUNT(*) AS sales, MIN(sold_date) AS first_day, "
        "MAX(sold_date) AS last_day FROM sales;",
    )
    rows = (out.get("result") or [{}])[0].get("results") or [{}]
    return rows[0] if rows else {}
