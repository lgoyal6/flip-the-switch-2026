"""Synthetic Orbit Payments event ledger and HTTP delivery client."""

from __future__ import annotations

import hashlib
import hmac
import json
import socket
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class OrbitEvent:
    event_id: str
    event_type: str
    order_id: str
    amount_cents: int = 12900
    address: str | None = None

    def payload(self) -> dict[str, Any]:
        return {key: value for key, value in asdict(self).items() if value is not None}


class OrbitSender:
    def __init__(self, secret: str = "orbit-demo-secret"):
        self.secret = secret.encode()
        self.attempts: list[dict[str, Any]] = []

    def deliver(
        self,
        event: OrbitEvent,
        endpoint: str,
        *,
        timeout: float = 0.2,
        retry: bool = False,
    ) -> list[dict[str, Any]]:
        self._attempt(event, endpoint, timeout)
        if retry and self.attempts[-1]["status"] == "timeout":
            self._attempt(event, endpoint, timeout)
        return [attempt for attempt in self.attempts if attempt["event_id"] == event.event_id]

    def _attempt(self, event: OrbitEvent, endpoint: str, timeout: float) -> None:
        payload = json.dumps(event.payload(), sort_keys=True).encode()
        signature = hmac.new(self.secret, payload, hashlib.sha256).hexdigest()
        request = urllib.request.Request(
            endpoint,
            data=payload,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "X-Orbit-Signature": signature,
            },
        )
        attempt_number = sum(1 for item in self.attempts if item["event_id"] == event.event_id) + 1
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = json.loads(response.read().decode())
            self.attempts.append(
                {
                    "event_id": event.event_id,
                    "attempt": attempt_number,
                    "status": "ok",
                    "http_status": response.status,
                    "response": body,
                }
            )
        except (TimeoutError, urllib.error.URLError) as exc:
            reason = getattr(exc, "reason", exc)
            if (
                isinstance(reason, (TimeoutError, socket.timeout))
                or "timed out" in str(exc).lower()
            ):
                status = "timeout"
            else:
                status = "error"
            self.attempts.append(
                {
                    "event_id": event.event_id,
                    "attempt": attempt_number,
                    "status": status,
                    "error": str(exc),
                }
            )
