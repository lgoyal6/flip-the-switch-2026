"""Register the POC agent and create its local private Switch room."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import httpx


def read_env(path: Path) -> dict[str, str]:
    return dict(
        line.split("=", 1)
        for line in path.read_text().splitlines()
        if line and not line.startswith("#") and "=" in line
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deployment-env", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("action", choices=["register", "create-room"])
    args = parser.parse_args()
    env = read_env(args.deployment_env)
    base_url = "http://127.0.0.1:" + env.get("API_HOST_PORT", "8000")

    if args.action == "register":
        if args.config.exists():
            raise SystemExit("Config already exists. Reuse it instead of rotating credentials.")
        with httpx.Client(base_url=base_url, timeout=30) as client:
            login = client.post(
                "/gateway/auth/login",
                json={
                    "email": env["GATEWAY_ADMIN_EMAIL"],
                    "password": env["GATEWAY_ADMIN_PASSWORD"],
                },
            )
            login.raise_for_status()
            registration = client.post(
                "/gateway/api-keys",
                json={"label": "Customer Integration Room registration", "type": "registration"},
            )
            registration.raise_for_status()
            agent = client.post(
                "/agents",
                headers={"Authorization": "Bearer " + registration.json()["key"]},
                json={
                    "name": "integration-coordinator",
                    "description": "Coordinates synthetic customer integration investigations.",
                    "connector_type": "customer-integration-room",
                    "integration_profile": {
                        "connection_model": "always_on",
                        "message_exchange": True,
                        "pre_invocation_mediation": [],
                        "post_invocation_mediation": [],
                        "event_reporting": [],
                    },
                },
            )
            agent.raise_for_status()
            args.config.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            config = {
                "base_url": base_url,
                "agent_id": agent.json()["id"],
                "api_key": agent.json()["api_key"],
                "messaging_username": env["MATTERMOST_USER"],
            }
            args.config.write_text(json.dumps(config, indent=2))
            args.config.chmod(0o600)
            client.delete("/gateway/api-keys/" + registration.json()["id"]).raise_for_status()
        print("Registered the integration coordinator and wrote its private config.")
        return

    config = json.loads(args.config.read_text())
    with httpx.Client(
        base_url=config["base_url"],
        timeout=30,
        headers={"Authorization": "Bearer " + config["api_key"]},
    ) as client:
        response = client.post(
            f"/agents/{config['agent_id']}/ops/create_room",
            json={
                "name": "Customer Integration Room",
                "description": "Synthetic cross-company integration incident case.",
                "agent_names": ["integration-coordinator"],
                "user_names": [config["messaging_username"]],
                "channel_type": "channel_private",
                "write_visibility": "private",
                "instructions": (
                    "Synthetic POC case. Show both sides of the integration incident, require "
                    "human approval for shared evidence, and publish only approved transcript messages."
                ),
            },
        )
        response.raise_for_status()
        result = response.json()["result"]
        if result.get("failed_attachments"):
            raise RuntimeError("Room created with incomplete attachments: " + json.dumps(result))
    config["room_id"] = result["id"]
    args.config.write_text(json.dumps(config, indent=2))
    args.config.chmod(0o600)
    print("Created Customer Integration Room with id", result["id"])


if __name__ == "__main__":
    main()
