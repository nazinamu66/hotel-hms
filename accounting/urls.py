# accounting/urls.py

from django.urls import path
from . import views

app_name = "accounting"

urlpatterns = [
    path("chart/", views.chart_of_accounts, name="chart"),
    path("ledger/<int:account_id>/", views.account_ledger, name="ledger"),
    path("reports/pnl/", views.profit_and_loss, name="pnl"),
    path("reports/balance-sheet/", views.balance_sheet, name="balance_sheet"),
    path("close-day/", views.close_day, name="close_day"),
    path("journal/", views.journal_view, name="journal"),
    path("reports/trial-balance/", views.trial_balance, name="trial_balance"),
    path("journal/<int:journal_id>/", views.journal_detail, name="journal_detail"),
]