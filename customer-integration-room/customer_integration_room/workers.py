"""Synthetic private workers used by the interactive proof of concept."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SyntheticWorker:
    organization: str
    role: str

    def facts(self) -> list[dict[str, str]]:
        if self.organization == "orbit-payments":
            return [
                {"evidence_id": "orbit_delivery_01", "claim": "evt_901 attempt 1 timed out"},
                {"evidence_id": "orbit_delivery_02", "claim": "evt_901 attempt 2 returned 200"},
            ]
        return [
            {"evidence_id": "cedar_db_01", "claim": "evt_901 created two fulfillment rows"},
            {"evidence_id": "cedar_log_01", "claim": "first request committed before timeout"},
            {"customer_email": "maya.chen@example.test", "api_key": "sk_live_demo_canary_123"},
        ]
