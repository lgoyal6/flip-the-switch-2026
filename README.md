# Contract Room

A backend engineer changes an orders feed from dollars to cents. Analytics breaks,
and the new column name alone does not establish the intended meaning. Contract
Room brings the engineer, an AI analyst, and an executable compatibility check
into a Switch room, then writes a repaired dataset only after human clarification
and approval of the exact proposed conversion.

Built for Flip the Switch 2026, Best Business Workflow. This is a bounded migration
rehearsal using synthetic orders, not a production warehouse migration service.

## The workflow

1. In Mattermost, send `@contract-room inspect`.
2. The original query actually fails in DuckDB. A live, signed-in Codex CLI
   proposes a source-column mapping and asks the human about units.
3. Send `@contract-room units dollars` to see the wrong conversion fail parity.
4. Send `@contract-room units cents` to get the actual SQL and check results.
5. Approve the full proposal ID shown in the preview. A compatible CSV and JSON
   receipt are written locally. The inputs remain intact.
6. Repeat approval to see the existing result returned. Restart the agent and
   ask `@contract-room status` to recover its proposal.

The example has four orders totaling $233.44. This is a fixture value, not a
measured customer result or performance claim.

## How Switch is used

| Switch capability | Role in the working path | Source |
| --- | --- | --- |
| Mattermost bridge and shared room | Human input, clarification, preview, approval, and results occupy one shared conversation | `contract_room/workflow.py` |
| Agent WebSocket | Delivers addressed messages and server-attributed sender/event IDs | `contract_room/switch.py` |
| Room context operation | Reads stored conversation on connection and restart | `contract_room/switch.py` |
| Agent HTTP operations | Posts results back into the original message thread | `contract_room/switch.py` |

Without Switch, there is no supported route from team chat into this workflow.
The local journal stores proposals, receipts, replay cursors, and command results;
Switch stores the shared conversation. Recovery uses both. Room instructions are
guidance, not an authorization mechanism.

## Run locally

Requirements: Python 3.12 or later, uv, Docker with Compose, and a Codex CLI
already signed in. The live planner uses that existing subscription. No paid
deployment, company Slack workspace, or separate model API key is required by
this implementation. The Docker stack downloads substantial images on first run.

```sh
uv sync --dev --python 3.12
python3 scripts/start_switch.py --directory ~/.codex/work/contract-room/local/switch
uv run python scripts/configure.py \
  --deployment-env ~/.codex/work/contract-room/local/switch/.env \
  --config ~/.codex/work/contract-room/local/connection.json
uv run contract-room create-room --config ~/.codex/work/contract-room/local/connection.json
```

Allow the stack's setup service to finish before configuring. It seeds the local
Mattermost user and links it to the Switch administrator. Helpers do not change
any hackathon registration or external workspace. They preserve existing secrets
and refuse to overwrite an existing agent configuration.

Before running the agent, bind its human owner with the exact opaque sender ID
from Switch's room context. This is a deliberate trust decision, never inferred
from a display name or an ID prefix. `scripts/bind_human.py` confirms the local
Mattermost account, posts a unique probe as that account, and finds that probe in
the Switch room before saving its attributed sender ID:

```sh
uv run python scripts/bind_human.py \
  --deployment-env ~/.codex/work/contract-room/local/switch/.env \
  --config ~/.codex/work/contract-room/local/connection.json
uv run contract-room run --config ~/.codex/work/contract-room/local/connection.json
```

Open Mattermost at http://localhost:18065. The local user name is `user`; its
generated password is `MATTERMOST_USER_PASSWORD` in the deployment's private
`.env`. The generated Switch admin password is a separate value. Do not publish
either file. Stop only this stack with:

```sh
docker compose --project-name contract-room \
  -f ~/.codex/work/contract-room/local/switch/compose.yaml \
  --env-file ~/.codex/work/contract-room/local/switch/.env down
```

This preserves volumes. The agent exits if its WebSocket is taken over or loses
its room. Restart it to resume its processed cursor. History gaps invalidate
pending proposals and require a fresh inspection.

## Verify

```sh
uv run pytest -q
uv run ruff check contract_room tests scripts
uv run ruff format --check contract_room tests scripts
```

For a real local end-to-end check, keep the agent running and use:

```sh
uv run python scripts/verify_live.py \
  --config ~/.codex/work/contract-room/local/connection.json \
  --deployment-env ~/.codex/work/contract-room/local/switch/.env \
  --report ~/.codex/work/contract-room/local/verification/live.json
```

That check posts messages as a **synthetic local human test actor**, makes an
actual Codex planner call, and checks the real output bytes. It does not enter
the hackathon. Unit tests replace the planner with an explicitly labelled test
double; they still execute the DuckDB checks and file/receipt path.

## What works and what does not

| Area | Scope |
| --- | --- |
| Working integration | Switch 0.31.0, local Mattermost, WebSocket events, live Codex mapping, DuckDB conversion |
| Human control | Explicit units, exact proposal approval, configured sender ID, input-byte binding |
| Verification | Nonempty data, no nulls, unique IDs, row count, exact per-order equality, decimal precision |
| Persistence | Local SQLite journal and output receipts; Switch conversation retained independently |
| Synthetic | Orders, team scenario, and the automated verification's human actor |
| Not implemented | Arbitrary schema migrations, uploads, live warehouses, deployment, undoing downstream writes, multi-user approval policies |
| Not claimed | Novel schema detection, production security isolation, universal migration correctness, exactly-once distributed execution |

The validator supports an amount-column mapping with cents or dollars as source
units and dollar output. Codex cannot supply executable SQL to it. Exact parity
requires before/after snapshots of the same orders, not unrelated batches or a
live stream. Passing these checks does not establish business correctness beyond
the human-confirmed assumptions and the tested snapshots.

## Two-minute demo plan

- 0:00-0:20: Show the old query failing after the feed changes.
- 0:20-0:45: Show the live agent's mapping and its request for human clarification.
- 0:45-1:05: Confirm the wrong units and show the refusal.
- 1:05-1:35: Confirm cents, inspect the SQL/checks, approve, and open the receipt.
- 1:35-2:00: Repeat approval and show restart recovery; state the synthetic/local boundary.

Two minutes is a presentation target, not an asserted event video limit. No entry
or video has been submitted by these helpers.

## References

- [Event brief and rules](https://www.wemakedevs.org/hackathons/switch)
- [Switch agent protocol](https://docs.switchagents.ai/switch-rooms/internals/agent-protocol)
- [Switch connection contract](https://docs.switchagents.ai/switch-rooms/internals/agent-connection)
- [Switch self-hosting](https://docs.switchagents.ai/switch-rooms/deploy/self-host)
- [Pinned upstream Compose file](https://github.com/sandbox-quantum/switch/blob/switch-v0.31.0/deploy/local/standalone-docker-compose.yml)

The stack helper downloads upstream deployment configuration; it does not vendor
or relicense Switch. Preserve upstream licensing for any redistributed components.
