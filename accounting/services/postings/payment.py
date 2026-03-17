from accounting.services.journal import post_journal_entry, get_account


def post_payment(payment):

    hotel = payment.folio.hotel

    cash = get_account(hotel, "1000")
    receivable = get_account(hotel, "1100")

    post_journal_entry(

        hotel=hotel,

        description="Guest payment",

        reference=f"PAY-{payment.id}",

        created_by=payment.collected_by,

        lines=[
            {"account": cash, "debit": payment.amount},
            {"account": receivable, "credit": payment.amount},
        ],
    )