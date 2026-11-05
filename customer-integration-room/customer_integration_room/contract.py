"""Reusable integration contract checks for future synthetic customers."""

from __future__ import annotations

import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from .cedar import CedarStore, CedarWebhookServer
from .orbit import OrbitEvent, OrbitSender

CONTRACT_ID = "integration-v1"


def run_preflight(*, fixed: bool, state_dir: Path | None = None) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(dir=state_dir) as temporary:
        root = Path(temporary)
        store = CedarStore(root / "cedar.sqlite3", fixed=fixed)
        store.seed_order()
        server = CedarWebhookServer(store)
        endpoint = server.start()
        try:
            event = OrbitEvent("evt_preflight_001", "order.paid", "ORD-1042")
            with ThreadPoolExecutor(max_workers=4) as pool:
                futures = [
                    pool.submit(OrbitSender().deliver, event, endpoint, timeout=1) for _ in range(4)
                ]
                [future.result() for future in futures]
            count = store.fulfillment_count()
        finally:
            server.close()
    return {
        "contract_id": CONTRACT_ID,
        "fixture": "cedar-next-customer",
        "fixed_receiver": fixed,
        "same_event_deliveries": 4,
        "fulfillment_count": count,
        "passed": count == 1,
        "failure": None if count == 1 else "receiver applies one event more than once",
    }


def run_contract_preflight(state_dir: Path | None = None) -> dict[str, Any]:
    vulnerable = run_preflight(fixed=False, state_dir=state_dir)
    repaired = run_preflight(fixed=True, state_dir=state_dir)
    return {"contract_id": CONTRACT_ID, "vulnerable": vulnerable, "repaired": repaired}
