from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from .models import (EmployeeEmploymentHistory, EmployeeProfile, PersonnelAction, PersonnelActionEvent, EmployeeSeparationClearance, EmployeeSeparationClearanceEvent)


def _history_value(employee, field):
    value = getattr(employee, field)
    if field == "department":
        return {"id": employee.department_id, "name": str(employee.department)}
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


@transaction.atomic
def apply_personnel_action(action_id, *, today=None):
    """Apply one approved action on/after its effective date, exactly once."""
    today = today or timezone.localdate()
    action = (
        PersonnelAction.objects.select_for_update()
        .select_related("employee", "decided_by", "proposed_department")
        .filter(pk=action_id)
        .first()
    )
    if not action or action.status != PersonnelAction.APPROVED or action.applied_at:
        return False
    if action.effective_date > today:
        return False
    if not action.decided_by_id:
        raise ValidationError("An approved action must identify its approving Director/Admin.")

    employee = EmployeeProfile.objects.select_for_update().get(pk=action.employee_id)
    before = {
        "department": _history_value(employee, "department"),
        "job_title": employee.job_title,
        "employment_status": employee.employment_status,
        "end_date": employee.end_date.isoformat() if employee.end_date else None,
    }
    changed_fields = []

    if action.action_type in {PersonnelAction.PROMOTION, PersonnelAction.APPOINTMENT}:
        if not action.proposed_job_title:
            raise ValidationError("A proposed job title is required before applying this action.")
        employee.job_title = action.proposed_job_title.strip()
        changed_fields.append("job_title")
        if action.action_type == PersonnelAction.APPOINTMENT and action.proposed_department_id:
            employee.department = action.proposed_department
            changed_fields.append("department")

    elif action.action_type == PersonnelAction.TRANSFER:
        if not action.proposed_department_id:
            raise ValidationError("A proposed department is required before applying this transfer.")
        if action.proposed_department.hotel_id != employee.hotel_id:
            raise ValidationError("The destination department must belong to the employee's current hotel.")
        employee.department = action.proposed_department
        changed_fields.append("department")

    elif action.action_type == PersonnelAction.SUSPENSION:
        if employee.employment_status not in {EmployeeProfile.ACTIVE, EmployeeProfile.ON_LEAVE}:
            raise ValidationError("Only an active employee or an employee on leave can be suspended.")
        employee.employment_status = EmployeeProfile.SUSPENDED
        changed_fields.append("employment_status")

    elif action.action_type == PersonnelAction.REINSTATEMENT:
        if employee.employment_status != EmployeeProfile.SUSPENDED:
            raise ValidationError("Only a suspended employee can be reinstated.")
        employee.employment_status = EmployeeProfile.ACTIVE
        changed_fields.append("employment_status")

    elif action.action_type in {
        PersonnelAction.RESIGNATION, PersonnelAction.RETIREMENT,
        PersonnelAction.TERMINATION, PersonnelAction.DISMISSAL,
    }:
        if employee.employment_status not in {
            EmployeeProfile.ACTIVE, EmployeeProfile.ON_LEAVE, EmployeeProfile.SUSPENDED,
        }:
            raise ValidationError("This employee is already separated from active employment.")
        employee.employment_status = {
            PersonnelAction.RESIGNATION: EmployeeProfile.RESIGNED,
            PersonnelAction.RETIREMENT: EmployeeProfile.RETIRED,
            PersonnelAction.TERMINATION: EmployeeProfile.TERMINATED,
            PersonnelAction.DISMISSAL: EmployeeProfile.TERMINATED,
        }[action.action_type]
        employee.end_date = action.effective_date
        changed_fields.extend(["employment_status", "end_date"])

    # Query, warning, commendation and award remain in the Personnel Action
    # register and do not mutate the employee's core employment fields.
    if changed_fields:
        employee.updated_by = action.decided_by
        employee.full_clean()
        employee.save(update_fields=list(dict.fromkeys(changed_fields + ["updated_by", "updated_at"])))
        after = {
            "department": _history_value(employee, "department"),
            "job_title": employee.job_title,
            "employment_status": employee.employment_status,
            "end_date": employee.end_date.isoformat() if employee.end_date else None,
        }
        event_for_field = {
            "department": "DEPARTMENT_TRANSFER",
            "job_title": "JOB_TITLE_CHANGE",
            "employment_status": "STATUS_CHANGE",
            "end_date": "END_DATE_CHANGE",
        }
        grouped = {}
        for field in changed_fields:
            if before[field] != after[field]:
                event_type = event_for_field[field]
                grouped.setdefault(event_type, {"old": {}, "new": {}})
                grouped[event_type]["old"][field] = before[field]
                grouped[event_type]["new"][field] = after[field]
        for event_type, values in grouped.items():
            EmployeeEmploymentHistory.objects.create(
                employee=employee,
                event_type=event_type,
                effective_date=action.effective_date,
                previous_values=values["old"],
                new_values=values["new"],
                reason=f"{action.get_action_type_display()}: {action.title}. {action.reason}".strip(),
                recorded_by=action.decided_by,
            )

    action.applied_at = timezone.now()
    action.save(update_fields=["applied_at", "updated_at"])

    separation_types = {PersonnelAction.RESIGNATION, PersonnelAction.RETIREMENT, PersonnelAction.TERMINATION, PersonnelAction.DISMISSAL}
    if action.action_type in separation_types:
        clearance, created = EmployeeSeparationClearance.objects.get_or_create(action=action)
        if created:
            EmployeeSeparationClearanceEvent.objects.create(
                clearance=clearance, event_type="CREATED", actor=action.decided_by,
                note="Automatically created when the separation Personnel Action became effective.",
            )

    PersonnelActionEvent.objects.create(
        action=action,
        event_type="APPLIED",
        actor=action.decided_by,
        note=f"Automatically applied by the effective-date processor on {action.effective_date:%d %b %Y}.",
    )
    return True
