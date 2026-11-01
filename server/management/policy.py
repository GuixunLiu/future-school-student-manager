"""Four explicit workspaces. Decisions never union a user's other roles."""
from dataclasses import dataclass

from business.store import Problem

ROLES = frozenset({'principal', 'admin', 'teacher', 'assessor'})
GLOBAL = {
    'principal': frozenset({'statistics.read', 'classes.read', 'submissions.read'}),
    'admin': frozenset({
        'accounts.manage', 'settings.manage', 'statistics.read', 'classes.read',
        'students.manage', 'classes.manage', 'hours.manage', 'attendance.correct',
        'data.export', 'data.delete', 'audit.read', 'submissions.read',
        'submissions.return', 'submissions.unlock', 'submissions.correct',
        'approvals.read', 'approvals.decide', 'attendance.start',
        'attendance.request',
    }),
    'teacher': frozenset(),
    'assessor': frozenset({
        'statistics.read', 'submissions.read', 'submissions.return',
        'submissions.export', 'scores.record', 'scores.calculate',
    }),
}


@dataclass(frozen=True)
class Scope:
    """Loaded by the server, never from fields submitted by a caller."""
    teacher_classes: frozenset = frozenset()
    approval_classes: frozenset = frozenset()


def workspace(actor, selected=None):
    if actor.get('kind') != 'user':
        raise Problem(403, 'user_required', '需要人员账号')
    roles = set(actor.get('roles', ())) & ROLES
    if selected is not None:
        if selected not in roles:
            raise Problem(403, 'workspace_forbidden', '没有所选身份权限')
        return selected
    if len(roles) == 1:
        return next(iter(roles))
    if len(roles) > 1:
        raise Problem(409, 'workspace_required', '请先选择工作身份')
    raise Problem(403, 'workspace_forbidden', '没有工作台权限')


def permits(actor, selected, action, *, scope=Scope(), target_org=None,
            class_id=None, owner_id=None, principal_export_allowed=False):
    role = workspace(actor, selected)
    # Callers must pass the organization of any target record they loaded.
    if target_org is not None and target_org != actor['org_id']:
        return False
    if action in GLOBAL[role]:
        return True
    if role == 'principal':
        # A server-side export policy must supply this flag; default deny.
        return action in {'data.export', 'submissions.export'} and principal_export_allowed
    if role == 'admin':
        return action == 'submissions.export'
    if role == 'teacher':
        if action in {'classes.read', 'attendance.start', 'attendance.request'}:
            return class_id is not None and class_id in scope.teacher_classes
        if action in {'submissions.read', 'submissions.write', 'submissions.submit'}:
            return owner_id is not None and owner_id == actor['id']
        if action == 'approvals.read':
            return owner_id == actor['id'] and class_id in scope.teacher_classes
    if role == 'assessor' and action in {'approvals.read', 'approvals.decide'}:
        return class_id is not None and class_id in scope.approval_classes
    return False


def require(actor, selected, action, **target):
    if not permits(actor, selected, action, **target):
        raise Problem(403, 'permission_denied', '当前身份无权操作这条资料')
