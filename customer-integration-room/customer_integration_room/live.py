"""Interactive Switch coordinator for the synthetic integration case."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .case import CaseJournal, CaseState
from .demo import run_demo
from .evidence import EvidencePacket, inspect_export, redact_facts
from .switch_bridge import SwitchBridge
from .workers import SyntheticWorker


class LiveCoordinator:
    def __init__(self, bridge: SwitchBridge, state_path: Path, agent_name: str):
        self.bridge = bridge
        self.agent_name = agent_name
        self.case = CaseJournal(state_path, "case_ord_1042")
        self.orbit = SyntheticWorker("orbit-payments", "sender investigator")
        self.cedar = SyntheticWorker("cedar-outfitters", "receiver investigator")
        self.owner_sender: str | None = next(
            (
                event["body"].get("owner_sender")
                for event in reversed(self.case.events())
                if event["body"].get("owner_sender")
            ),
            None,
        )
        self.packets: dict[str, EvidencePacket] = {}

    async def handle_frame(self, frame: dict[str, Any]) -> None:
        payload = frame.get("data", {}).get("payload", {})
        body = str(payload.get("body", ""))
        command = re.sub(rf"@{re.escape(self.agent_name)}\b", "", body, flags=re.IGNORECASE).strip()
        if not command:
            return
        sender = str(payload.get("sender", ""))
        thread_id = payload.get("thread_id") or payload.get("message_id")
        if command.lower() in {"start", "open", "investigate"}:
            self.owner_sender = self.owner_sender or sender
            self.case.transition(
                CaseState.INVESTIGATING,
                {
                    "organizations": [self.orbit.organization, self.cedar.organization],
                    "owner_sender": self.owner_sender,
                },
            )
            await self.reply(
                "Both private workers are investigating. Reply `approve evidence` "
                "to review the packets before they enter the shared case.",
                thread_id,
            )
            return
        if command.lower() == "approve evidence":
            if not self._is_owner(sender):
                await self.reply("Evidence approval requires the case owner.", thread_id)
                return
            cedar_facts = self.cedar.facts()
            violations = inspect_export(cedar_facts)
            safe_cedar, redactions = redact_facts(cedar_facts)
            self.packets["orbit"] = EvidencePacket.approve(
                "packet_orbit_001",
                "case_ord_1042",
                self.orbit.organization,
                self.orbit.facts(),
                sender,
            )
            self.packets["cedar"] = EvidencePacket.approve(
                "packet_cedar_001",
                "case_ord_1042",
                self.cedar.organization,
                safe_cedar,
                sender,
            )
            for packet in self.packets.values():
                self.case.approve(
                    f"approval_{packet.packet_id}",
                    packet.packet_id,
                    packet.sha256,
                    sender,
                    {
                        "redactions": list(redactions)
                        if packet.packet_id.endswith("cedar_001")
                        else []
                    },
                )
            self.case.transition(
                CaseState.EVIDENCE_SHARED,
                {"packets": [packet.packet_id for packet in self.packets.values()]},
            )
            await self.reply(
                "Evidence shared. The Cedar packet removed "
                + ", ".join(violations)
                + ". Root cause is a committed request followed by a retry. "
                "Reply `decide event_id` to approve the business rule.",
                thread_id,
            )
            return
        if command.lower() == "decide order_id":
            await self.reply(
                "Refused unsafe rule. Deduplicating by order_id would suppress legitimate "
                "address updates. Use `decide event_id`.",
                thread_id,
            )
            return
        if command.lower() == "decide event_id":
            if not self._is_owner(sender):
                await self.reply("The event identity decision requires the case owner.", thread_id)
                return
            self.case.transition(
                CaseState.PATCH_PROPOSED,
                {"retry_identity": "event_id", "distinct_events_apply": True},
            )
            await self.reply(
                "Patch proposed. It records event_id atomically before the fulfillment write "
                "and keeps distinct event types applicable. Reply `approve patch`.",
                thread_id,
            )
            return
        if command.lower() == "approve patch":
            if not self._is_owner(sender):
                await self.reply("Patch approval requires the case owner.", thread_id)
                return
            patch = {
                "patch_id": "patch_001",
                "patch_sha256": "patch-sha-demo",
                "description": "Atomic event-id deduplication before business effects.",
            }
            self.case.approve(
                "approval_patch_001", patch["patch_id"], patch["patch_sha256"], sender, patch
            )
            self.case.transition(CaseState.PATCH_APPROVED, patch)
            await self.reply(
                "Patch approved. Reply `verify` to run ten concurrent retries and the address update.",
                thread_id,
            )
            return
        if command.lower() == "verify":
            result = run_demo()
            self.case.transition(
                CaseState.VERIFYING,
                {
                    "same_event_deliveries": 10,
                    "fulfillment_count": result["incident"]["after_fulfillment_count"],
                },
            )
            self.case.transition(
                CaseState.CUSTOMER_ACCEPTED,
                {"accepted_by": sender, "address": result["incident"]["address_after_update"]},
            )
            self.case.transition(
                CaseState.CONTRACT_GENERATED,
                {"contract_id": "integration-v1", "rules": ["deduplicate by event_id"]},
            )
            await self.reply(
                "Verified. Before repair the incident created 2 fulfillment rows. "
                "After repair, 10 concurrent deliveries created 1 row. "
                "evt_902 still updated the address. The saved contract catches the "
                "vulnerable next-customer fixture and passes on the repaired fixture.",
                thread_id,
            )
            return
        if command.lower() == "status":
            await self.reply(f"Case state: {self.case.current_state() or 'open'}.", thread_id)
            return
        await self.reply(
            "Commands are `start`, `approve evidence`, `decide order_id`, `decide event_id`, "
            "`approve patch`, `verify`, and `status`.",
            thread_id,
        )

    def _is_owner(self, sender: str) -> bool:
        return bool(sender and self.owner_sender and sender == self.owner_sender)

    async def reply(self, body: str, thread_id: str | None) -> None:
        await self.bridge.post_message(body, thread_id=thread_id)
