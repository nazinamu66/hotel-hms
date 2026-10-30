from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction

from inventory.models import StockCostLayer, StockMovement
from kitchen.models import ProductionBatch


class Command(BaseCommand):
    help = (
        "Safely restore PROD references on legacy finished-food cost layers "
        "when an exact production movement match exists."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Persist unambiguous matches. Without this flag, the command is a dry run.",
        )
        parser.add_argument(
            "--hotel-id",
            type=int,
            help="Limit the scan to one hotel.",
        )
        parser.add_argument(
            "--tolerance-seconds",
            type=int,
            default=5,
            help="Maximum difference between movement and layer creation timestamps (default: 5).",
        )

    def handle(self, *args, **options):
        apply_changes = options["apply"]
        hotel_id = options.get("hotel_id")
        tolerance_seconds = options["tolerance_seconds"]

        if tolerance_seconds < 0:
            self.stderr.write(self.style.ERROR("Timestamp tolerance cannot be negative."))
            return

        batches = ProductionBatch.objects.filter(
            is_executed=True,
            produced_by__department__isnull=False,
        ).select_related(
            "recipe__product",
            "produced_by__department",
            "produced_by__department__hotel",
        ).order_by("created_at", "pk")

        if hotel_id:
            batches = batches.filter(produced_by__department__hotel_id=hotel_id)

        counts = {"planned": 0, "updated": 0, "already_tagged": 0, "skipped": 0}
        mode = "APPLY" if apply_changes else "DRY RUN"
        self.stdout.write(self.style.MIGRATE_HEADING(
            f"Production cost-layer provenance repair ({mode})"
        ))

        for batch in batches.iterator():
            reference = f"PROD-{batch.pk}"
            product = batch.recipe.product
            kitchen = batch.produced_by.department

            movements = StockMovement.objects.filter(
                reference=reference,
                movement_type="IN",
                product=product,
                to_department=kitchen,
                quantity=batch.quantity_produced,
            )
            if movements.count() != 1:
                counts["skipped"] += 1
                self.stdout.write(
                    f"SKIP batch #{batch.pk}: expected exactly one matching production IN movement; "
                    f"found {movements.count()}."
                )
                continue

            movement = movements.first()
            tagged = StockCostLayer.objects.filter(
                source_reference=reference,
                product=product,
                department=kitchen,
            )
            if tagged.exists():
                counts["already_tagged"] += 1
                self.stdout.write(f"OK batch #{batch.pk}: already has {tagged.count()} tagged layer(s).")
                continue

            lower = movement.created_at - timedelta(seconds=tolerance_seconds)
            upper = movement.created_at + timedelta(seconds=tolerance_seconds)
            candidates = StockCostLayer.objects.filter(
                product=product,
                department=kitchen,
                quantity_received=movement.quantity,
                source_reference="",
                created_at__gte=lower,
                created_at__lte=upper,
            )

            candidate_count = candidates.count()
            if candidate_count != 1:
                counts["skipped"] += 1
                self.stdout.write(
                    f"SKIP batch #{batch.pk}: expected exactly one untagged layer matching "
                    f"product, department, quantity and timestamp; found {candidate_count}."
                )
                continue

            layer = candidates.first()
            counts["planned"] += 1
            self.stdout.write(
                f"MATCH batch #{batch.pk} -> layer #{layer.pk} "
                f"({product.name}, qty {layer.quantity_received}, "
                f"created {layer.created_at:%Y-%m-%d %H:%M:%S.%f})."
            )

            if apply_changes:
                with transaction.atomic():
                    locked_layer = StockCostLayer.objects.select_for_update().get(pk=layer.pk)
                    # Re-check the only field this repair is allowed to change.
                    if locked_layer.source_reference:
                        counts["skipped"] += 1
                        self.stdout.write(
                            self.style.WARNING(
                                f"SKIP layer #{layer.pk}: it was tagged while this command was running."
                            )
                        )
                        continue
                    locked_layer.source_reference = reference
                    locked_layer.save(update_fields=["source_reference"])
                counts["updated"] += 1

        self.stdout.write("")
        self.stdout.write(
            f"Summary: {counts['planned']} match(es), {counts['updated']} updated, "
            f"{counts['already_tagged']} already tagged, {counts['skipped']} skipped."
        )
        if not apply_changes:
            self.stdout.write(self.style.WARNING(
                "Dry run only. No database records were changed. Re-run with --apply to persist matches."
            ))
