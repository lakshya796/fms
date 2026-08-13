# Gift Voucher Desk (MAIR retail)

A standalone, publicly accessible desk for printing and issuing MAIR retail gift vouchers.
It lives in this repo purely for shared deployment infrastructure — it is not part of the
fleet/transport domain, doesn't reference `fleet`, `iam` or `accounting`, and requires no login.

- Backend: Django app `vouchers` (`backend/vouchers/`), routed at `/api/v1/vouchers/`.
- Frontend: static page `app/vouchers/page.tsx`, served at `/vouchers`.

## Why public

The requirement was explicit: store staff need to generate and issue vouchers from a shared
till-side page with no sign-in step. Every endpoint under `vouchers/` therefore uses DRF's
`AllowAny` permission instead of the project default (`IsAuthenticated`). This is a deliberate
trade-off, not an oversight — see **Risk** below.

## Model

- `VoucherBatch` — one print run: a numeric series (prefix + zero-padded range), a denomination,
  a validity window, and free-text `created_by` (who filled in the form; there's no login to
  attribute it to a user).
- `GiftVoucher` — one voucher per number in the batch's range. `status` is `unassigned` or
  `issued`; `display_status` additionally reports `expired` once `valid_to` has passed,
  regardless of the stored status.

## Creating a batch

`POST /api/v1/vouchers/batches/` with `start`, `end`, `value`, `currency`, `valid_from`,
`valid_to`, optional `created_by`. `start`/`end` are voucher numbers like `GV-2026-000101` —
the numeric suffix must have matching width and the same prefix on both ends. Every number in
the inclusive range is created in one transaction; a range that collides with existing vouchers
is rejected outright rather than silently skipping duplicates.

A batch is capped at `MAX_BATCH_SIZE = 1000` vouchers (`backend/vouchers/models.py`) — the one
built-in guardrail against a public, unauthenticated endpoint minting an unbounded number of
monetary vouchers in a single request.

## Issuing a voucher

`POST /api/v1/vouchers/vouchers/{id}/issue/` with an optional `phone_number`. Rejects vouchers
that are already issued or whose validity window has passed. Phone number is deliberately
optional — a voucher can be issued to walk-in cash customers with nothing recorded against it.

## Listing and filtering

`GET /api/v1/vouchers/vouchers/` supports `?status=unassigned|issued|expired` (matching the
same split the summary cards use — `expired` overrides the stored status regardless of whether
it was issued), `?search=` (voucher number or phone, partial match), and `?batch=`.

`GET /api/v1/vouchers/vouchers/summary/` returns the counts shown on the stat cards.

`GET /api/v1/vouchers/lookup/{number}/` looks a voucher up by its printed number — for a till
that only has the physical voucher in hand.

## PDF

`GET /api/v1/vouchers/vouchers/{id}/pdf/` renders the voucher as a PDF (`backend/vouchers/pdf.py`,
via `reportlab`) matching the MAIR template: logo, "GIFT VOUCHER" heading, a bordered
label/value table (number, phone, value, issue date, validity), a Code128 barcode of the
voucher number, and the terms line. The logo is stored at
`backend/vouchers/assets/mair_logo.png` — replace that file to change the mark on every
printed voucher.

## Frontend

`app/vouchers/page.tsx` is a self-contained page — it does not use the authenticated console's
sidebar shell, and never touches `sessionStorage`/the `fms_token` flow the rest of the console
relies on. It reuses `fmsRequest` from `app/lib/fms-api.ts`, which already tolerates anonymous
use. Styling is scoped under `.voucher-*` classes in `app/globals.css`, which derive the whole
MAIR palette from a single `--mair-green` token rather than the fleet console's own theme.

## Risk

This page is intentionally public and write-capable: anyone with the link can mint vouchers with
real monetary value and issue them. The only built-in defence is the per-batch size cap. If this
needs tightening later, a lightweight shared passcode gate (checked client-side, or a shared
header validated server-side) would be a small addition — but that was not requested, and the
page is public by explicit design.
