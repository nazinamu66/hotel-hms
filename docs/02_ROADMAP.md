Phase 1 — Security and reliability [P0]
- Enforce hotel and department data isolation on every view, API, form and service.
- Centralize role permissions and approval boundaries.
- Make state-changing operations POST-only and validate state transitions.
- Use atomic transactions and row locks for stock, payment and production operations.
- Add automated tests for permissions, duplicate submissions and rollback behaviour.
- Remove dead code and reconcile model, form, view and status definitions.

Phase 2 — Kitchen and inventory [P0]
- Complete ingredient request and approval lifecycle.
- Complete direct purchasing, goods receipt and partial fulfilment.
- Unify stock movements and FIFO costing.
- Enforce recipe control modes, version history and unit conversions.
- Add production variance, waste, yield and batch-cost snapshots.
- Improve kitchen ticket transitions, timing and kitchen display.

Phase 3 — Restaurant and Front Desk [P0]
- Validate POS charge, payment, room charge and refund workflows.
- Make order posting, stock deduction, folio charges and COGS idempotent.
- Complete reservation, walk-in, check-in and check-out flows.
- Validate room availability and prevent overlapping active reservations.
- Reconcile guest folios, deposits, payments, invoices and refunds.
- Test room charges from restaurant and other enabled hotel outlets.

Phase 4 — Housekeeping, Laundry and Linen [P1]
- Room cleaning queues, assignments, inspections and room-ready workflow.
- Linen issue, return, loss, damage, laundry cycles and stock reconciliation.
- Departmental supplies requests and consumption tracking.
- Prevent room status conflicts between Front Desk, Housekeeping and Maintenance.

Phase 5 — Maintenance and assets [P1]
- Work orders, assignment, priority, SLA and completion records.
- Preventive maintenance schedules and overdue alerts.
- Equipment register, warranty, service history and lifecycle costs.
- Spare parts consumption linked to inventory and accounting.

Phase 6 — Finance and management [P0]
- Supplier statements and accounts-payable ageing.
- Cashier shift reconciliation, cash handovers and variance approvals.
- Bank reconciliation and payment-reference controls.
- Inventory valuation and general-ledger reconciliation.
- Daily revenue, occupancy, department profitability and exception reports.
- Period closing, locked accounting periods and audit trails.

Phase 7 — Platform and deployment [P0]
- Move production data to PostgreSQL and configure environment-based settings.
- Move secrets out of source code; set DEBUG=False and configure allowed hosts.
- Configure HTTPS, secure cookies, CSRF, backups and restore testing.
- Set up logging, error monitoring, health checks and alerting.
- Automate migrations, tests, static assets and deployment through CI/CD.
- Document user roles, operational procedures, recovery and release rollback.

Phase 8 — Advanced capabilities [P2]
- Supplier purchase planning, lead times and price history.
- Demand forecasting and ingredient usage trends.
- Menu engineering, recipe profitability and food-cost alerts.
- Multi-branch consolidated reporting and organization-level controls.
- Audit exports, scheduled reports and management dashboards.
- Optional integrations for accounting exports, POS hardware and guest messaging.


The next Kitchen features









Feature 1 — Complete ingredient requests and purchasing
Next priority
- Track requested, approved, issued, and outstanding quantities.
- Support partial deliveries for direct purchases.
- Support partial store transfers and prevent over-issuing.
- Update request statuses automatically.
- Keep stock, FIFO costs, and accounting in sync.





Feature 2 — Recipe control and version history
- Enforce STRICT, TOLERANCE, and FLEXIBLE recipe controls during production.
- Record recipe changes as versions so past batches retain their original recipe context.
- Validate units and conversions to avoid stock calculation errors.




Feature 3 — Production costing, yield and waste
- Save an auditable cost snapshot for each production batch.
- Compare expected versus actual ingredient usage.
- Record spoilage, waste, and yield.
- Report cost per portion and production variances.




Feature 4 — Kitchen order and ticket management
- Enforce valid transitions: New → Preparing → Ready → Served.
- Record acceptance, preparation times, and delays.
- Improve the kitchen display for pending and overdue orders.
- Prevent duplicate serving and incorrect status changes.



Feature 5 — Kitchen reporting and controls
- Ingredient usage and replenishment reports.
- Food production and cost-per-portion reports.
- Waste, yield, and variance reports.
- Role-based permissions and regression tests across the workflows.




3. Implementation order
1. Foundation and access control
   
   Consolidated hotel selection, organization scoping and date filters.
2. Financial overview
   
   Revenue, expenses, COGS, profitability and receivables, reconciled to accounting.
3. Hotel performance
   
   Occupancy, arrivals, departures and hotel-by-hotel comparisons.
4. Corporate accounts
   
   Company invoices, outstanding balances and deposits.
5. Operational overview
   
   Housekeeping, maintenance, kitchen, restaurant and inventory.
6. Management alerts
   
   Pending approvals, overdue work, low stock and unresolved cash variances.