# Accounting, operations flow and user management

Built for a fleet owner running 1000+ vehicles, where the work is split across branches
and no single person touches every module.

## 1. User management (`/api/v1/iam/`)

| Resource | What it is |
| --- | --- |
| `organisations/` | The transport company, with GSTIN, PAN, CIN and the financial-year start month |
| `branches/` | Branches, depots, warehouses and workshops, each with its own state GST registration |
| `roles/` | Named permission bundles, e.g. Dispatcher, Accounts executive |
| `users/` | A login plus its employee code, designation, role, branch and reporting line |
| `audit-log/` | Read-only. Every create, update and delete with the person and IP behind it |
| `permissions/` | The catalogue of permissions a role can grant |
| `me/` | Who am I, which branch, and what may I do |

### Permissions

Nineteen permissions across `operations`, `masters`, `rates`, `maintenance`, `compliance`,
`expenses`, `accounting`, `users` and `reports`. Read and write are separate, so
`accounting.view` lets someone read the ledger while `accounting.manage` is needed to
create vouchers and `accounting.post` to post or reverse them.

Seven roles ship ready to use: Administrator, Branch manager, Dispatcher, Accounts
executive, Accounts manager, Workshop supervisor and Viewer.

**Enforcement is backwards compatible by design.** Superusers always pass, and a login with
no role attached keeps unrestricted access — so switching this on cannot lock an existing
deployment out. Restriction begins the moment you give someone a role.

```bash
POST /api/v1/iam/users/          # username + password + employee_code + role + branch
POST /api/v1/iam/users/{id}/deactivate/    # blocks the login immediately
POST /api/v1/iam/users/{id}/set_password/
```

Passwords go through Django's validators (minimum 10 characters, not common, not numeric,
not similar to the username).

## 2. Accounting (`/api/v1/accounting/`)

Real double entry. Every financial event becomes a `JournalEntry` whose lines must balance,
and the reports are derived from those lines rather than from module-specific tables — which
is what keeps the books consistent when several people are entering data at once.

| Resource | Notes |
| --- | --- |
| `accounts/` | Chart of accounts. 29 heads seeded for an Indian transporter |
| `cost-centres/` | Attach a line to a truck, branch, route or driver |
| `journal-entries/` | Vouchers with their lines; `POST {id}/reverse/` posts the mirror |
| `vendor-bills/` | Purchase invoices with GST and TDS |
| `payments/` | Receipts and payments, allocated against invoices or bills |
| `fiscal-years/` | Indian April-to-March years |

### Posting rules

| Event | Entry |
| --- | --- |
| Customer invoice | Dr Sundry debtors · Cr Freight income · Cr Output GST |
| Vendor bill | Dr Expense head · Dr Input GST · Cr Sundry creditors · Cr TDS payable |
| Receipt | Dr Bank · Cr Sundry debtors |
| Payment | Dr Sundry creditors (or Driver advances) · Cr Bank |
| Trip expense | Dr the matching head · Cr Driver advances when the driver paid, else Cash |
| Fuel purchase | Dr Diesel · Cr the wallet it was bought on |

Each rule is idempotent per source document: posting the same invoice twice returns the
existing entry rather than double counting revenue.

Two guards protect the books: an entry whose debits and credits differ is rejected, and a
line posted to a **group heading** (an account with children, such as "Expenses") is refused
— postings belong on its leaves.

### Reports

```
GET /api/v1/accounting/reports/trial-balance/?from=&to=&branch=
GET /api/v1/accounting/reports/profit-and-loss/?from=&to=
GET /api/v1/accounting/reports/ledger/?account=1200&from=&to=
GET /api/v1/accounting/reports/receivable-ageing/
GET /api/v1/accounting/reports/payable-ageing/
GET /api/v1/accounting/reports/vehicle-profitability/?from=&to=
GET /api/v1/accounting/reports/gst-summary/?from=&to=
```

Ageing buckets are current / 1-30 / 31-60 / 61-90 / 90+. Vehicle profitability nets order
revenue against diesel and on-road expenses per truck — the number a fleet owner lives by.

### Chart of accounts

Seeded by `python manage.py seed_accounting` (idempotent):

```
1000 Assets            2000 Liabilities        4000 Income
 1110 Cash in hand      2100 Sundry creditors   4100 Freight income
 1120 Bank accounts     2200 Output GST         4200 Detention and other income
 1200 Sundry debtors    2300 TDS payable       5000 Expenses
 1300 Input GST         2400 Vehicle loans      5100 Diesel      5500 Tyres
 1400 Driver advances  3000 Equity              5200 Toll/FASTag 5600 Hired vehicle freight
 1510 Vehicles          3100 Owner capital      5300 Driver bhatta 5700 Loading/unloading
                                                5400 Maintenance  5800 RTO/permits/fines
                                                                  5900 Administrative
```

The same command seeds the seven roles, a head-office branch and the current fiscal year.

## 3. Operations flow

At scale, demand arrives before a truck is committed to it. The flow is now:

```
Indent (customer demand)
   -> allocate a vehicle and driver
   -> convert to a consignment Order   (distance and freight priced from the rate card)
   -> dispatch -> tracking activity
   -> ePOD  (OTP to the consignee -> driver capture -> office verifies)
   -> invoice (freight and GST taken from the rate card, posted to the ledger)
   -> receipt
```

```bash
POST /api/v1/indents/                  # capture demand
POST /api/v1/indents/{id}/allocate/    # {"vehicle": 1, "driver": 2}
POST /api/v1/indents/{id}/convert/     # becomes a priced, allocated Order
POST /api/v1/indents/{id}/cancel/
POST /api/v1/orders/{id}/pod-request/  # issue the delivery OTP
POST /api/v1/orders/{id}/pod-submit/   # driver capture at the drop
POST /api/v1/orders/{id}/invoice/      # bill it and post the entry, in one step
POST /api/v1/service-rates/project/    # projected margin for a lane before committing a truck
GET  /api/v1/orders/{id}/profitability/  # revenue, diesel, on-road cost, margin, cost per km
```

Nobody types a freight figure onto an invoice. `orders/{id}/invoice/` reprices the consignment
from its rate card, carries the freight, GST percentage, RCM flag and place of supply across,
recomputes the total from its parts and posts the entry. It refuses an undelivered consignment
or one whose ePOD has not been verified, and billing the same order twice returns the invoice
already raised — so revenue cannot be double counted from a double click.

Orders and indents both carry a `branch`, and `Order` is indexed on `(status, created_at)`,
`(branch, status)` and `tracking_number` for list performance at fleet scale.

## 4. Console

New sections in the sidebar:

- **TRANSPORT → Indents** — demand board with drag-and-drop between any two columns and a
  detail drawer on click; dropping onto "allocated" opens the truck allocation panel, and
  "converted" creates the priced order
- **TRANSPORT → ePOD** — the delivery desk: issue the OTP, record what the driver captured,
  and verify or reject it. Consignments held by a shortage or damage sit in one queue
- **COMMERCIAL → Rates** — the freight estimator now has a second mode that projects the
  margin on a lane using the fleet's own diesel and on-road spend, with the break-even rate
  per km
- **ACCOUNTS → Ledger** — chart of accounts with live balances, group headings marked
- **ACCOUNTS → Vouchers** — journal entries, plus a composer that will not let you post an
  unbalanced entry or select a group heading
- **ACCOUNTS → Vendor bills** — purchase invoices with GST and TDS
- **ACCOUNTS → Payments** — receipts and payments, settling a bill in one step
- **ACCOUNTS → Financials** — the six reports above, with a date window
- **ADMIN → Users / Roles / Branches / Audit trail** — the role editor is a permission
  checklist grouped by module

The sidebar footer shows who is signed in, with their role, and a sign-out button. A token
that has been revoked, or a login that has been deactivated, returns the operator to the
sign-in screen rather than leaving a workspace that can no longer load.

## 5. Tests

```
python manage.py test          # 107 tests across fleet, accounting and iam
```

Covering: rating and GST, geofencing, the order and indent lifecycle, the ePOD workflow,
automatic invoicing and the ePOD gate on it, lane margin projection, mileage, compliance
windows, double-entry balance rules, group-heading rejection, every posting rule and its
idempotency, all seven reports, ageing buckets, payment allocation, role enforcement
(including that a login without a role keeps working), password validation and the audit trail.
