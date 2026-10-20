"""Employee subsystem: named agents that work under SAGE.

Replaces the old process-worker system (app/execution/worker.py +
ProcessExecutor). See docs/WORKER_EMPLOYEE_PLAN.md.
"""

from app.employees.employee import Employee, EmployeeResult
from app.employees.roster import ROSTER, EmployeeSpec, display_name, get

__all__ = [
    "Employee",
    "EmployeeResult",
    "EmployeeSpec",
    "ROSTER",
    "display_name",
    "get",
]
