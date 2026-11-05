"""Opt-in local integration check using an actual Switch, Mattermost, and Codex run.

The seeded local Mattermost user is a synthetic test actor. This does not submit
an event entry or message a public workspace.
"""

import argparse
import json
import re
import time
from pathlib import Path

import httpx


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--deployment-env", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    env = dict(
        line.split("=", 1)
        for line in args.deployment_env.read_text().splitlines()
        if line and not line.startswith("#") and "=" in line
    )
    report = {
        "actor": "synthetic local Mattermost user",
        "planner": "live signed-in Codex CLI",
        "steps": [],
    }
    base = "http://127.0.0.1:" + env.get("MATTERMOST_HOST_PORT", "8065")
    with httpx.Client(base_url=base, timeout=20) as client:
        login = client.post(
            "/api/v4/users/login",
            json={"login_id": env["MATTERMOST_USER"], "password": env["MATTERMOST_USER_PASSWORD"]},
        )
        login.raise_for_status()
        client.headers["Authorization"] = "Bearer " + login.headers["Token"]
        user_id = login.json()["id"]
        teams = client.get("/api/v4/users/me/teams").json()
        team = next(t for t in teams if t["name"] == env["MATTERMOST_TEAM_NAME"])
        channels = client.get(f"/api/v4/users/me/teams/{team['id']}/channels").json()
        channel = next(c for c in channels if c["display_name"] == "Contract Room")
        report["room_url"] = f"{base}/{team['name']}/channels/{channel['name']}"

        def exchange(command, expected):
            print("Checking:", command.split()[0], flush=True)
            posted = client.post(
                "/api/v4/posts",
                json={"channel_id": channel["id"], "message": "@contract-room " + command},
            )
            posted.raise_for_status()
            post_id = posted.json()["id"]
            deadline = time.monotonic() + 150
            while time.monotonic() < deadline:
                response = client.get(f"/api/v4/posts/{post_id}/thread")
                response.raise_for_status()
                replies = [
                    p
                    for p in response.json()["posts"].values()
                    if p["id"] != post_id and p["user_id"] != user_id
                ]
                if replies:
                    body = max(replies, key=lambda p: p["create_at"])["message"]
                    report["steps"].append({"command": command, "post_id": post_id, "reply": body})
                    args.report.parent.mkdir(parents=True, exist_ok=True)
                    args.report.write_text(json.dumps(report, indent=2))
                    if expected not in body:
                        raise AssertionError(f"Expected {expected!r}; received {body!r}")
                    print("Passed:", expected, flush=True)
                    return body
                time.sleep(0.5)
            raise TimeoutError("Agent did not reply within 150 seconds.")

        exchange("inspect", "Codex analysis")
        exchange("units dollars", "Snapshot parity failed")
        preview = exchange("units cents", "Preview passed")
        plan_id = re.search(r"approve ([a-f0-9]{64})", preview).group(1)
        exchange("approve " + plan_id, "Applied and verified")
        exchange("approve " + plan_id, "Already applied")
        exchange("status", plan_id)
        receipt = json.loads((Path(config["state"]) / f"{plan_id}.json").read_text())
        assert Path(receipt["artifact"]).read_bytes() == Path(config["before"]).read_bytes()
        report.update(passed=True, receipt=receipt)
        args.report.write_text(json.dumps(report, indent=2))
        args.report.chmod(0o600)
        print("Live workflow verified:", report["room_url"], flush=True)


if __name__ == "__main__":
    main()
