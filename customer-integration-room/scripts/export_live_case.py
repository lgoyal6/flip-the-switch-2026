"""Export the current local case and shared Switch room into the demo console."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any

import httpx

from customer_integration_room.demo import run_demo


def journal_state(path: Path) -> tuple[str | None, list[dict[str, Any]]]:
    if not path.exists():
        return None, []
    with sqlite3.connect(path) as db:
        rows = db.execute("SELECT state, body FROM case_events ORDER BY id").fetchall()
    events = [{"state": state, "body": json.loads(body)} for state, body in rows]
    return events[-1]["state"] if events else None, events


def shared_messages(config_path: Path) -> list[dict[str, Any]]:
    config = json.loads(config_path.read_text())
    with httpx.Client(
        base_url=config["base_url"],
        headers={"Authorization": "Bearer " + config["api_key"]},
        timeout=30,
    ) as client:
        response = client.post(
            f"/agents/{config['agent_id']}/ops/read_context",
            json={"room_id": config["room_id"], "limit": 100},
        )
        response.raise_for_status()
        threads = response.json()["result"]["threads"]
    messages: list[dict[str, Any]] = []
    for thread in threads:
        for entry in [thread["root"], *thread["replies"]]:
            messages.append(
                {
                    "message_id": entry.get("message_id", ""),
                    "sender": entry.get("sender_name", entry.get("sender", "participant")),
                    "body": entry.get("body", ""),
                    "visibility": "shared",
                    "organization": None,
                    "evidence_ids": [],
                }
            )
    return messages


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    data = run_demo()
    state, events = journal_state(args.state)
    data["state"] = state or data["state"]
    data["timeline"] = events or data["timeline"]
    live_messages = True
    try:
        messages = shared_messages(args.config)
    except httpx.HTTPError:
        messages = []
        live_messages = False
    if messages:
        data["room"] = {"room_id": data["room"]["room_id"], "messages": messages}
    data["source"] = (
        "live Switch room and local case journal"
        if live_messages
        else "deterministic demo fallback"
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    print(f"Exported {len(data['room']['messages'])} room messages to {args.output}")


if __name__ == "__main__":
    main()
