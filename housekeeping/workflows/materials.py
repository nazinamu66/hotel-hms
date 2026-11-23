"""Backward-compatible entry point for assignment-based material consumption."""

from housekeeping.workflows.material_custody import consume_assigned_material


def consume_cleaning_material(assignment, product, quantity, user, notes=""):
    """Record cleaning consumption from the material allocation for this assignment."""
    usages = consume_assigned_material(
        assignment=assignment,
        product=product,
        quantity=quantity,
        user=user,
        purpose="CLEANING",
        notes=notes,
    )
    return usages[0] if usages else None
