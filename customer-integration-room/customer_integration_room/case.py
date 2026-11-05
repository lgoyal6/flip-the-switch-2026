"""Durable case state and human-bound approval receipts."""

from __future__ import annotations

import json
import sqlite3
from enum import StrEnum
from pathlib import Path
from typing import Any


class CaseState(StrEnum):
    OPEN = "open"
    INVESTIGATING = "investigating"
    EVIDENCE_REVIEW = "evidence_review"
    EVIDENCE_SHARED = "evidence_shared"
    DECISION_REQUIRED = "decision_required"
    PATCH_PROPOSED = "patch_proposed"
    PATCH_APPROVED = "patch_approved"
    VERIFYING = "verifying"
    CUSTOMER_ACCEPTED = "customer_accepted"
    CONTRACT_GENERATED = "contract_generated"


class CaseJournal:
    def __init__(self, path: Path, case_id: str):
        self.path = path
        self.case_id = case_id
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS case_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    case_id TEXT NOT NULL,
                    state TEXT NOT NULL,
                    body TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS approvals (
                    approval_id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL,
                    subject_id TEXT NOT NULL,
                    subject_sha256 TEXT NOT NULL,
                    approved_by TEXT NOT NULL,
                    body TEXT NOT NULL
                );
                """
            )

    def _db(self):
        return sqlite3.connect(self.path)

    def transition(self, state: CaseState, body: dict[str, Any]) -> None:
        with self._db() as db:
            db.execute(
                "INSERT INTO case_events(case_id, state, body) VALUES (?, ?, ?)",
                (self.case_id, state.value, json.dumps(body, sort_keys=True)),
            )

    def current_state(self) -> CaseState | None:
        with self._db() as db:
            row = db.execute(
                "SELECT state FROM case_events WHERE case_id=? ORDER BY id DESC LIMIT 1",
                (self.case_id,),
            ).fetchone()
        return CaseState(row[0]) if row else None

    def approve(
        self,
        approval_id: str,
        subject_id: str,
        subject_sha256: str,
        approved_by: str,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        if not approved_by:
            raise ValueError("A human approver is required")
        with self._db() as db:
            row = db.execute(
                "SELECT body FROM approvals WHERE approval_id=?", (approval_id,)
            ).fetchone()
            if row:
                return json.loads(row[0]) | {"already_recorded": True}
            receipt = {
                "approval_id": approval_id,
                "case_id": self.case_id,
                "subject_id": subject_id,
                "subject_sha256": subject_sha256,
                "approved_by": approved_by,
                "body": body,
            }
            db.execute(
                "INSERT INTO approvals VALUES (?, ?, ?, ?, ?, ?)",
                (
                    approval_id,
                    self.case_id,
                    subject_id,
                    subject_sha256,
                    approved_by,
                    json.dumps(receipt, sort_keys=True),
                ),
            )
            return receipt

    def events(self) -> list[dict[str, Any]]:
        with self._db() as db:
            rows = db.execute(
                "SELECT state, body FROM case_events WHERE case_id=? ORDER BY id", (self.case_id,)
            ).fetchall()
        return [{"state": state, "body": json.loads(body)} for state, body in rows]
