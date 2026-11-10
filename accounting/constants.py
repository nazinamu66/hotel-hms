# ==================================================
# ACCOUNT TYPES
# ==================================================

CASH = "cash"
BANK = "bank"

CURRENT_ASSET = "current_asset"
INVENTORY = "inventory"
FIXED_ASSET = "fixed_asset"

CURRENT_LIABILITY = "current_liability"
LONG_TERM_LIABILITY = "long_term_liability"

EQUITY = "equity"

INCOME = "income"
OTHER_INCOME = "other_income"

COGS = "cogs"

EXPENSE = "expense"
OTHER_EXPENSE = "other_expense"


# ==================================================
# SYSTEM ACCOUNT KEYS
# ==================================================

CASH_ACCOUNT = "cash"

BANK_CONTROL = "bank_control"

POS_CLEARING = "pos_clearing"

ACCOUNTS_RECEIVABLE = "accounts_receivable"

INVENTORY_ASSET = "inventory_asset"

EQUIPMENT_ASSET = "equipment_asset"

FINISHED_GOODS_INVENTORY = "finished_goods_inventory"

ACCOUNTS_PAYABLE = "accounts_payable"

SUPPLIER_ADVANCES = "supplier_advances"

GUEST_DEPOSITS = "guest_deposits"

OWNER_CAPITAL = "owner_capital"

RETAINED_EARNINGS = "retained_earnings"

OPENING_BALANCE_EQUITY = "opening_balance_equity"

ROOM_REVENUE = "room_revenue"

RESTAURANT_REVENUE = "restaurant_revenue"

OTHER_REVENUE = "other_revenue"

COST_OF_GOODS_SOLD = "cost_of_goods_sold"

SPOILAGE_EXPENSE = "spoilage_expense"
INVENTORY_VARIANCE_EXPENSE = "inventory_variance_expense"
INVENTORY_VARIANCE_GAIN = "inventory_variance_gain"

MAINTENANCE_EXPENSE = "maintenance_expense"

LAUNDRY_EXPENSE = "laundry_expense"

RESTAURANT_EXPENSE = "restaurant_expense"

UTILITIES_EXPENSE = "utilities_expense"


# ==================================================
# ACCOUNT GROUPS
# ==================================================

ASSET_TYPES = [
    CASH,
    BANK,
    CURRENT_ASSET,
    INVENTORY,
    FIXED_ASSET,
]

LIABILITY_TYPES = [
    CURRENT_LIABILITY,
    LONG_TERM_LIABILITY,
]

INCOME_TYPES = [
    INCOME,
    OTHER_INCOME,
]

EXPENSE_TYPES = [
    EXPENSE,
    COGS,
    OTHER_EXPENSE,
]

# ==================================================
# NORMAL BALANCE GROUPS
# ==================================================

DEBIT_NORMAL_TYPES = (
    ASSET_TYPES +
    EXPENSE_TYPES
)

CREDIT_NORMAL_TYPES = (
    LIABILITY_TYPES +
    [EQUITY] +
    INCOME_TYPES
)


# ==================================================
# DEFAULT CHART OF ACCOUNTS
# ==================================================

DEFAULT_ACCOUNTS = [

    # --------------------------
    # CASH
    # --------------------------

    {
        "code": "1000",
        "name": "Cash",
        "system_key": CASH_ACCOUNT,
        "type": CASH,
        "is_system": True,
        "allow_posting": True,
        "allow_manual_entries": False,
    },

    # --------------------------
    # BANKS
    # --------------------------

    {
        "code": "1010",
        "name": "Bank Accounts",
        "system_key": BANK_CONTROL,
        "type": BANK,
        "is_system": True,
        "allow_posting": False,
        "allow_manual_entries": False,
    },

    {
        "code": "1020",
        "name": "POS Clearing",
        "system_key": POS_CLEARING,
        "type": CURRENT_ASSET,
        "is_system": True,
        "allow_posting": True,
        "allow_manual_entries": False,
    },

    {
        "code": "5150",
        "name": "Laundry Expense",
        "system_key": LAUNDRY_EXPENSE,
        "type": EXPENSE,
        "is_system": True,
        "allow_posting": True,
        "allow_manual_entries": True,
    },

    # --------------------------
    # RECEIVABLES
    # --------------------------

    {
        "code": "1100",
        "name": "Accounts Receivable",
        "system_key": ACCOUNTS_RECEIVABLE,
        "type": CURRENT_ASSET,
        "is_system": True,
        "allow_posting": True,
        "allow_manual_entries": False,
    },

    # --------------------------
    # INVENTORY
    # --------------------------

    {
        "code": "1200",
        "name": "Inventory Asset",
        "system_key": INVENTORY_ASSET,
        "type": INVENTORY,
        "is_system": True,
        "allow_posting": True,
        "allow_manual_entries": False,
    },

    {
        "code": "1250",
        "name": "Equipment and Fixed Assets",
        "system_key": EQUIPMENT_ASSET,
        "type": FIXED_ASSET,
        "is_system": True,
        "allow_posting": True,
        "allow_manual_entries": False,
    },

    {
        "code": "1220",
        "name": "Supplier Advances",
        "system_key": SUPPLIER_ADVANCES,
        "type": CURRENT_ASSET,
        "is_system": True,
        "allow_posting": True,
        "allow_manual_entries": False,
    },

    {
        "code": "1210",
        "name": "Finished Goods Inventory",
        "system_key": FINISHED_GOODS_INVENTORY,
        "type": INVENTORY,
        "is_system": True,
        "allow_posting": True,
        "allow_manual_entries": False,
    },

    # --------------------------
    # LIABILITIES
    # --------------------------

    {
        "code": "2000",
        "name": "Accounts Payable",
        "system_key": ACCOUNTS_PAYABLE,
        "type": CURRENT_LIABILITY,
        "is_system": True,
        "allow_posting": True,
        "allow_manual_entries": False,
    },

    {
        "code": "2100",
        "name": "Guest Deposits",
        "system_key": GUEST_DEPOSITS,
        "type": CURRENT_LIABILITY,
        "is_system": True,
        "allow_posting": True,
        "allow_manual_entries": False,
    },

    # --------------------------
    # EQUITY
    # --------------------------

    {
        "code": "3000",
        "name": "Owner Capital",
        "system_key": OWNER_CAPITAL,
        "type": EQUITY,
        "is_system": True,
        "allow_posting": True,
        "allow_manual_entries": True,
    },

    {
        "code": "3001",
        "name": "Retained Earnings",
        "system_key": RETAINED_EARNINGS,
        "type": EQUITY,
        "is_system": True,
        "allow_posting": True,
        "allow_manual_entries": False,
    },

    {
        "code": "3002",
        "name": "Opening Balance Equity",
        "system_key": OPENING_BALANCE_EQUITY,
        "type": EQUITY,
        "is_system": True,
        "allow_posting": True,
        "allow_manual_entries": False,
    },

    # --------------------------
    # REVENUE
    # --------------------------

    {
        "code": "4000",
        "name": "Room Revenue",
        "system_key": ROOM_REVENUE,
        "type": INCOME,
        "is_system": True,
        "allow_posting": True,
        "allow_manual_entries": False,
    },

    {
        "code": "4100",
        "name": "Restaurant Revenue",
        "system_key": RESTAURANT_REVENUE,
        "type": INCOME,
        "is_system": True,
        "allow_posting": True,
        "allow_manual_entries": False,
    },

    {
        "code": "4200",
        "name": "Other Revenue",
        "system_key": OTHER_REVENUE,
        "type": OTHER_INCOME,
        "is_system": True,
        "allow_posting": True,
        "allow_manual_entries": True,
    },

    # --------------------------
    # COST OF SALES
    # --------------------------

    {
        "code": "5000",
        "name": "Cost of Goods Sold",
        "system_key": COST_OF_GOODS_SOLD,
        "type": COGS,
        "is_system": True,
        "allow_posting": True,
        "allow_manual_entries": False,
    },

    # --------------------------
    # OPERATING EXPENSES
    # --------------------------

    {
        "code": "5001",
        "name": "Spoilage Expense",
        "system_key": SPOILAGE_EXPENSE,
        "type": EXPENSE,
        "is_system": True,
        "allow_posting": True,
        "allow_manual_entries": True,
    },

    {
        "code": "5002",
        "name": "Inventory Variance Expense",
        "system_key": INVENTORY_VARIANCE_EXPENSE,
        "type": EXPENSE,
        "is_system": True,
        "allow_posting": True,
        "allow_manual_entries": False,
    },

    {
        "code": "4300",
        "name": "Inventory Variance Gain",
        "system_key": INVENTORY_VARIANCE_GAIN,
        "type": OTHER_INCOME,
        "is_system": True,
        "allow_posting": True,
        "allow_manual_entries": False,
    },

    {
        "code": "5100",
        "name": "Maintenance Expense",
        "system_key": MAINTENANCE_EXPENSE,
        "type": EXPENSE,
        "is_system": True,
        "allow_posting": True,
        "allow_manual_entries": True,
    },

    {
        "code": "5300",
        "name": "Restaurant Expense",
        "system_key": RESTAURANT_EXPENSE,
        "type": EXPENSE,
        "is_system": True,
        "allow_posting": True,
        "allow_manual_entries": True,
    },

    {
        "code": "5200",
        "name": "Utilities Expense",
        "system_key": UTILITIES_EXPENSE,
        "type": EXPENSE,
        "is_system": True,
        "allow_posting": True,
        "allow_manual_entries": True,
    },
]