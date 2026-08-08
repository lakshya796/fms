# Voucher Portal (ADCOOP) — Phase 1

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

## Preview → confirm (§6)

`POST batches/preview/` renders one sample coupon from the submitted form
**without touching the database** (an unsaved, batch-shaped object is enough
for the renderer) and returns the PDF with an `X-Preview-Hash` header — a
SHA-256 of the canonicalised form payload. `POST batches/` requires that hash
back and rejects a stale one, so changing any field after previewing genuinely
invalidates it — enforced server-side, not left to the browser
(`PortalApiTests.test_create_with_stale_hash_rejected`).

## Generation and storage

`POST batches/` allocates numbers, bulk-creates `PortalVoucher` rows
(`status=generated`) and sets `batch.status=generating`, then **starts a
background thread** that renders every voucher's individual PDF, uploads each
one, assembles the combined print PDF, and flips `batch.status=generated` (or
`failed`, with `generation_error` set).

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

## Issuing (§8)

Two paths, both on `PortalVoucher.issue()`:

- **Manual**: `POST vouchers/issue/` with `voucher_ids` and optional
  name/phone/email/reference.
- **Bulk CSV**: `POST batches/{id}/issue_bulk/`, a `multipart/form-data` upload
  with a `name,phone,email,reference` header row. Assigns to the oldest
  unissued vouchers in the batch, in order; rejects the whole upload up front
  if there are more valid recipient rows than available vouchers (rather than
  partially issuing and leaving the caller to figure out which recipients
  didn't get one). Malformed rows are collected and returned in `rejected`
  rather than aborting the whole upload.

## What Phase 1 deliberately does not include

Everything in the brief's Phase 2 and 3: approval workflow, email
notifications, department-scoped permissions beyond "logged in," reporting
exports, SAP integration, multi-template management UI, Word-based template
upload. `iam`'s existing `Role`/`allows()`/`AuditLog` machinery is the
intended foundation for the Phase 2 permission and audit work — see
`backend/iam/models.py` — rather than a parallel system built inside
`voucher_portal`.

## Decisions made building this (see the implementation plan for the full list)

- **D1 — Portal vs public desk**: new authenticated area; `/vouchers` is
  untouched and keeps running as the public till page.
- **D2 — Bulk output**: both an individual PDF per voucher (for digital
  delivery, stored on S3 when configured) and one combined multi-page PDF (one
  coupon per page, for print).
- **D4 — Max discount cap**: always optional, never enforced as required, even
  in the form.
- **Reference data**: seeded from the brief's own examples
  (`seed_voucher_portal`); edit via `/admin/` once real department/type/prefix
  data is available.

## Tests

`python manage.py test voucher_portal` — 19 tests: numbering (including a real
concurrent-allocation test across 8 threads), discount validation, the
preview-hash invalidation flow, batch generation and its snapshot guarantee,
manual and CSV issuing, and API-level auth enforcement.
