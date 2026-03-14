def calculate_restaurant_summary(
    report_date,
    department=None,
    hotel=None,
    staff=None
):
    ...
    return {
        "total_sales": ...,
        "total_refunds": ...,
        "net_sales": ...,
        "payment_summary": ...,
        "staff_sales": ...,
        "room_total": ...,
        "walkin_total": ...,
        "order_count": ...,
    }