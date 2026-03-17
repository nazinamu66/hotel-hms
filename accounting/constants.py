ASSET = "asset"
LIABILITY = "liability"
EQUITY = "equity"
INCOME = "income"
EXPENSE = "expense"
BANK = "bank"


DEFAULT_ACCOUNTS = [

    # Assets
    {"code": "1000", "name": "Cash", "type": ASSET},
    {"code": "1010", "name": "Bank", "type": BANK},
    {"code": "1100", "name": "Accounts Receivable", "type": ASSET},
    {"code": "1200", "name": "Inventory Asset", "type": ASSET},

    # Liabilities
    {"code": "2000", "name": "Accounts Payable", "type": LIABILITY},
    {"code": "2100", "name": "Guest Deposits", "type": LIABILITY},

    # Equity
    {"code": "3000", "name": "Owner Capital", "type": EQUITY},

    # Income
    {"code": "4000", "name": "Room Revenue", "type": INCOME},
    {"code": "4100", "name": "Restaurant Revenue", "type": INCOME},
    {"code": "4200", "name": "Other Revenue", "type": INCOME},

    # Expenses
    {"code": "5000", "name": "Cost of Goods Sold", "type": EXPENSE},
    {"code": "5100", "name": "Maintenance Expense", "type": EXPENSE},
    {"code": "5200", "name": "Utilities Expense", "type": EXPENSE},
]