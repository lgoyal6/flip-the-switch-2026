"""Small Switch transport adapter for publishing and receiving case-room messages."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx
from websockets.asyncio.client import connect


@dataclass(frozen=True)
class SwitchConfig:
    base_url: str
    agent_id: str
    api_key: str
    room_id: str
    connection_id: str = ""

    def with_connection_id(self) -> SwitchConfig:
        return SwitchConfig(
            base_url=self.base_url,
            agent_id=self.agent_id,
            api_key=self.api_key,
            room_id=self.room_id,
            connection_id=self.connection_id or str(uuid.uuid4()),
        )


class SwitchBridge:
    """Transport only. Case policy and approval logic stay in the local application."""

    def __init__(self, config: SwitchConfig):
        self.config = config.with_connection_id()

    async def post_message(self, body: str, *, thread_id: str | None = None) -> dict[str, Any]:
        headers = {
            "Authorization": "Bearer " + self.config.api_key,
            "X-Switch-Connection-Id": self.config.connection_id,
        }
        payload: dict[str, Any] = {"body": body}
        if thread_id:
            payload["thread_id"] = thread_id
        async with httpx.AsyncClient(base_url=self.config.base_url, headers=headers) as client:
            response = await client.post(
                f"/agents/{self.config.agent_id}/ops/post_message", json=payload
            )
            response.raise_for_status()
            return response.json()["result"]

    async def read_context(self, limit: int = 100) -> dict[str, Any]:
        headers = {
            "Authorization": "Bearer " + self.config.api_key,
            "X-Switch-Connection-Id": self.config.connection_id,
        }
        async with httpx.AsyncClient(base_url=self.config.base_url, headers=headers) as client:
            response = await client.post(
                f"/agents/{self.config.agent_id}/ops/read_context", json={"limit": limit}
            )
            response.raise_for_status()
            return response.json()["result"]

    async def serve(
        self,
        on_message: Callable[[dict[str, Any]], Awaitable[None]],
        *,
        start_from: str = "head",
    ) -> None:
        config = self.config
        params = urlencode(
            {
                "connection_id": config.connection_id,
                "scope": "single",
                "filter": "addressed",
                "rooms": config.room_id,
                "start_from": start_from,
                "client": "customer-integration-room",
                "client_version": "0.1.0",
            }
        )
        url = config.base_url.replace("http", "ws", 1)
        url += f"/agents/{config.agent_id}/connection/ws?{params}"
        async with connect(
            url,
            additional_headers={"Authorization": "Bearer " + config.api_key},
        ) as socket:
            state = json.loads(await socket.recv())
            if state.get("event") != "connection_state":
                raise RuntimeError("Switch did not return connection state")
            if config.room_id not in state["data"].get("rooms", []):
                raise RuntimeError("Switch did not claim the configured case room")
            cursor = state["data"].get("cursor", "head")
            async for raw in socket:
                frame = json.loads(raw)
                if frame.get("event") == "ping":
                    cursor = frame.get("id") or frame.get("data", {}).get("cursor") or cursor
                    await socket.send(json.dumps({"type": "pong", "cursor": cursor}))
                    continue
                if frame.get("event") == "message":
                    cursor = frame.get("id", cursor)
                    await on_message(frame)
                elif frame.get("event") in {"gap", "evicted", "refused", "room_released"}:
                    raise RuntimeError("Switch closed or invalidated the case connection")


async def publish_transcript(config: SwitchConfig, messages: list[dict[str, Any]]) -> None:
    bridge = SwitchBridge(config)
    listener = asyncio.create_task(bridge.serve(lambda _frame: _ignore_message()))
    try:
        shared = [message for message in messages if message["visibility"] == "shared"]
        for message in shared:
            for _attempt in range(50):
                try:
                    await bridge.post_message(message["body"])
                    break
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code != 409:
                        raise
                    await asyncio.sleep(0.1)
            else:
                raise RuntimeError("Switch did not activate the coordinator connection")
    finally:
        listener.cancel()
        with suppress(asyncio.CancelledError):
            await listener


async def _ignore_message() -> None:
    return None


def run(coro: Awaitable[Any]) -> Any:
    return asyncio.run(coro)
