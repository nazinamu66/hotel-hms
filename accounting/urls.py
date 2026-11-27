# accounting/urls.py

from django.urls import path
from . import views

app_name = "accounting"

urlpatterns = [
    path("", views.accounting_dashboard, name="dashboard"),
    path("supplier-payments/", views.supplier_payments, name="supplier_payments"),
    path("staff-reimbursements/", views.staff_reimbursements, name="staff_reimbursements"),
    path("staff-reimbursements/<int:claim_id>/", views.staff_reimbursement_detail, name="staff_reimbursement_detail"),
    path("chart/", views.chart_of_accounts, name="chart"),
    path("opening-balances/", views.financial_opening_balances, name="financial_opening_balances"),
    path("opening-balances/adjustments/", views.financial_opening_adjustments, name="financial_opening_adjustments"),
    path("ledger/<int:account_id>/", views.account_ledger, name="ledger"),
    path("ledger/<int:account_id>/export.xlsx", views.account_ledger_export_xlsx, name="ledger_export_xlsx"),
    path("ledger/<int:account_id>/export.pdf", views.account_ledger_export_pdf, name="ledger_export_pdf"),
    path("reports/pnl/", views.profit_and_loss, name="pnl"),
    path("reports/pnl/export.xlsx", views.profit_and_loss_export_xlsx, name="pnl_export_xlsx"),
    path("reports/pnl/export.pdf", views.profit_and_loss_export_pdf, name="pnl_export_pdf"),
    path("reports/balance-sheet/", views.balance_sheet, name="balance_sheet"),
    path("reports/balance-sheet/export.xlsx", views.balance_sheet_export_xlsx, name="balance_sheet_export_xlsx"),
    path("reports/balance-sheet/export.pdf", views.balance_sheet_export_pdf, name="balance_sheet_export_pdf"),
    path("close-day/", views.close_day, name="close_day"),
    path("journal/", views.journal_view, name="journal"),
    path("reports/trial-balance/", views.trial_balance, name="trial_balance"),
    path("journal/<int:journal_id>/", views.journal_detail, name="journal_detail"),
    path("accounts/create/",views.create_account,name="create_account",),
    path("accounts/<int:account_id>/edit/",views.edit_account,name="edit_account",),
    path(
        "cash-deposit/",
        views.cash_deposit,
        name="cash_deposit",
    ),

    path(
        "bank-transfer/",
        views.bank_transfer,
        name="bank_transfer",
    ),
]