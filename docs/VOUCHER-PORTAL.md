# Voucher Portal (ADCOOP) — Phases 1 & 2, and Phase 3 minus integrations

An authenticated extension of the public gift voucher desk (`vouchers/`, see
[docs/GIFT-VOUCHERS.md](GIFT-VOUCHERS.md)), built to the requirements brief and the
attached ADCOOP discount coupon template. It is a **separate Django app**
(`voucher_portal`) with its own models — nothing here shares a table or a
foreign key with `vouchers`, so the two run side by side and neither's
migrations touch the other's data.

- Backend: `backend/voucher_portal/`, routed at `/api/v1/voucher-portal/`.
- Frontend: `app/voucher-portal/page.tsx`, served at `/voucher-portal`.

## Why a separate app, and why login here but not on `/vouchers`

`/vouchers` is deliberately public (a store-till page, no login). The requirements
brief requires login everywhere (§13). Those two facts can't both apply to one
app, so the portal is new and public `/vouchers` is untouched — see decision D1
below. Every `voucher-portal/` endpoint uses the project default
(`IsAuthenticated`, token or session auth via `iam`).

## Discount model

A batch is either `percentage` or `fixed`, never both:

- **Percentage**: `percentage_value` (0 < x ≤ 100), optional `max_discount_value`
  ("up to AED X"). Per the brief, the cap is **not enforced as mandatory** — an
  uncapped percentage voucher is allowed; the form nudges toward setting one but
  doesn't require it.
- **Fixed**: `fixed_value` (> 0).

`PortalBatch.display_value` formats both the same way the coupon prints them —
`"50% OFF (up to AED 50.00)"` or `"AED 500.00 OFF"`. Watch for
`Decimal.normalize()`: it renders whole numbers in scientific notation
(`Decimal("50").normalize()` → `5E+1`), which is wrong for anything a human
reads — `models.trim_decimal()` exists specifically to avoid that, and both the
model's `display_value` and the PDF renderer use it.

## Validity

The create form only collects `valid_to` ("Valid until") — no start date. §3.3
already treats the start date as stored-but-unprinted; the form goes a step
further and doesn't collect it at all. `BatchFormSerializer.valid_from` is
`required=False` and defaults to today (`timezone.localdate()`) when omitted,
so the API stays usable without a frontend either way.

## Numbering (§4)

`VoucherPrefix` owns one sequence (`next_sequence`, `sequence_length`).
`services/numbering.allocate()` reserves a contiguous block under
`select_for_update()`, inside the same transaction that creates the voucher
rows — that row lock, not retries, is what makes two batches generated at the
same instant against the same prefix get non-overlapping numbers. Covered by a
real multi-threaded test (`NumberingConcurrencyTests`).

Reference data (departments, voucher types, prefixes) is seeded from the
brief's own examples:

```
python manage.py seed_voucher_portal
```

Departments (HR, Marketing), voucher types (Employee/Marketing/Gift Voucher),
prefixes (EMP, MKT, ADCOOP, 4-digit sequences) — edit via `/admin/` once real
data is available; nothing about them is hard-coded elsewhere.

## Snapshotting

`PortalBatch` copies everything a printed voucher depends on directly onto its
own row at generation time — discount, validity, terms, `prefix_snapshot`,
`sequence_length_snapshot`, and `template_snapshot` (a full copy of the
template's geometry and artwork path). Editing a `VoucherPrefix` label or a
`VoucherTemplate`'s artwork afterwards can never retroactively change a voucher
that's already been printed and handed to someone
(`PreviewAndGenerationTests.test_batch_snapshot_survives_prefix_edit`).

## Template and PDF rendering

`voucher_portal/geometry.py` holds the default field layout, measured directly
from the attached `DiscountCoupon.pdf`'s content stream — a 479.52 × 178pt
coupon on a 594.72 × 792pt page, with every dynamic field positioned in points
from the coupon's top-left corner. `voucher_portal/pdf.py` draws a **fixed,
known set of fields** (discount numeral, cap line, valid-until, restrictions,
barcode, code) at those configurable positions — not a generic layout
interpreter. That's enough for Phase 3's "editable field positions" to be a
geometry change, not new code, as long as new designs stay within this field
set.

Artwork upload rules (`voucher_portal/validators.py`), derived from the
template's proportions:

| Rule | Value |
| --- | --- |
| Required aspect ratio | 2.74 : 1 (± 2%) |
| Width | 1500–4000 px (1987px = the template's native size at 300 DPI) |
| Formats | JPEG, PNG, RGB |
| Max size | 5 MB |

Until a real design is uploaded, every template falls back to
`voucher_portal/assets/default_artwork.png` — the sample artwork from the
attached PDF.

**Uploading artwork from the create form**: the frontend doesn't route through a
separate template-management screen for Phase 1 — the create-batch form has its
own "Voucher artwork" file input that `POST`s straight to `templates/`
(multipart, `name` + `artwork`), gets back a template id, and includes it as
`template` on the batch payload. A brand-new template created this way still
gets the coupon's known field positions automatically (`VoucherTemplate.
field_geometry`'s model default is the same `DEFAULT_FIELD_GEOMETRY`, not an
empty dict) — only the artwork image changes, never where things are drawn.

Watch for this if you touch `VoucherTemplateSerializer`: DRF's `BooleanField`
treats a key that's simply absent from a `multipart/form-data` body as `False`
(`default_empty_html`, simulating an unchecked HTML checkbox) — it does **not**
fall through to the model's own `default=True`. The create form's upload never
sends `is_active`, so without `is_active = serializers.BooleanField(default=
True)` declared explicitly on the serializer, every uploaded template was
silently created inactive and invisible to the batch it was uploaded for
(`ArtworkUploadTests.test_uploaded_template_is_active_without_saying_so` guards
this).

## Preview → draft → approve → generate (§6, §10)

`POST batches/preview/` renders one sample coupon from the submitted form
**without touching the database** (an unsaved, batch-shaped object is enough
for the renderer) and returns the PDF with an `X-Preview-Hash` header — a
SHA-256 of the canonicalised form payload. `POST batches/` requires that hash
back and rejects a stale one, so changing any field after previewing genuinely
invalidates it — enforced server-side, not left to the browser
(`PortalApiWorkflowTests.test_create_with_stale_hash_rejected`).

Creating a batch and actually minting its vouchers are two separate steps,
split by the approval workflow below:

- `POST batches/` (`services/generation.create_draft_batch`) saves the batch
  exactly as submitted — discount, validity, terms, and a snapshot of the
  chosen prefix and template — as `status=draft`. **No numbers are allocated,
  no `PortalVoucher` rows exist yet.**
- `POST batches/{id}/submit/` → `pending_approval`, notifies approvers.
- `POST batches/{id}/approve/` → `approved`, notifies the requester. Self-
  approval is blocked for everyone except Administrators (§10: "should not
  approve their own request unless explicitly permitted" — Administrator is
  that permission).
- `POST batches/{id}/reject/` (reason required) → `rejected`, notifies the
  requester with the reason. A rejected batch can be resubmitted directly
  (`submit` again) rather than needing a true edit-and-resave step — there's
  no field-level edit endpoint yet, so "editing and resubmitting" (§10) is
  simplified to "resubmit the same batch," documented here rather than left
  as a silent gap.
- `POST batches/{id}/generate/` — **only valid from `approved`** — is what
  used to happen automatically in Phase 1: allocates numbers
  (`services/numbering.allocate`, using the batch's own snapshot rather than
  the live prefix, so an edit to the prefix between submission and generation
  can never change what gets printed), bulk-creates `PortalVoucher` rows, and
  starts the background PDF job below.
- `POST batches/{id}/cancel/` — Administrator only, from any non-terminal
  status — logs a `StatusChange` and stops the batch going further.

Every transition is logged to `StatusChange` (who, when, from/to status,
reason) and, where relevant, an in-app `Notification`. See
`services/workflow.py`.

## Background PDF generation and storage

`generate_vouchers` **starts a background thread** that renders every
voucher's individual PDF, uploads each one, assembles the combined print PDF,
and flips `batch.status` from `generating` to `generated` (or `failed`, with
`generation_error` set). Once at least one voucher is issued, `PortalBatch.
refresh_issue_status()` (called after every issue action) moves the batch on
to `partially_issued` / `fully_issued`.

**This thread-based approach is a deliberate Phase 1 simplification, not an
oversight.** There's no task queue on this box — no Redis, no Celery. A
Python thread inside the existing gunicorn worker works for the batch sizes a
POC needs, but it does **not** survive a worker restart mid-job, and a very
large batch will hold CPU inside that worker for the duration. If batch sizes
or reliability requirements grow, this is the first thing to replace with a
real queue (Django-Q or RQ against the existing Postgres avoids adding Redis).
The frontend polls `GET batches/{id}/` every 2.5s while `status=generating`.

Storage (`voucher_portal/storage.py`) is an abstraction, not a hard S3
dependency: if `VOUCHER_PORTAL_S3_BUCKET` (and standard AWS credentials) are
set in the environment, individual and combined PDFs upload to that bucket. If
not, they're written to `MEDIA_ROOT` and served locally. Nothing else in the
code changes either way — callers only ever see a URL back.

```
VOUCHER_PORTAL_S3_BUCKET=my-bucket
AWS_ACCESS_KEY_ID=...
AWS_SECRET_ACCESS_KEY=...
AWS_REGION=ap-south-1   # optional, defaults to ap-south-1
```

**Local fallback in production still needs an nginx location block** —
`MEDIA_ROOT` is served by Django only when `DEBUG=True` (fine for local
testing; not how this project runs in production). Until S3 credentials are
set, add something like this to the existing nginx config fronting
`phloz-fms`:

```nginx
location /media/ {
    alias /opt/phloz/fms/shared/media/;
}
```

`MEDIA_ROOT` defaults to `shared/media` under the app root
(`$MEDIA_ROOT` env var to override), which — like `fms.env` — survives every
deploy, unlike the timestamped release directories.

## Issuing, redemption and cancellation (§8, §9)

- **Manual issue**: `POST vouchers/issue/` with `voucher_ids` and optional
  name/phone/email/reference.
- **Bulk CSV issue**: `POST batches/{id}/issue_bulk/`, a `multipart/form-data`
  upload with a `name,phone,email,reference` header row. Assigns to the oldest
  unissued vouchers in the batch, in order; rejects the whole upload up front
  if there are more valid recipient rows than available vouchers (rather than
  partially issuing and leaving the caller to figure out which recipients
  didn't get one). Malformed rows are collected and returned in `rejected`
  rather than aborting the whole upload.
- **Redeem**: `POST vouchers/{id}/redeem/` — issued → redeemed. No SAP or POS
  integration triggers this; it's a manual "this voucher was used in store"
  action, since third-party integration is explicitly out of scope here.
- **Cancel**: `POST vouchers/{id}/cancel/` (batch or voucher) — Administrator
  only, matching §11's role table, which lists cancel only under
  Administrator.

## Roles and department permissions (§11)

Two independent axes, deliberately **not** built on `iam.Role` —
`services/access.py` explains why (retail voucher staff and fleet-ops staff
are different people in a different org, even when they share a Django
login):

- **Role** (`PortalUserAccess.role`) decides which *actions* a login may
  perform at all: `create`, `approve`, `issue`, `report`, `admin`, `download`.
  The four roles match §11 exactly — Administrator gets everything;
  Requester gets create/issue/download; Approver gets approve/download/report;
  Report Viewer gets report only. See `models.ROLE_ACTIONS`.
- **Department scope** (`PortalUserAccess.departments`, a M2M) decides which
  departments' batches and vouchers a login can see and act on at all.
  Administrator/Report Viewer with nothing assigned see every department (the
  common case for those roles); Requester/Approver with nothing assigned see
  **nothing** — an explicit grant is required, a locked-down default rather
  than an accidentally-open one.

Django staff/superusers get implicit Administrator access with no department
restriction, so the bootstrap `fleetadmin` (or any dev login with
`is_staff=True`) works immediately with no separate grant — see
`get_access()`. Voucher-type-level permission granularity from §11
("Permissions should be assignable by department, voucher type, action") is
**not** implemented — department is the boundary that's actually enforced;
voucher-type scoping was judged not worth the added complexity for this pass
and would be a natural next axis on `PortalUserAccess` if it's ever needed.

Every `voucher-portal/` endpoint requires both the project default
(`IsAuthenticated`) and `HasPortalAccess` (an active grant, or Django
staff/superuser). Action and department checks happen per-view
(`_require`/`_require_department` in `views.py`), not just in the frontend —
§13's "access must be enforced on the server, not only hidden in the
interface."

**Managing access**: `/api/v1/voucher-portal/access/` (Administrator only) —
grant an *existing* Django login a role and department scope; creating the
account itself is `iam`'s or Django admin's job, not this app's. `GET
access/me/` (anyone with portal access) returns the caller's own role,
actions and department scope, so the frontend knows what to show without
duplicating the permission table client-side.

## Notifications (§10)

**In-app only** — there's no SMTP configured on this deployment. `POST
batches/{id}/submit/` notifies every approver/administrator scoped to the
batch's department; `approve`/`reject` notify the batch's requester.
`GET notifications/` (scoped to the caller), `POST notifications/{id}/read/`,
`POST notifications/read-all/`. Wiring in real email later only needs an
email backend and a call from `services/workflow.py`'s `_notify()` — nothing
about the workflow itself changes.

## Reporting and dashboards (§12)

`GET reports/summary/`, `by-department/`, `by-type/`, `trend/`, `batches/`
and `export/` (CSV) — every one scoped to the caller's visible departments in
`services/reports.py`, so a Report Viewer scoped to one department can't see
another's numbers even in an aggregate total.

All six share one filter set (`reports.apply_filters`): `department`,
`voucher_type`, `status`, `from`, `to`. Keeping the filtering in one function
rather than per-view is deliberate — the summary, the breakdowns, the trend
and the CSV can't drift into filtering differently from each other.

`trend/` returns a **dense** monthly series (`?months=` 1–24, default 12):
months with no activity come back as explicit zeros rather than being
omitted, because a chart with gaps where nothing happened reads as missing
data instead of a quiet month.

The dashboard (`/voucher-portal` → Reports) puts one filter row above
everything it scopes, then stat tiles, a multi-series trend line, a grouped
department bar chart, and the by-type and batch-level tables. Both charts are
inline SVG — no chart library — with a hover crosshair/tooltip layer and a
"Show table" twin, so no value is reachable only by hovering.

Chart colours are the first three slots of a validated categorical palette
(`#2a78d6` / `#eb6834` / `#1baf7a`), checked all-pairs against this page's
white card surface: worst CVD ΔE 9.2, worst normal-vision ΔE 24.0. Aqua sits
below 3:1 contrast on white, which is why both charts ship direct value
labels and a table view. Series colour follows the *measure*, not its rank,
so filtering never repaints a series out from under the reader.

## Template library and layout editor (§5 "Configurable templates")

Templates supported multipart CRUD from Phase 1 (the create-form's inline
artwork upload). On top of that:

- **Library screen** — list every template, set which is `is_default`,
  activate/deactivate old designs.
- **Layout editor** — drag any field to reposition it, or type exact
  coordinates. The canvas is the coupon's own coordinate space (points from
  the top-left corner) scaled to fit, so what's dragged is what prints.
  Alongside position it edits font size, colour, line spacing, box
  width/height, and the qualifier's static text.
- `GET templates/field-catalogue/` is the single source of truth for which
  fields exist: `pdf.py` draws exactly these keys, `validate_field_geometry`
  accepts exactly these keys, and the editor offers exactly these keys. A
  field the renderer can't draw therefore can't be introduced by editing
  geometry.
- `GET|POST templates/{id}/preview/` renders a sample coupon — POST unsaved
  geometry to see an edit *before* committing it, without creating a batch
  and burning real voucher numbers.
- `POST templates/{id}/reset-geometry/` restores the measured default layout.

`validators.validate_field_geometry` is what stands between a careless drag
and a print run of unreadable vouchers: it rejects unknown field keys,
duplicates, non-numeric values, negatives, and any position outside the
coupon's own dimensions.

One gotcha worth knowing if you extend the template endpoints: they accept
`MultiPartParser`, `FormParser` **and** `JSONParser`. Multipart carries the
artwork upload; `field_geometry` is a nested structure a form encoder can't
represent, so dropping `JSONParser` makes every geometry save fail with a 415.

## What's still not included

SAP/external-system integration (explicitly out of scope for this pass),
Word-based template upload (the brief itself treats DOCX as a bigger,
separate future item, and image-based artwork is what actually drives the
coupon design today), real SMTP delivery, and voucher-type-level permission
scoping (department is the boundary that's enforced; see "Roles and
department permissions").

## Decisions made building this

- **D1 — Portal vs public desk**: new authenticated area; `/vouchers` is
  untouched and keeps running as the public till page.
- **D2 — Bulk output**: both an individual PDF per voucher (for digital
  delivery, stored on S3 when configured) and one combined multi-page PDF (one
  coupon per page, for print).
- **D4 — Max discount cap**: always optional, never enforced as required, even
  in the form.
- **Email notifications**: in-app only for this pass — no SMTP is configured
  on this deployment; real email is a config change away, not a rebuild.
- **Additional template formats**: a multi-template library with image-based
  artwork, not Word/DOCX upload — the brief itself treats DOCX upload as a
  bigger, separate future item.
- **Reference data**: seeded from the brief's own examples
  (`seed_voucher_portal`); edit via `/admin/` or the Team access screen once
  real department/type/prefix/user data is available.

## Tests

`python manage.py test voucher_portal` — 80 tests: numbering (including a real
concurrent-allocation test across 8 threads), discount validation, the
preview-hash invalidation flow, the full draft → submit → approve → generate
→ issue → redeem workflow (both via `services/workflow.py` directly and
through the HTTP API), self-approval blocking, role and department-scope
enforcement, notifications, reporting (summary, breakdowns, the dense monthly
trend, batch-level rows, every filter, and department-scoped visibility on
each), the template library, the geometry editor (valid moves, out-of-bounds
and unknown-key rejection, catalogue/renderer agreement, unsaved-geometry
preview, reset-to-default, and non-admin lockout), team-access management,
and artwork upload (aspect ratio/size rejection, and the `is_active`
multipart regression noted above).
