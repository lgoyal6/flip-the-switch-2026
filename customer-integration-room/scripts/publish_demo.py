"""Publish the synthetic shared transcript to a configured Switch room."""

from __future__ import annotations

import argparse
import os

from customer_integration_room.demo import run_demo
from customer_integration_room.switch_bridge import SwitchConfig, publish_transcript, run


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=os.environ.get("SWITCH_BASE_URL"))
    parser.add_argument("--agent-id", default=os.environ.get("SWITCH_AGENT_ID"))
    parser.add_argument("--api-key", default=os.environ.get("SWITCH_API_KEY"))
    parser.add_argument("--room-id", default=os.environ.get("SWITCH_ROOM_ID"))
    args = parser.parse_args()
    missing = [
        name
        for name, value in {
            "base-url": args.base_url,
            "agent-id": args.agent_id,
            "api-key": args.api_key,
            "room-id": args.room_id,
        }.items()
        if not value
    ]
    if missing:
        parser.error("missing Switch configuration: " + ", ".join(missing))
    result = run_demo()
    run(
        publish_transcript(
            SwitchConfig(args.base_url, args.agent_id, args.api_key, args.room_id),
            result["room"]["messages"],
        )
    )
    print(f"Published {len(result['room']['messages'])} transcript messages to {args.room_id}.")


if __name__ == "__main__":
    main()
