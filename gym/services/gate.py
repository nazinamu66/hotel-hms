from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from gym.models import GymMembership, GymVisit


@transaction.atomic
def check_in_member(*, membership_id, hotel, user, note=""):
    membership = (
        GymMembership.objects.select_for_update()
        .select_related("member")
        .filter(pk=membership_id, hotel=hotel, member__hotel=hotel)
        .first()
    )
    if not membership:
        raise ValidationError("Membership could not be found for this hotel.")
    if not membership.member.is_active:
        raise ValidationError("This member record is inactive.")
    today = timezone.localdate()
    if membership.status != GymMembership.ACTIVE:
        raise ValidationError(f"This membership is {membership.get_status_display().lower()}.")
    if membership.start_date > today:
        raise ValidationError("This membership has not started yet.")
    if membership.end_date < today:
        raise ValidationError("This membership has expired.")
    if GymVisit.objects.filter(membership=membership, exited_at__isnull=True).exists():
        raise ValidationError("This member is already checked in.")
    visit = GymVisit.objects.create(
        membership=membership,
        hotel=hotel,
        checked_in_by=user,
        gate_note=(note or "").strip()[:255],
    )
    return visit


@transaction.atomic
def check_out_member(*, visit_id, hotel, user):
    visit = (
        GymVisit.objects.select_for_update()
        .select_related("membership__member")
        .filter(pk=visit_id, hotel=hotel, membership__hotel=hotel)
        .first()
    )
    if not visit:
        raise ValidationError("Open visit could not be found for this hotel.")
    if visit.exited_at is not None:
        raise ValidationError("This visit has already been checked out.")
    visit.exited_at = timezone.now()
    visit.checked_out_by = user
    visit.save(update_fields=["exited_at", "checked_out_by"])
    return visit
