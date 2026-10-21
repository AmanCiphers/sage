"""The fixed employee roster (plan N9 + naming decisions).

Exactly five employees, always known to SAGE. Names/ids are **lowercase**
in core logic (`asta`), the display name is cased for UI/CLI ("Asta").
The stable identity key is the id: `employee-asta`.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EmployeeSpec:
    """Static description of one roster member."""

    key: str  # lowercase core id, e.g. "asta"
    name: str  # display name, e.g. "Asta"
    role_card: str = ""  # role-card filename under roles/ (added later)

    @property
    def id(self) -> str:
        return f"employee-{self.key}"


ROSTER: tuple[EmployeeSpec, ...] = (
    EmployeeSpec(key="asta", name="Asta", role_card="asta.md"),
    EmployeeSpec(key="otto", name="Otto", role_card="otto.md"),
    EmployeeSpec(key="tony", name="Tony", role_card="tony.md"),
    EmployeeSpec(key="alpha", name="Alpha", role_card="alpha.md"),
    EmployeeSpec(key="charlie", name="Charlie", role_card="charlie.md"),
)

_BY_KEY = {spec.key: spec for spec in ROSTER}
_BY_ID = {spec.id: spec for spec in ROSTER}


def normalize(worker: str) -> str:
    """Accept a key, display name, or id; return the lowercase key.

    Case-insensitive so the LLM/user can't trip on casing.
    Raises KeyError for unknown employees.
    """

    raw = (worker or "").strip()
    if not raw:
        raise KeyError("empty employee name")

    if raw in _BY_KEY:
        return raw

    lowered = raw.lower()
    if lowered in _BY_KEY:
        return lowered

    if lowered.startswith("employee-") and lowered[len("employee-") :] in _BY_KEY:
        return lowered[len("employee-") :]

    raise KeyError(f"unknown employee: {worker}")


def get(worker: str) -> EmployeeSpec:
    return _BY_KEY[normalize(worker)]


def get_by_id(employee_id: str) -> EmployeeSpec:
    return _BY_ID[employee_id]


def display_name(worker: str) -> str:
    try:
        return get(worker).name
    except KeyError:
        return worker
