"""A bounded migration rehearsal, with approvals bound to exact input bytes."""

import csv
import hashlib
import io
import json
import re
import sqlite3
import tempfile
from contextlib import contextmanager
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

import duckdb


class Refused(ValueError):
    """The proposed action has not met its prerequisites."""


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


class Engine:
    def __init__(self, before: Path, current: Path, state: Path):
        self.before, self.current, self.state = before, current, state
        state.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.db() as db:
            db.execute(
                "CREATE TABLE IF NOT EXISTS plans (id TEXT PRIMARY KEY, body TEXT, receipt TEXT)"
            )
            db.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")

    @contextmanager
    def db(self):
        connection = sqlite3.connect(self.state / "state.sqlite3")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def get(self, key: str, default=None):
        with self.db() as db:
            row = db.execute("SELECT value FROM settings WHERE key=?", [key]).fetchone()
        return json.loads(row[0]) if row else default

    def set(self, key: str, value):
        with self.db() as db:
            db.execute("INSERT OR REPLACE INTO settings VALUES (?,?)", [key, json.dumps(value)])

    def invalidate_pending(self):
        with self.db() as db:
            db.execute("DELETE FROM plans WHERE receipt IS NULL")
            db.execute("DELETE FROM settings WHERE key='active'")

    def snapshots(self):
        # DuckDB and the digests consume the same bytes, even if a file changes mid-check.
        return self.before.read_bytes(), self.current.read_bytes()

    def inspect(self):
        old, new = self.snapshots()
        old_columns = next(csv.reader(io.StringIO(old.decode("utf-8"))))
        new_columns = next(csv.reader(io.StringIO(new.decode("utf-8"))))
        with duckdb.connect() as db:
            self.load(db, "candidate", new)
            try:
                db.execute("SELECT order_id, amount, currency FROM candidate").fetchall()
                baseline_error = None
            except duckdb.Error as exc:
                baseline_error = str(exc).splitlines()[0]
        return {
            "before_columns": old_columns,
            "current_columns": new_columns,
            "baseline_error": baseline_error,
            "current_sha256": digest(new),
        }

    @staticmethod
    def load(db, name: str, data: bytes):
        # Materialize the already-hashed snapshot before the temporary file is removed.
        with tempfile.NamedTemporaryFile(suffix=".csv") as snapshot:
            snapshot.write(data)
            snapshot.flush()
            db.execute(
                f"CREATE TABLE {name} AS SELECT * FROM read_csv(?, all_varchar=true)",
                [snapshot.name],
            )

    @staticmethod
    def sql(amount_column: str, units: str):
        if not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*", amount_column):
            raise Refused("Unsupported column name.")
        if units not in {"cents", "dollars"}:
            raise Refused("Confirm source units as cents or dollars first.")
        # Decimal multiplication preserves money precision. Division would produce a double.
        expression = f'CAST("{amount_column}" AS DECIMAL(18,2))'
        if units == "cents":
            expression = (
                f"CAST(\"{amount_column}\" AS DECIMAL(18,0)) * CAST('0.01' AS DECIMAL(3,2))"
            )
        return (
            "SELECT order_id, CAST(" + expression + " AS DECIMAL(18,2)) AS amount, "
            "currency FROM candidate ORDER BY order_id"
        )

    def evaluate(self, old: bytes, new: bytes, sql: str):
        with duckdb.connect() as db:
            self.load(db, "original", old)
            self.load(db, "candidate", new)
            try:
                rows = db.execute(sql).fetchall()
                expected = db.execute(
                    "SELECT order_id, CAST(amount AS DECIMAL(18,2)), currency "
                    "FROM original ORDER BY order_id"
                ).fetchall()
            except duckdb.Error as exc:
                raise Refused("Compatibility query failed: " + str(exc).splitlines()[0]) from exc
        if not rows or any(value is None for row in rows for value in row):
            raise Refused("Empty data or null values: no compatible output was written.")
        if len({row[0] for row in rows}) != len(rows):
            raise Refused("Duplicate order IDs: no compatible output was written.")
        if rows != expected:
            raise Refused(
                "Snapshot parity failed: IDs, amounts, or currencies differ from the baseline."
            )
        return rows, {
            "rows": len(rows),
            "total": str(sum(row[1] for row in rows)),
            "checks": ["nonempty", "no_nulls", "unique_ids", "row_count", "per_order_parity"],
        }

    def preview(self, amount_column: str, units: str, approver: str, confirmation_id: str):
        if not approver or not confirmation_id:
            raise Refused("A human unit-confirmation event is required.")
        old, new = self.snapshots()
        sql = self.sql(amount_column, units)
        for row in csv.DictReader(io.StringIO(new.decode("utf-8"))):
            try:
                value = Decimal(row[amount_column])
                precision = Decimal(1) if units == "cents" else Decimal("0.01")
                if not value.is_finite() or value != value.quantize(precision):
                    raise Refused("Source precision would be lost by this conversion.")
            except (InvalidOperation, TypeError, KeyError) as exc:
                raise Refused(
                    "Missing or invalid amount: no compatible output was written."
                ) from exc
        _, checks = self.evaluate(old, new, sql)
        plan = {
            "version": 1,
            "before_sha256": digest(old),
            "current_sha256": digest(new),
            "amount_column": amount_column,
            "units": units,
            "sql": sql,
            "approver": approver,
            "confirmation_id": confirmation_id,
            "verification": checks,
        }
        body = json.dumps(plan, sort_keys=True)
        plan_id = digest(body.encode())
        with self.db() as db:
            db.execute("INSERT OR IGNORE INTO plans VALUES (?,?,NULL)", [plan_id, body])
        return plan_id, plan

    def approve(self, plan_id: str, sender: str, approval_event: str):
        with self.db() as db:
            row = db.execute("SELECT body,receipt FROM plans WHERE id=?", [plan_id]).fetchone()
            if not row:
                raise Refused("Unknown proposal. Copy the full proposal ID from the preview.")
            plan = json.loads(row[0])
            if sender != plan["approver"] or not approval_event:
                raise Refused("Only the human who confirmed the units can approve this proposal.")
            if row[1]:
                return json.loads(row[1]) | {"already_applied": True}
            old, new = self.snapshots()
            if digest(old) != plan["before_sha256"] or digest(new) != plan["current_sha256"]:
                raise Refused("Input changed after preview. Inspect and confirm units again.")
            rows, checks = self.evaluate(old, new, plan["sql"])
            artifact = self.state / f"{plan_id}.csv"
            buffer = io.StringIO()
            writer = csv.writer(buffer, lineterminator="\n")
            writer.writerow(["order_id", "amount", "currency"])
            writer.writerows(rows)
            output = buffer.getvalue().encode()
            temporary = artifact.with_suffix(".tmp")
            temporary.write_bytes(output)
            temporary.replace(artifact)
            receipt = {
                "proposal_id": plan_id,
                "approval_event": approval_event,
                "approved_by": sender,
                "source_sha256": digest(new),
                "baseline_sha256": digest(old),
                "output_sha256": digest(output),
                "verification": checks,
                "artifact": str(artifact),
                "sql": plan["sql"],
                "applied_at": datetime.now(UTC).isoformat(),
                "scope": "synthetic migration snapshot; local output only",
            }
            db.execute("UPDATE plans SET receipt=? WHERE id=?", [json.dumps(receipt), plan_id])
            (self.state / f"{plan_id}.json").write_text(json.dumps(receipt, indent=2))
        return receipt
