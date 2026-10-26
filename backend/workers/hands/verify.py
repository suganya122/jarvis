"""Before/after snapshots: how an action proves it did something."""

from contracts.hands import ElementInfo

Snapshot = dict[str, ElementInfo]
WATCHED_STATES = ("checked", "pressed", "selected", "expanded", "sensitive")
MAX_CHANGES = 8


def _label(info: ElementInfo) -> str:
    return f"{info.role} '{info.name or (info.text or '')[:40]}'"


def describe_changes(before: Snapshot, after: Snapshot) -> list[str]:
    """Human-readable differences, e.g. "label 'Count: 1' (was 'Count: 0')"."""
    changes: list[str] = []
    for element_id, new in after.items():
        old = before.get(element_id)
        if old is None:
            changes.append(f"appeared: {_label(new)}")
        elif old.name != new.name:
            changes.append(f"{new.role} now '{new.name}' (was '{old.name}')")
        elif old.text != new.text:
            changes.append(f"{_label(new)} text now '{(new.text or '')[:60]}'")
        else:
            for state in WATCHED_STATES:
                if (state in old.states) != (state in new.states):
                    changes.append(f"{_label(new)} {'now' if state in new.states else 'no longer'} {state}")
        if len(changes) >= MAX_CHANGES:
            return changes
    for element_id, old in before.items():
        if element_id not in after and len(changes) < MAX_CHANGES:
            changes.append(f"gone: {_label(old)}")
    return changes
