"""Switch WebSocket transport. The reader answers heartbeats while the worker runs Codex."""

import asyncio
import json
import logging
import uuid
from urllib.parse import urlencode

import httpx
from websockets.asyncio.client import connect

from .engine import Engine
from .workflow import Workflow

log = logging.getLogger(__name__)


class Switch:
    def __init__(self, config: dict, engine: Engine):
        self.config, self.engine = config, engine
        self.connection_id = engine.get("connection_id") or str(uuid.uuid4())
        engine.set("connection_id", self.connection_id)
        self.http = httpx.AsyncClient(
            base_url=config["base_url"],
            timeout=30,
            headers={
                "Authorization": "Bearer " + config["api_key"],
                "X-Switch-Connection-Id": self.connection_id,
            },
        )

    async def op(self, name: str, **arguments):
        response = await self.http.post(
            f"/agents/{self.config['agent_id']}/ops/{name}", json=arguments
        )
        response.raise_for_status()
        return response.json()["result"]

    async def serve(self):
        workflow = Workflow(self.engine, self.config["approver"])
        cursor = self.engine.get("cursor", "head")
        generation = self.engine.get("generation")
        queue = asyncio.Queue(maxsize=64)
        params = {
            "connection_id": self.connection_id,
            "scope": "single",
            "filter": "all",
            "rooms": self.config["room_id"],
            "start_from": cursor,
            "client": "contract-room",
            "client_version": "0.1.0",
        }
        if generation is not None:
            params["expected_generation"] = generation
        url = (
            self.config["base_url"].replace("http", "ws", 1)
            + f"/agents/{self.config['agent_id']}/connection/ws?"
            + urlencode(params)
        )

        async def work():
            while True:
                event = await queue.get()
                payload = event["data"]["payload"]
                if event["event"] == "message" and payload.get("addressed"):
                    reply = await asyncio.to_thread(
                        workflow.handle, payload["body"], payload["sender"], payload["message_id"]
                    )
                    await self.op(
                        "post_message",
                        body=reply,
                        thread_id=payload.get("thread_id") or payload["message_id"],
                    )
                self.engine.set("cursor", event["id"])
                queue.task_done()

        try:
            async with connect(
                url, additional_headers={"Authorization": "Bearer " + self.config["api_key"]}
            ) as ws:
                # Verify the connection before starting anything that needs a claimed room.
                first = json.loads(await ws.recv())
                if first.get("event") != "connection_state":
                    raise RuntimeError("Switch refused the connection: " + json.dumps(first))
                state = first["data"]
                if self.config["room_id"] not in state["rooms"]:
                    raise RuntimeError("Switch did not claim the configured room.")
                self.engine.set("generation", state["generation"])
                if cursor == "head":
                    self.engine.set("cursor", state["cursor"])
                async with asyncio.TaskGroup() as tasks:

                    async def read():
                        async for raw in ws:
                            frame = json.loads(raw)
                            kind = frame.get("event")
                            if kind == "ping":
                                await ws.send(
                                    json.dumps(
                                        {
                                            "type": "pong",
                                            "cursor": self.engine.get("cursor", state["cursor"]),
                                        }
                                    )
                                )
                            elif kind == "gap":
                                # Never authorize from an incomplete replay. A new inspection is required.
                                self.engine.set("history_gap", True)
                                self.engine.invalidate_pending()
                                self.engine.set("cursor", frame["data"]["resumed_at"])
                                raise RuntimeError(
                                    "Switch history gap; inspect again after reconnecting."
                                )
                            elif kind in {"evicted", "refused", "room_released"}:
                                raise RuntimeError(
                                    "Switch closed the room connection: " + json.dumps(frame)
                                )
                            elif kind in {"message", "command", "room_join"}:
                                queue.put_nowait(frame)
                        raise RuntimeError(
                            "Switch disconnected. Restart to resume the stored cursor."
                        )

                    tasks.create_task(read())
                    tasks.create_task(work())
                    tasks.create_task(self.read_shared_context())
        finally:
            await self.http.aclose()

    async def read_shared_context(self):
        context = await self.op("read_context", limit=100)
        self.engine.set("room_context", context)
        log.info(
            "Connected to Switch; recovered %d room threads (truncated=%s).",
            len(context.get("threads", [])),
            context.get("truncated"),
        )
