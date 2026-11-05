# Customer Integration Room

Customer Integration Room is a proof of concept for resolving a cross-company
integration incident through Switch. Orbit Payments sends a payment event to Cedar
Outfitters. A timeout causes the same event to be delivered twice, and Cedar creates
two fulfillment jobs for one backpack order.

The demo shows two private investigations, approved evidence packets, a human decision
about event identity, an idempotency patch, concurrent replay verification, and a saved
integration contract for the next customer.

## Run the vertical slice

From this directory, run:

```sh
uv run pytest -q
uv run python -m customer_integration_room.demo
```

The demo uses synthetic data and local HTTP services. The core path is real. Orbit sends
signed HTTP requests, Cedar writes SQLite state, retries can time out after a committed
transaction, and the repaired receiver is exercised concurrently.

## Current proof

- Before the patch, a timeout followed by a retry creates two fulfillment rows.
- After the patch, ten concurrent deliveries of one event create one fulfillment row.
- A distinct address update for the same order still applies.
- A packet containing a customer email or secret canary is blocked before approval.
- Approval receipts survive replay and remain bound to their original subject hash.
- Contract `integration-v1` fails a vulnerable next-customer fixture and passes on the repaired fixture.

The existing Contract Room prototype remains a separate project in the parent directory.
To view the split-screen synthetic incident console, generate its run data and serve the
web directory:

```sh
uv run python -m customer_integration_room.demo --write-demo web/demo.json
uv run python -m http.server 8765 --directory web
```

Open http://127.0.0.1:8765. The current console uses a deterministic room transcript so
the visual story is reproducible. The next integration step is to route these transcript
messages through a live Switch room. `customer_integration_room.switch_bridge` provides
the Switch HTTP operations and WebSocket subscription for that connection. It expects
agent credentials supplied at runtime and contains no saved deployment secrets.

To publish the shared portion of a synthetic run into a configured room, provide the
runtime connection values through environment variables:

```sh
SWITCH_BASE_URL=http://localhost:18000 \
SWITCH_AGENT_ID=... \
SWITCH_API_KEY=... \
SWITCH_ROOM_ID=... \
uv run python scripts/publish_demo.py
```

To run the interactive coordinator, keep it connected to the room and send the listed
commands from the Mattermost user in that room:

```sh
uv run python scripts/run_coordinator.py \
  --config ~/.codex/work/customer-integration-room/local/connection.json \
  --state ~/.codex/work/customer-integration-room/local/live-case
```

The interactive sequence is `start`, `approve evidence`, `decide event_id`, `approve patch`,
and `verify`. Sending `decide order_id` demonstrates the unsafe rule being rejected.

For the isolated local deployment, register the coordinator and create its room with
the setup helper. The deployment `.env` and coordinator config stay outside the project.

```sh
uv run python scripts/setup_switch.py register \
  --deployment-env ~/.codex/work/customer-integration-room/local/switch/.env \
  --config ~/.codex/work/customer-integration-room/local/connection.json
uv run python scripts/setup_switch.py create-room \
  --deployment-env ~/.codex/work/customer-integration-room/local/switch/.env \
  --config ~/.codex/work/customer-integration-room/local/connection.json
```
