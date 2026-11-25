from django.core.management.base import BaseCommand
from django.core.exceptions import ValidationError
from django.utils import timezone

from personnel.action_services import apply_personnel_action
from personnel.models import PersonnelAction


class Command(BaseCommand):
    help = "Apply approved Personnel Actions whose effective dates have arrived."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="List due actions without applying them.",
        )

    def handle(self, *args, **options):
        today = timezone.localdate()
        due_actions = PersonnelAction.objects.filter(
            status=PersonnelAction.APPROVED,
            applied_at__isnull=True,
            effective_date__lte=today,
        ).order_by("effective_date", "pk")

        due_ids = list(due_actions.values_list("pk", flat=True))
        if options["dry_run"]:
            self.stdout.write(f"{len(due_ids)} approved action(s) are due as of {today}.")
            for action in due_actions:
                self.stdout.write(f"  {action.reference} · {action.get_action_type_display()} · {action.employee.full_name}")
            return

        applied = 0
        skipped = 0
        failed = 0
        for action_id in due_ids:
            try:
                if apply_personnel_action(action_id, today=today):
                    applied += 1
                else:
                    skipped += 1
            except ValidationError as exc:
                failed += 1
                action = PersonnelAction.objects.get(pk=action_id)
                message = "; ".join(exc.messages)
                self.stderr.write(self.style.ERROR(f"{action.reference}: {message}"))

        self.stdout.write(self.style.SUCCESS(
            f"Personnel Action processing complete. Applied: {applied}; skipped: {skipped}; needs attention: {failed}."
        ))
