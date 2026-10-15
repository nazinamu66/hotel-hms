from accounting.services.journal import (
    post_journal_entry,
    get_account,
    get_system_account,
)


def post_payment(payment):

    hotel = payment.folio.hotel

    receivable = get_account(
        hotel,
        "1100",
    )

    if payment.method == "CASH":

        debit_account = get_account(
            hotel,
            "1000",
        )

    elif payment.method in ("POS", "TRANSFER"):

        if not payment.account:
            raise ValueError(
                "Bank account is required for POS or Transfer payment."
            )

        debit_account = payment.account

        if debit_account.hotel_id != hotel.id:
            raise ValueError(
                "Payment bank account does not belong to this hotel."
            )

    elif payment.method == "DEPOSIT":

        debit_account = get_system_account(
            hotel,
            "guest_deposits",
        )

    else:

        raise ValueError(
            "Unsupported payment method."
        )

    post_journal_entry(

        hotel=hotel,

        description="Guest payment",

        reference=f"PAY-{payment.id}",

        created_by=payment.collected_by,

        lines=[

            {
                "account": debit_account,
                "debit": payment.amount,
            },

            {
                "account": receivable,
                "credit": payment.amount,
            },

        ],
    )