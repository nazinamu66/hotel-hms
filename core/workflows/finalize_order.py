from django.db import transaction

from django.core.exceptions import ValidationError

def validate_order(order):

    if order.is_cogs_posted:
        raise ValidationError(
            "COGS has already been posted."
        )


def post_cogs(order):

    from accounting.services.postings.cogs import post_cogs_for_order

    post_cogs_for_order(order)


def mark_cogs_posted(order):

    order.is_cogs_posted = True

    order.save(
        update_fields=[
            "is_cogs_posted",
        ]
    )

@transaction.atomic
def finalize_order(order):

    validate_order(order)

    post_cogs(order)

    mark_cogs_posted(order)

    return order