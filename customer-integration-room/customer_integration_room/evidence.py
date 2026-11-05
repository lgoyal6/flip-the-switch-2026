"""Approved evidence packets for the shared Switch case room."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any


class ExportBlocked(ValueError):
    """A proposed packet contains data that cannot enter the shared room."""


_SENSITIVE_KEYS = re.compile(r"(?:email|secret|token|api[_-]?key|password)", re.IGNORECASE)
_CANARY = re.compile(r"(?:sk_live_[A-Za-z0-9_]+|api[_-]?key[_-]?[A-Za-z0-9_]+)", re.IGNORECASE)


@dataclass(frozen=True)
class EvidencePacket:
    packet_id: str
    case_id: str
    organization: str
    facts: tuple[dict[str, Any], ...]
    redactions: tuple[str, ...]
    sha256: str

    @classmethod
    def approve(
        cls,
        packet_id: str,
        case_id: str,
        organization: str,
        facts: list[dict[str, Any]],
        approver: str,
    ) -> EvidencePacket:
        if not approver:
            raise ExportBlocked("A human approver is required.")
        violations = _violations(facts)
        if violations:
            raise ExportBlocked("Sensitive fields remain: " + ", ".join(violations))
        canonical = json.dumps(facts, sort_keys=True, separators=(",", ":"))
        return cls(
            packet_id=packet_id,
            case_id=case_id,
            organization=organization,
            facts=tuple(facts),
            redactions=(),
            sha256=hashlib.sha256(canonical.encode()).hexdigest(),
        )


def inspect_export(facts: list[dict[str, Any]]) -> list[str]:
    """Return deterministic violations before any packet can be approved."""
    return _violations(facts)


def redact_facts(facts: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], tuple[str, ...]]:
    redactions: list[str] = []

    def clean(value: Any, path: str) -> Any:
        if isinstance(value, dict):
            result = {}
            for key, item in value.items():
                child_path = f"{path}.{key}" if path else key
                if _SENSITIVE_KEYS.search(key) or _CANARY.search(str(item)):
                    redactions.append(child_path)
                else:
                    result[key] = clean(item, child_path)
            return result
        if isinstance(value, list):
            return [clean(item, f"{path}[{index}]") for index, item in enumerate(value)]
        if isinstance(value, str) and _CANARY.search(value):
            redactions.append(path)
            return "[REDACTED]"
        return value

    return [clean(fact, "") for fact in facts], tuple(redactions)


def _violations(value: Any, path: str = "") -> list[str]:
    violations: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            child = f"{path}.{key}" if path else key
            if _SENSITIVE_KEYS.search(key) or _CANARY.search(str(item)):
                violations.append(child)
            else:
                violations.extend(_violations(item, child))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            violations.extend(_violations(item, f"{path}[{index}]"))
    elif isinstance(value, str) and _CANARY.search(value):
        violations.append(path)
    return violations
