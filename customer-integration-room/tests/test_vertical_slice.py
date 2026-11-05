from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from customer_integration_room.case import CaseJournal, CaseState
from customer_integration_room.cedar import CedarStore, CedarWebhookServer
from customer_integration_room.contract import run_contract_preflight
from customer_integration_room.evidence import (
    EvidencePacket,
    ExportBlocked,
    inspect_export,
    redact_facts,
)
from customer_integration_room.live import LiveCoordinator
from customer_integration_room.orbit import OrbitEvent, OrbitSender


def test_timeout_retry_creates_duplicate_before_patch(tmp_path: Path):
    store = CedarStore(tmp_path / "cedar.sqlite3")
    store.seed_order()
    server = CedarWebhookServer(store, delayed_events={"evt_901": 0.2})
    endpoint = server.start()
    try:
        attempts = OrbitSender().deliver(
            OrbitEvent("evt_901", "order.paid", "ORD-1042"),
            endpoint,
            timeout=0.03,
            retry=True,
        )
    finally:
        server.close()
    assert [attempt["status"] for attempt in attempts] == ["timeout", "ok"]
    assert store.fulfillment_count() == 2


def test_fixed_receiver_is_atomic_under_concurrent_retries(tmp_path: Path):
    store = CedarStore(tmp_path / "cedar.sqlite3")
    store.seed_order()
    store.apply_idempotency_patch()
    server = CedarWebhookServer(store)
    endpoint = server.start()
    try:
        event = OrbitEvent("evt_901", "order.paid", "ORD-1042")
        with ThreadPoolExecutor(max_workers=10) as pool:
            futures = [
                pool.submit(OrbitSender().deliver, event, endpoint, timeout=1) for _ in range(10)
            ]
            [future.result() for future in futures]
        OrbitSender().deliver(
            OrbitEvent("evt_902", "address.updated", "ORD-1042", address="42 River Road"),
            endpoint,
            timeout=1,
        )
    finally:
        server.close()
    assert store.fulfillment_count() == 1
    assert store.order()["address"] == "42 River Road"


def test_export_blocks_sensitive_data_until_redacted(tmp_path: Path):
    facts = [
        {"claim": "two fulfillment rows", "customer_email": "maya.chen@example.test"},
        {"api_key": "sk_live_demo_canary_123"},
    ]
    assert inspect_export(facts) == ["[0].customer_email", "[1].api_key"]
    with pytest.raises(ExportBlocked):
        EvidencePacket.approve("packet_1", "case_1", "cedar", facts, "cedar-eng")
    safe, redactions = redact_facts(facts)
    packet = EvidencePacket.approve("packet_1", "case_1", "cedar", safe, "cedar-eng")
    assert packet.packet_id == "packet_1"
    assert set(redactions) == {"customer_email", "api_key"}


def test_approval_is_bound_and_replay_safe(tmp_path: Path):
    journal = CaseJournal(tmp_path / "case.sqlite3", "case_1")
    journal.transition(CaseState.OPEN, {"order_id": "ORD-1042"})
    receipt = journal.approve("approval_1", "patch_1", "sha1", "cedar-eng", {"ok": True})
    replay = journal.approve("approval_1", "patch_1", "sha2", "other", {"ok": False})
    assert receipt["subject_sha256"] == "sha1"
    assert replay["already_recorded"] is True
    assert replay["subject_sha256"] == "sha1"
    assert journal.current_state() == CaseState.OPEN


class FakeBridge:
    def __init__(self):
        self.messages = []

    async def post_message(self, body: str, *, thread_id: str | None = None):
        self.messages.append({"body": body, "thread_id": thread_id})
        return {"id": str(len(self.messages))}


def frame(body: str, sender: str = "human-1"):
    return {
        "data": {
            "payload": {
                "body": "@integration-coordinator " + body,
                "sender": sender,
                "message_id": "message-1",
            }
        }
    }


def test_live_coordinator_rejects_unsafe_rule_and_closes_case(tmp_path: Path):
    import asyncio

    bridge = FakeBridge()
    coordinator = LiveCoordinator(bridge, tmp_path / "case.sqlite3", "integration-coordinator")

    async def run_case():
        for command in [
            "start",
            "approve evidence",
            "decide order_id",
            "decide event_id",
            "approve patch",
            "verify",
        ]:
            await coordinator.handle_frame(frame(command))

    asyncio.run(run_case())
    assert coordinator.case.current_state() == CaseState.CONTRACT_GENERATED
    assert any("Refused unsafe rule" in message["body"] for message in bridge.messages)
    assert any(
        "10 concurrent deliveries created 1 row" in message["body"] for message in bridge.messages
    )


def test_live_coordinator_recovers_owner_after_restart(tmp_path: Path):
    import asyncio

    journal_path = tmp_path / "case.sqlite3"
    first_bridge = FakeBridge()
    first = LiveCoordinator(first_bridge, journal_path, "integration-coordinator")
    asyncio.run(first.handle_frame(frame("start", sender="human-1")))
    restarted_bridge = FakeBridge()
    restarted = LiveCoordinator(restarted_bridge, journal_path, "integration-coordinator")
    asyncio.run(restarted.handle_frame(frame("approve evidence", sender="human-1")))
    assert restarted.case.current_state() == CaseState.EVIDENCE_SHARED


def test_contract_preflight_catches_vulnerable_next_customer(tmp_path: Path):
    result = run_contract_preflight(tmp_path)
    assert result["vulnerable"]["passed"] is False
    assert result["vulnerable"]["fulfillment_count"] == 4
    assert result["repaired"]["passed"] is True
    assert result["repaired"]["fulfillment_count"] == 1
