import shutil
from pathlib import Path

import pytest

from contract_room.engine import Engine, Refused
from contract_room.workflow import Workflow


@pytest.fixture
def engine(tmp_path):
    source = Path(__file__).parents[1] / "examples"
    before, current = tmp_path / "before.csv", tmp_path / "current.csv"
    shutil.copy(source / "orders_before.csv", before)
    shutil.copy(source / "orders_current.csv", current)
    return Engine(before, current, tmp_path / "runtime")


def test_original_query_fails_and_real_compatibility_query_restores_every_order(engine):
    assert "amount" in engine.inspect()["baseline_error"]
    plan_id, preview = engine.preview("amount_cents", "cents", "human", "units-event")
    assert preview["verification"]["total"] == "233.44"
    assert not list(engine.state.glob("*.csv"))
    receipt = engine.approve(plan_id, "human", "approval-event")
    assert receipt["verification"]["rows"] == 4
    assert Path(receipt["artifact"]).read_text() == engine.before.read_text()


def test_wrong_unit_confirmation_does_not_generate_an_approvable_plan(engine):
    with pytest.raises(Refused, match="parity failed"):
        engine.preview("amount_cents", "dollars", "human", "units-event")
    assert not list(engine.state.glob("*.csv"))


@pytest.mark.parametrize("which", ["before", "current"])
def test_input_change_after_preview_invalidates_approval(engine, which):
    plan_id, _ = engine.preview("amount_cents", "cents", "human", "units-event")
    path = getattr(engine, which)
    path.write_text(path.read_text() + "order-005,999,USD\n")
    with pytest.raises(Refused, match="Input changed"):
        engine.approve(plan_id, "human", "approve")
    assert not list(engine.state.glob("*.csv"))


def test_agent_cannot_approve_a_human_plan(engine):
    plan_id, _ = engine.preview("amount_cents", "cents", "human", "units-event")
    with pytest.raises(Refused, match="Only the human"):
        engine.approve(plan_id, "agent", "spoofed-approval")


def test_approval_replay_and_process_restart_are_idempotent(engine):
    plan_id, _ = engine.preview("amount_cents", "cents", "human", "units-event")
    original = engine.approve(plan_id, "human", "first-approval")
    restarted = Engine(engine.before, engine.current, engine.state)
    again = restarted.approve(plan_id, "human", "second-approval")
    assert again["already_applied"]
    assert again["approval_event"] == "first-approval"
    assert again["output_sha256"] == original["output_sha256"]
    assert len(list(engine.state.glob("*.csv"))) == 1


@pytest.mark.parametrize("value", ["", "invalid", "-1"])
def test_null_invalid_and_changed_amounts_are_refused(engine, value):
    engine.current.write_text(engine.current.read_text().replace("1250", value))
    with pytest.raises(Refused):
        engine.preview("amount_cents", "cents", "human", "units-event")


def test_duplicate_ids_are_refused_even_if_both_snapshots_agree(engine):
    for path in [engine.before, engine.current]:
        path.write_text(path.read_text().replace("order-002", "order-001"))
    with pytest.raises(Refused, match="Duplicate"):
        engine.preview("amount_cents", "cents", "human", "units-event")


def test_large_decimal_amounts_do_not_pass_through_float(engine):
    engine.before.write_text("order_id,amount,currency\na,99999999999999.99,USD\n")
    engine.current.write_text("order_id,amount_cents,currency\na,9999999999999999,USD\n")
    _, preview = engine.preview("amount_cents", "cents", "human", "units-event")
    assert preview["verification"]["total"] == "99999999999999.99"


def test_fractional_cents_cannot_be_rounded_into_passing_the_checks(engine):
    engine.current.write_text(engine.current.read_text().replace("1250", "1250.1"))
    with pytest.raises(Refused, match="precision would be lost"):
        engine.preview("amount_cents", "cents", "human", "units-event")


def test_units_before_inspection_and_unauthorized_sender_are_refused(engine):
    workflow = Workflow(engine, "human", propose=lambda _: pytest.fail("AI should not be called"))
    assert "Run inspect first" in workflow.handle("units cents", "human", "1")
    assert "configured human" in workflow.handle("inspect", "agent", "2")


def test_conversation_inspect_confirm_restart_approve(engine):
    # Deterministic planner is a test double, not a live-AI demo.
    planner = lambda _: {"amount_column": "amount_cents", "explanation": "Please confirm units."}
    workflow = Workflow(engine, "human", propose=planner)
    assert "Codex analysis" in workflow.handle("@contract-room inspect", "human", "1")
    assert "Preview passed" in workflow.handle("units cents", "human", "2")
    active = engine.get("active")
    restarted = Workflow(
        Engine(engine.before, engine.current, engine.state), "human", propose=planner
    )
    assert active["proposal_id"] in restarted.handle("status", "human", "3")
    assert "Applied and verified" in restarted.handle(
        "approve " + active["proposal_id"], "human", "4"
    )
    assert "Already applied" in restarted.handle("approve " + active["proposal_id"], "human", "5")


def test_new_inspection_revokes_an_unapplied_preview(engine):
    planner = lambda _: {"amount_column": "amount_cents", "explanation": "Please confirm units."}
    workflow = Workflow(engine, "human", propose=planner)
    workflow.handle("inspect", "human", "1")
    workflow.handle("units cents", "human", "2")
    previous = engine.get("active")["proposal_id"]
    workflow.handle("inspect", "human", "3")
    assert "Unknown proposal" in workflow.handle("approve " + previous, "human", "4")
    assert not list(engine.state.glob("*.csv"))


def test_history_gap_requires_fresh_inspection_even_if_a_preview_survives(engine):
    planner = lambda _: {"amount_column": "amount_cents", "explanation": "Please confirm units."}
    workflow = Workflow(engine, "human", propose=planner)
    workflow.handle("inspect", "human", "1")
    workflow.handle("units cents", "human", "2")
    previous = engine.get("active")["proposal_id"]
    engine.set("history_gap", True)
    assert "missing history" in workflow.handle("approve " + previous, "human", "3")
    assert "missing history" in workflow.handle("units cents", "human", "4")
    workflow.handle("inspect", "human", "5")
    assert "Preview passed" in workflow.handle("units cents", "human", "6")
