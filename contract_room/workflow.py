"""The room's small, explicit inspect, clarify, preview, approve loop."""

import re

from . import planner
from .engine import Engine, Refused


class Workflow:
    def __init__(self, engine: Engine, approver: str, propose=planner.propose):
        self.engine, self.approver, self.propose = engine, approver, propose

    def handle(self, body: str, sender: str, event_id: str):
        if sender != self.approver:
            return (
                "Refused: this workflow accepts instructions only from its configured human owner."
            )
        previous = self.engine.get("reply:" + event_id)
        if previous:
            return previous
        command = re.sub(r"@contract-room\b", "", body).strip()
        try:
            reply = self.execute(command, sender, event_id)
        except Refused as exc:
            reply = "Refused: " + str(exc)
        self.engine.set("reply:" + event_id, reply)
        return reply

    def execute(self, command: str, sender: str, event_id: str):
        if command.lower() == "inspect":
            self.engine.invalidate_pending()
            self.engine.set("history_gap", False)
            schema = self.engine.inspect()
            answer = self.propose(schema)
            self.engine.set("active", {"schema": schema, "mapping": answer, "proposal_id": None})
            failure = schema["baseline_error"] or "The old query still runs."
            return (
                "**Migration rehearsal: synthetic orders data**\n\n"
                f"Old query result: `{failure}`\n\n"
                f"Before: `{', '.join(schema['before_columns'])}`\n"
                f"Current: `{', '.join(schema['current_columns'])}`\n\n"
                f"**Codex analysis:** {answer['explanation']}\n\n"
                "Confirm with `@contract-room units cents` or `@contract-room units dollars`. "
                "No compatible output has been written."
            )
        if command.lower() in {"units cents", "units dollars"}:
            if self.engine.get("history_gap"):
                raise Refused(
                    "Switch reported missing history. Run inspect again before confirming units."
                )
            active = self.engine.get("active")
            if not active:
                raise Refused(
                    "Run inspect first so the unit decision refers to an identified input."
                )
            if self.engine.inspect()["current_sha256"] != active["schema"]["current_sha256"]:
                raise Refused("Input changed since inspection. Run inspect again.")
            units = command.lower().split()[1]
            plan_id, plan = self.engine.preview(
                active["mapping"]["amount_column"], units, sender, event_id
            )
            active["proposal_id"] = plan_id
            self.engine.set("active", active)
            return (
                f"**Preview passed:** {plan['verification']['rows']} orders; "
                f"total ${plan['verification']['total']}.\n"
                f"Human-confirmed source units: **{units}**.\n\n"
                f"```sql\n{plan['sql']}\n```\n\n"
                "Checked nonempty data, nulls, unique IDs, row count, and exact per-order parity "
                "against the before-change snapshot.\n\n"
                f"Approve these exact inputs and SQL with `@contract-room approve {plan_id}`. "
                "The original files will remain intact."
            )
        if command.startswith("approve "):
            if self.engine.get("history_gap"):
                raise Refused("Switch reported missing history. Run inspect again before approval.")
            plan_id = command.split(maxsplit=1)[1].strip()
            receipt = self.engine.approve(plan_id, sender, event_id)
            prefix = (
                "Already applied; no additional output created."
                if receipt.get("already_applied")
                else "**Applied and verified.**"
            )
            return (
                f"{prefix}\n\n{receipt['verification']['rows']} compatible orders; "
                f"total ${receipt['verification']['total']}.\n"
                f"Proposal: `{plan_id}`\n"
                f"Output SHA-256: `{receipt['output_sha256']}`\n"
                f"Human approval event: `{receipt['approval_event']}`\n\n"
                "The CSV and full SQL/check receipt are saved on the agent's machine. "
                "Scope: synthetic migration snapshot, local execution."
            )
        if command.lower() == "status":
            active = self.engine.get("active")
            if not active:
                return "No inspected change yet. Start with `@contract-room inspect`."
            if active.get("proposal_id"):
                return (
                    "Recovered the pending or applied proposal from the local journal: `"
                    + active["proposal_id"]
                    + "`. The preview and human decision remain "
                    "in this Switch room. Repeating approval cannot create another output."
                )
            return "Mapping proposed. Awaiting human unit confirmation; no output written."
        return (
            "I rehearse one orders-data migration with you. Start with `@contract-room inspect`, "
            "confirm `units cents` or `units dollars`, then approve the proposal ID. "
            "Use `status` to recover the current decision after a restart."
        )
