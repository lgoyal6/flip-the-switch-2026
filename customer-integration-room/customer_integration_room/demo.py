"""Run the first complete Customer Integration Room proof of concept."""

from __future__ import annotations

import argparse
import json
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from .case import CaseJournal, CaseState
from .cedar import CedarStore, CedarWebhookServer
from .contract import run_contract_preflight
from .evidence import EvidencePacket, inspect_export, redact_facts
from .orbit import OrbitEvent, OrbitSender
from .room import CaseRoom


def run_demo(state_dir: Path | None = None) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(dir=state_dir) as temporary:
        root = Path(temporary)
        case = CaseJournal(root / "case.sqlite3", "case_ord_1042")
        room = CaseRoom("integration-ord-1042")
        room.post(
            "cedar-engineer", "One payment produced two fulfillment jobs. Opening case ORD-1042."
        )
        room.post(
            "orbit-worker",
            "Investigating sender delivery history for evt_901.",
            visibility="private",
            organization="orbit-payments",
        )
        room.post(
            "cedar-worker",
            "Investigating receiver logs and fulfillment state.",
            visibility="private",
            organization="cedar-outfitters",
        )
        case.transition(CaseState.OPEN, {"order_id": "ORD-1042", "customer": "Maya Chen"})
        case.transition(CaseState.INVESTIGATING, {"organizations": ["orbit", "cedar"]})

        vulnerable = CedarStore(root / "cedar.sqlite3")
        vulnerable.seed_order()
        vulnerable_server = CedarWebhookServer(
            vulnerable,
            delayed_events={"evt_901": 0.25},
        )
        endpoint = vulnerable_server.start()
        try:
            sender = OrbitSender()
            event = OrbitEvent("evt_901", "order.paid", "ORD-1042")
            attempts = sender.deliver(event, endpoint, timeout=0.05, retry=True)
            before_count = vulnerable.fulfillment_count()
        finally:
            vulnerable_server.close()

        room.post(
            "orbit-worker",
            "evt_901 attempt 1 timed out. Retry attempt 2 returned HTTP 200.",
            evidence_ids=("orbit_delivery_01", "orbit_delivery_02"),
        )
        room.post(
            "cedar-worker",
            "The first request committed before the timeout. The retry created a second fulfillment.",
            evidence_ids=("cedar_db_01", "cedar_log_01"),
        )

        orbit_facts = [
            {"evidence_id": "orbit_delivery_01", "claim": "evt_901 attempt 1 timed out"},
            {"evidence_id": "orbit_delivery_02", "claim": "evt_901 attempt 2 returned 200"},
        ]
        cedar_facts = [
            {"evidence_id": "cedar_db_01", "claim": "evt_901 created two fulfillment rows"},
            {"evidence_id": "cedar_log_01", "claim": "first request committed before timeout"},
            {"customer_email": "maya.chen@example.test", "api_key": "sk_live_demo_canary_123"},
        ]
        blocked = inspect_export(cedar_facts)
        room.post(
            "export-guard",
            "Blocked Cedar packet because it contained a customer email and a secret canary.",
            visibility="private",
            organization="cedar-outfitters",
        )
        safe_facts, redactions = redact_facts(cedar_facts)
        orbit_packet = EvidencePacket.approve(
            "packet_orbit_001", "case_ord_1042", "orbit-payments", orbit_facts, "orbit-eng"
        )
        cedar_packet = EvidencePacket.approve(
            "packet_cedar_001", "case_ord_1042", "cedar-outfitters", safe_facts, "cedar-eng"
        )
        case.transition(
            CaseState.EVIDENCE_SHARED,
            {"packets": [orbit_packet.packet_id, cedar_packet.packet_id]},
        )
        room.post(
            "integration-coordinator",
            "Approved evidence is now shared. Both sides show the same root cause.",
            evidence_ids=(orbit_packet.packet_id, cedar_packet.packet_id),
        )
        case.transition(
            CaseState.DECISION_REQUIRED,
            {"question": "Should distinct event types for one order both apply?"},
        )
        decision = {
            "retry_identity": "event_id",
            "distinct_events_apply": True,
            "approved_by": "cedar-eng",
        }
        room.post(
            "cedar-engineer",
            "Decision approved. Retries share event_id. Distinct event types for one order still apply.",
        )
        case.transition(CaseState.PATCH_PROPOSED, decision)
        patch = vulnerable.apply_idempotency_patch()
        case.approve("approval_patch_001", patch["patch_id"], "patch-sha-demo", "cedar-eng", patch)
        case.transition(CaseState.PATCH_APPROVED, patch)
        room.post(
            "cedar-worker", "Patch approved and applied. Starting concurrent replay verification."
        )
        vulnerable.reset_runtime()
        fixed_server = CedarWebhookServer(vulnerable)
        fixed_endpoint = fixed_server.start()
        try:
            fixed_sender = OrbitSender()
            with ThreadPoolExecutor(max_workers=10) as pool:
                futures = [
                    pool.submit(
                        fixed_sender.deliver,
                        event,
                        fixed_endpoint,
                        timeout=1,
                    )
                    for _ in range(10)
                ]
                [future.result() for future in futures]
            address_event = OrbitEvent(
                "evt_902", "address.updated", "ORD-1042", address="42 River Road, Portland"
            )
            fixed_sender.deliver(address_event, fixed_endpoint, timeout=1)
            after_count = vulnerable.fulfillment_count()
            order = vulnerable.order()
        finally:
            fixed_server.close()
        case.transition(
            CaseState.VERIFYING,
            {"same_event_deliveries": 10, "fulfillment_count": after_count},
        )
        room.post(
            "verification-runner",
            f"Verified 10 deliveries of evt_901 produce {after_count} fulfillment. evt_902 updates the address.",
        )
        case.transition(
            CaseState.CUSTOMER_ACCEPTED,
            {"accepted_by": "cedar-eng", "address": order["address"]},
        )
        room.post(
            "cedar-engineer",
            "Accepted. Integration contract integration-v1 is ready for the next customer.",
        )
        case.transition(
            CaseState.CONTRACT_GENERATED,
            {
                "contract_id": "integration-v1",
                "rules": ["deduplicate by event_id", "apply distinct event types"],
            },
        )
        return {
            "case_id": "case_ord_1042",
            "incident": {
                "order_id": "ORD-1042",
                "event_id": "evt_901",
                "attempts": attempts,
                "before_fulfillment_count": before_count,
                "after_fulfillment_count": after_count,
                "address_after_update": order["address"],
            },
            "evidence": {
                "blocked_export_fields": blocked,
                "redactions": list(redactions),
                "published_packets": [orbit_packet.packet_id, cedar_packet.packet_id],
            },
            "patch": patch,
            "state": case.current_state().value if case.current_state() else None,
            "timeline": case.events(),
            "room": {"room_id": room.room_id, "messages": room.messages()},
            "preflight": run_contract_preflight(),
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write-demo", type=Path, help="Write the synthetic run to a JSON file.")
    args = parser.parse_args()
    result = run_demo()
    encoded = json.dumps(result, indent=2, sort_keys=True)
    if args.write_demo:
        args.write_demo.parent.mkdir(parents=True, exist_ok=True)
        args.write_demo.write_text(encoded + "\n")
    else:
        print(encoded)


if __name__ == "__main__":
    main()
