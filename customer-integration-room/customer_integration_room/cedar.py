"""Synthetic Cedar Outfitters receiver and its local fulfillment store."""

from __future__ import annotations

import hashlib
import hmac
import http.server
import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlparse


class CedarStore:
    """A tiny transactional receiver with a deliberately vulnerable and fixed mode."""

    def __init__(self, path: Path, *, fixed: bool = False):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fixed = fixed
        self._initialize()

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA foreign_keys = ON")
            yield db
        finally:
            db.close()

    def _initialize(self) -> None:
        with self.connection() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS orders (
                    order_id TEXT PRIMARY KEY,
                    customer_email TEXT NOT NULL,
                    amount_cents INTEGER NOT NULL,
                    address TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS fulfillments (
                    fulfillment_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    order_id TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    FOREIGN KEY (order_id) REFERENCES orders(order_id)
                );
                CREATE TABLE IF NOT EXISTS processed_events (
                    event_id TEXT PRIMARY KEY,
                    event_type TEXT NOT NULL,
                    order_id TEXT NOT NULL,
                    processed_at REAL NOT NULL
                );
                CREATE TABLE IF NOT EXISTS audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    created_at REAL NOT NULL
                );
                """
            )

    @property
    def fixed(self) -> bool:
        return self._fixed

    def seed_order(
        self,
        order_id: str = "ORD-1042",
        customer_email: str = "maya.chen@example.test",
        amount_cents: int = 12900,
        address: str = "17 Cedar Lane, Portland",
    ) -> None:
        with self.connection() as db:
            db.execute(
                "INSERT OR REPLACE INTO orders(order_id, customer_email, amount_cents, address) "
                "VALUES (?, ?, ?, ?)",
                (order_id, customer_email, amount_cents, address),
            )

    def reset_runtime(self) -> None:
        """Reset the fixture while retaining the seeded customer order."""
        with self.connection() as db:
            db.execute("DELETE FROM fulfillments")
            db.execute("DELETE FROM processed_events")
            db.execute("DELETE FROM audit")
            db.execute(
                "UPDATE orders SET address=? WHERE order_id=?",
                ("17 Cedar Lane, Portland", "ORD-1042"),
            )

    def apply_idempotency_patch(self) -> dict[str, Any]:
        self._fixed = True
        return {
            "patch_id": "patch_001",
            "description": "Atomically record event IDs before applying business effects.",
            "files": ["cedar_receiver/webhooks.py", "cedar_receiver/schema.sql"],
            "checks": [
                "unique processed event IDs",
                "transactional fulfillment write",
                "distinct event types remain applicable",
            ],
        }

    def _apply_event(self, db: sqlite3.Connection, event: dict[str, Any]) -> str:
        event_id = event["event_id"]
        event_type = event["event_type"]
        order_id = event["order_id"]
        if event_type == "order.paid":
            db.execute(
                "INSERT INTO fulfillments(order_id, event_id, created_at) VALUES (?, ?, ?)",
                (order_id, event_id, time.time()),
            )
            return "fulfillment_created"
        if event_type == "address.updated":
            db.execute(
                "UPDATE orders SET address=? WHERE order_id=?",
                (event["address"], order_id),
            )
            return "address_updated"
        raise ValueError(f"Unsupported event type {event_type}")

    def process_event(self, event: dict[str, Any], *, response_delay: float = 0) -> dict[str, Any]:
        event_id = event["event_id"]
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                if self._fixed:
                    inserted = db.execute(
                        "INSERT OR IGNORE INTO processed_events "
                        "(event_id, event_type, order_id, processed_at) VALUES (?, ?, ?, ?)",
                        (event_id, event["event_type"], event["order_id"], time.time()),
                    ).rowcount
                    if inserted == 0:
                        outcome = "duplicate_ignored"
                    else:
                        outcome = self._apply_event(db, event)
                else:
                    outcome = self._apply_event(db, event)
                db.execute(
                    "INSERT INTO audit(event_id, outcome, created_at) VALUES (?, ?, ?)",
                    (event_id, outcome, time.time()),
                )
                db.commit()
            except Exception:
                db.rollback()
                raise
        if response_delay:
            time.sleep(response_delay)
        return {"event_id": event_id, "outcome": outcome}

    def fulfillment_count(self, order_id: str = "ORD-1042") -> int:
        with self.connection() as db:
            return int(
                db.execute(
                    "SELECT COUNT(*) FROM fulfillments WHERE order_id=?", (order_id,)
                ).fetchone()[0]
            )

    def order(self, order_id: str = "ORD-1042") -> dict[str, Any]:
        with self.connection() as db:
            row = db.execute("SELECT * FROM orders WHERE order_id=?", (order_id,)).fetchone()
        if row is None:
            raise KeyError(order_id)
        return dict(row)

    def audit_rows(self) -> list[dict[str, Any]]:
        with self.connection() as db:
            rows = db.execute("SELECT * FROM audit ORDER BY id").fetchall()
        return [dict(row) for row in rows]


class CedarWebhookServer:
    """Threaded HTTP server used by the demo and verification tests."""

    def __init__(
        self,
        store: CedarStore,
        secret: str = "orbit-demo-secret",
        delayed_events: dict[str, float] | None = None,
    ):
        self.store = store
        self.secret = secret.encode()
        self.delayed_events = dict(delayed_events or {})
        self._lock = threading.Lock()
        self._server: http.server.ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> str:
        store = self.store
        secret = self.secret
        owner = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: Any) -> None:
                return

            def do_POST(self) -> None:
                if urlparse(self.path).path != "/webhooks/orbit":
                    self.send_error(404)
                    return
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length)
                provided = self.headers.get("X-Orbit-Signature", "")
                expected = hmac.new(secret, body, hashlib.sha256).hexdigest()
                if not hmac.compare_digest(provided, expected):
                    self._json(401, {"error": "invalid_signature"})
                    return
                event = json.loads(body)
                with owner._lock:
                    delay = owner.delayed_events.pop(event["event_id"], 0)
                try:
                    result = store.process_event(event, response_delay=delay)
                except (KeyError, ValueError) as exc:
                    self._json(422, {"error": str(exc)})
                    return
                self._json(200, result)

            def _json(self, status: int, payload: dict[str, Any]) -> None:
                encoded = json.dumps(payload, sort_keys=True).encode()
                try:
                    self.send_response(status)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(encoded)))
                    self.end_headers()
                    self.wfile.write(encoded)
                except (BrokenPipeError, ConnectionResetError):
                    return

        self._server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return f"http://127.0.0.1:{self._server.server_port}/webhooks/orbit"

    def close(self) -> None:
        if self._server is None:
            return
        self._server.shutdown()
        self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=2)
