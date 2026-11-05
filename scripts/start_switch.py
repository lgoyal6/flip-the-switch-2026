"""Start an isolated, localhost-only Switch deployment from its pinned public release."""

import argparse
import secrets
import subprocess
import urllib.request
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    root = args.directory.expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    compose = root / "compose.yaml"
    if not compose.exists():
        url = (
            "https://raw.githubusercontent.com/sandbox-quantum/switch/"
            "switch-v0.31.0/deploy/local/standalone-docker-compose.yml"
        )
        with urllib.request.urlopen(url, timeout=30) as response:
            compose.write_bytes(response.read())
    env = root / ".env"
    if not env.exists():
        values = {
            "SWITCH_VERSION": "0.31.0",
            "DB_OWNER_USER": "switch_owner",
            "DB_USER": "switch_app",
            "DB_NAME": "switch",
            "COMPOSE_PROFILES": "collab,gateway",
            "POSTGRES_HOST_PORT": "15432",
            "API_HOST_PORT": "18000",
            "MATTERMOST_HOST_PORT": "18065",
            "GATEWAY_HOST_PORT": "13000",
            "GATEWAY_ADMIN_EMAIL": "admin@switch.local",
            "MATTERMOST_ADMIN_USER": "admin",
            "MATTERMOST_TEAM_NAME": "contract-room",
            "MATTERMOST_USER": "user",
            "FRONTEND_BASE_URL": "http://localhost:13000",
            "GATEWAY_PUBLIC_URL": "http://localhost:18000",
            "SECRET_KEYS": "local:" + secrets.token_hex(32),
            "THIRD_PARTY_AVATARS_ENABLED": "false",
            "TELEMETRY_ENABLED": "false",
        }
        for key in [
            "DB_OWNER_PASSWORD",
            "DB_PASSWORD",
            "AGENT_REGISTRATION_TOKEN",
            "GATEWAY_ADMIN_PASSWORD",
            "MATTERMOST_ADMIN_PASSWORD",
            "MATTERMOST_USER_PASSWORD",
        ]:
            values[key] = secrets.token_hex(24)
        env.write_text("".join(f"{key}={value}\n" for key, value in values.items()))
        env.chmod(0o600)
    subprocess.run(
        [
            "docker",
            "compose",
            "--project-name",
            "contract-room",
            "-f",
            str(compose),
            "--env-file",
            str(env),
            "--profile",
            "collab",
            "--profile",
            "gateway",
            "up",
            "-d",
        ],
        cwd=root,
        check=True,
    )
    print("Switch: http://localhost:18000; Mattermost: http://localhost:18065")
    print("Generated local-only credentials are in", env)


if __name__ == "__main__":
    main()
