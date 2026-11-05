"""Bind the known local Mattermost account to its Switch-attributed sender ID."""

import argparse
import json
import time
import uuid
from pathlib import Path

import httpx


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--deployment-env", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    if config.get("approver"):
        print("Human already bound; keeping the existing approval identity.")
        return
    env = dict(
        line.split("=", 1)
        for line in args.deployment_env.read_text().splitlines()
        if line and not line.startswith("#") and "=" in line
    )
    with (
        httpx.Client(
            base_url="http://127.0.0.1:" + env.get("MATTERMOST_HOST_PORT", "8065"), timeout=20
        ) as messaging,
        httpx.Client(
            base_url=config["base_url"],
            timeout=20,
            headers={"Authorization": "Bearer " + config["api_key"]},
        ) as switch,
    ):
        login = messaging.post(
            "/api/v4/users/login",
            json={"login_id": env["MATTERMOST_USER"], "password": env["MATTERMOST_USER_PASSWORD"]},
        )
        login.raise_for_status()
        if login.json()["username"] != config["messaging_username"]:
            raise RuntimeError("The logged-in messaging account does not match this configuration.")
        messaging.headers["Authorization"] = "Bearer " + login.headers["Token"]
        teams = messaging.get("/api/v4/users/me/teams").json()
        team = next(t for t in teams if t["name"] == env["MATTERMOST_TEAM_NAME"])
        channels = messaging.get(f"/api/v4/users/me/teams/{team['id']}/channels").json()
        channel = next(c for c in channels if c["display_name"] == "Contract Room")
        probe = "Contract Room local human binding probe: " + str(uuid.uuid4())
        messaging.post(
            "/api/v4/posts", json={"channel_id": channel["id"], "message": probe}
        ).raise_for_status()
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            response = switch.post(
                f"/agents/{config['agent_id']}/ops/read_context",
                json={"room_id": config["room_id"], "limit": 100},
            )
            response.raise_for_status()
            entries = [
                entry
                for thread in response.json()["result"]["threads"]
                for entry in [thread["root"], *thread["replies"]]
            ]
            matches = [entry for entry in entries if entry["body"] == probe]
            if matches:
                if len(matches) != 1 or matches[0]["sender_name"] != login.json()["username"]:
                    raise RuntimeError("Ambiguous identity probe; human binding refused.")
                config["approver"] = matches[0]["sender"]
                args.config.write_text(json.dumps(config, indent=2))
                args.config.chmod(0o600)
                print("Bound the known local human participant without parsing its opaque ID.")
                return
            time.sleep(0.25)
        raise TimeoutError("Switch did not receive the local identity probe.")


if __name__ == "__main__":
    main()
