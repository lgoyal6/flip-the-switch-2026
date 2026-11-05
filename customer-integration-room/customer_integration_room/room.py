"""A deterministic room transcript that can later be backed by Switch messages."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class RoomMessage:
    message_id: str
    sender: str
    body: str
    visibility: str = "shared"
    organization: str | None = None
    evidence_ids: tuple[str, ...] = ()


class CaseRoom:
    """Record the product's shared and private conversation in one replayable log."""

    def __init__(self, room_id: str):
        self.room_id = room_id
        self._messages: list[RoomMessage] = []

    def post(
        self,
        sender: str,
        body: str,
        *,
        visibility: str = "shared",
        organization: str | None = None,
        evidence_ids: tuple[str, ...] = (),
    ) -> RoomMessage:
        if visibility not in {"shared", "private"}:
            raise ValueError("visibility must be shared or private")
        message = RoomMessage(
            message_id=f"room-msg-{len(self._messages) + 1:03d}",
            sender=sender,
            body=body,
            visibility=visibility,
            organization=organization,
            evidence_ids=evidence_ids,
        )
        self._messages.append(message)
        return message

    def messages(self, *, visibility: str | None = None) -> list[dict[str, Any]]:
        selected = self._messages
        if visibility is not None:
            selected = [message for message in selected if message.visibility == visibility]
        return [asdict(message) for message in selected]
