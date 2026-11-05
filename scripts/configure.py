"""Configure the agent against the helper's local deployment, without printing credentials."""

import argparse
import json
from pathlib import Path

import httpx


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--deployment-env", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    if args.config.exists():
        raise SystemExit(
            "Config already exists. Reuse it; this helper never rotates live credentials."
        )
    env = dict(
        line.split("=", 1)
        for line in args.deployment_env.read_text().splitlines()
        if line and not line.startswith("#") and "=" in line
    )
    base = "http://127.0.0.1:" + env.get("API_HOST_PORT", "8000")
    with httpx.Client(base_url=base, timeout=30) as client:
        client.post(
            "/gateway/auth/login",
            json={"email": env["GATEWAY_ADMIN_EMAIL"], "password": env["GATEWAY_ADMIN_PASSWORD"]},
        ).raise_for_status()
        registration = client.post(
            "/gateway/api-keys",
            json={"label": "Contract Room registration", "type": "registration"},
        )
        registration.raise_for_status()
        agent = client.post(
            "/agents",
            headers={"Authorization": "Bearer " + registration.json()["key"]},
            json={
                "name": "contract-room",
                "description": "Human-confirmed data repair",
                "connector_type": "contract-room",
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
        source = Path(__file__).resolve().parents[1]
        args.config.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        config = {
            "base_url": base,
            "agent_id": agent.json()["id"],
            "api_key": agent.json()["api_key"],
            "messaging_username": env["MATTERMOST_USER"],
            "before": str(source / "examples/orders_before.csv"),
            "current": str(source / "examples/orders_current.csv"),
            "state": str(args.config.parent / "runtime"),
        }
        args.config.write_text(json.dumps(config, indent=2))
        args.config.chmod(0o600)
        # The one-time registration credential is no longer needed.
        client.delete("/gateway/api-keys/" + registration.json()["id"]).raise_for_status()
    print("Private agent config created:", args.config)
    print("Next: contract-room create-room --config", args.config)


if __name__ == "__main__":
    main()
