"""Run the interactive synthetic coordinator against a Switch room."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from customer_integration_room.live import LiveCoordinator
from customer_integration_room.switch_bridge import SwitchBridge, SwitchConfig


async def run(config_path: Path, state_path: Path) -> None:
    config = json.loads(config_path.read_text())
    bridge = SwitchBridge(
        SwitchConfig(
            config["base_url"],
            config["agent_id"],
            config["api_key"],
            config["room_id"],
        )
    )
    coordinator = LiveCoordinator(bridge, state_path, "integration-coordinator")
    await bridge.serve(coordinator.handle_frame)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(run(args.config, args.state))


if __name__ == "__main__":
    main()
