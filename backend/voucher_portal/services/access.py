"""Who can do what in the Voucher Portal.

Two independent axes, matching §11 of the requirements brief: a *role* decides
which actions a login may perform at all (create, approve, issue, report,
admin), and *department scope* decides which departments' batches and
vouchers it can see and act on. Neither is derived from `iam` - voucher
portal staff are not necessarily fleet-ops staff, even where they share a
Django login, so this is deliberately its own small system rather than
reusing `iam.Role`.
"""
from dataclasses import dataclass
from typing import FrozenSet, Optional

from .. import models


@dataclass(frozen=True)
class Access:
    role: str
    actions: FrozenSet[str]
    department_ids: Optional[FrozenSet[int]]  # None means every department

    def can(self, action: str) -> bool:
        return action in self.actions

    def sees_department(self, department_id) -> bool:
        return self.department_ids is None or department_id in self.department_ids


def get_access(user) -> Optional[Access]:
    """None means no portal access at all. Django staff/superusers get
    implicit Administrator access with no department restriction, so the
    bootstrap admin login works immediately without a separate grant."""
    if not user or not getattr(user, "is_authenticated", False):
        return None
    if user.is_superuser or user.is_staff:
        return Access(role="administrator", actions=models.ROLE_ACTIONS["administrator"], department_ids=None)

    try:
        row = user.voucher_access
    except models.PortalUserAccess.DoesNotExist:
        return None
    if not row.is_active:
        return None

    department_ids = frozenset(row.departments.values_list("id", flat=True))
    # Administrator and Report Viewer with nothing explicitly assigned see
    # every department (the common case for those roles); Requester/Approver
    # with nothing assigned see nothing, requiring an explicit grant.
    if row.role in ("administrator", "report_viewer") and not department_ids:
        department_ids = None

    return Access(role=row.role, actions=models.ROLE_ACTIONS.get(row.role, frozenset()), department_ids=department_ids)
