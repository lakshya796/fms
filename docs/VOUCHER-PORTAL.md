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
prefixes (EMP, MKT, ADCOOP, 4-digit sequences) — after that they are managed
from the portal's own Setup screen (or `/admin/`); nothing about them is
hard-coded elsewhere.

## Snapshotting

`PortalBatch` copies everything a printed voucher depends on directly onto its
own row at generation time — discount, validity, terms, `prefix_snapshot`,
`sequence_length_snapshot`, and `template_snapshot` (a full copy of the
template's geometry and artwork path). Editing a `VoucherPrefix` label or a
`VoucherTemplate`'s artwork afterwards can never retroactively change a voucher
that's already been printed and handed to someone
(`PreviewAndGenerationTests.test_batch_snapshot_survives_prefix_edit`).

## Template and PDF rendering

A template's `field_geometry` is a **layout document** (`voucher_portal/
geometry.py`). Version 3 — what the designer writes — is a free-form,
ordered list of elements the user added, positioned in points from the card's
top-left corner:

```jsonc
{
  "version": 3,
  "coupon": {"w": 479.52, "h": 178},   // card size (also on the model)
  "background": "#FFFFFF",
  "artwork": {"x": 0, "y": 0, "w": 479.52, "h": 178},
  "elements": [                         // array order == paint order
    {"id": "barcode", "type": "barcode", "x": 289.5, "y": 128, "w": 150, "h": 24},
    {"id": "t1", "type": "text",  "text": "EID GIFT", "x": 16, "y": 24, "size": 22, ...},
    {"id": "f1", "type": "field", "source": "valid_to", "prefix": "Valid until ", ...}
  ]
}
```

Element types are `text` (wording the user types), `field` (a voucher/batch
variable resolved at print time), `box`, `line` and `barcode`.
`voucher_portal/pdf.py` is a **generic interpreter** — it draws whatever is in
`elements` — so a new design needs no renderer change.

**Nothing is prefilled.** A new template is an empty card carrying only the
mandatory barcode (`geometry.blank_geometry()`, which is `VoucherTemplate.
field_geometry`'s model default). The old fixed ADCOOP field set is still
available, but only as an opt-in *starter* in the designer that drops those
fields in as ordinary, editable elements.

**The barcode is mandatory and unique per voucher.** It encodes
`PortalVoucher.number`, which is `unique=True` and allocated under a row lock
(`services/numbering.py`), so two vouchers can never carry the same barcode.
`validate_field_geometry` refuses to save a layout with no visible barcode, or
one smaller than 40 × 8pt (too small to scan reliably).

**Versions 1 and 2** (the old fixed `fields` catalogue, plus `text_layers`)
are still readable: `geometry.to_elements()` converts them to the version 3
shape, preserving position, size, font, colour and paint order. That conversion
runs on the way into the renderer, so a batch generated before the designer
existed — which carries its own immutable version 2 `template_snapshot` —
keeps printing exactly as it did. It also runs on the way to the browser as the
serializer's read-only `layout` field, so an old template opens in the designer
as editable elements. Nothing is rewritten on disk until the user saves.

Card size is per template (`coupon_width` / `coupon_height`, with presets in
the designer). The page a card is printed on grows to fit if a design is
bigger than the stored page size, and the designer's own PDF proof is rendered
trimmed to the card itself (`build_voucher_pdf(..., fit_page=True)`).

Artwork upload rules (`voucher_portal/validators.py`):

| Rule | Value |
| --- | --- |
| Required aspect ratio | the template's own card ratio (± 2%), 2.74 : 1 by default |
| Width | 1500–4000 px (1987px = the ADCOOP coupon's native size at 300 DPI) |
| Formats | JPEG, PNG, RGB |
| Max size | 5 MB |

Artwork is optional — a card with none prints on its background colour. It does
**not** fall back to any bundled design: "I uploaded nothing" must not mean
"you get someone else's branding".

**Read artwork back from `artwork_path`, never `artwork`.** The `artwork` field
is the `ImageField`'s own media URL, and *nothing serves `/media/` in
production*: `urls.py` registers those routes through
`django.conf.urls.static.static()`, which returns an empty list when `DEBUG` is
false, and the nginx config only proxies the API's own prefix. That URL is
therefore a 404 the moment it leaves a dev machine — it uploads fine and then
can't be fetched. `GET templates/{id}/artwork/` streams the file through the
authenticated API instead (same reasoning as the PDF downloads), and
`artwork_path` on the serializer points at it. Because that needs an auth
header, the browser fetches artwork as a blob and hands the `<img>` an object
URL, exactly as it already does for PDFs.

Uploads also need `MEDIA_ROOT` pointing outside the release directory —
`deploy/ec2/deploy-fms.sh` writes `MEDIA_ROOT=/opt/phloz/fms/shared/media` into
the environment file and back-fills it on existing installs. Without it the
settings default is `BASE_DIR/media`, i.e. inside the current release, so every
deploy silently orphaned whatever had been uploaded.

**Uploading artwork**: the designer's "new card design" form and its *Replace
artwork* control both go straight to `templates/` (multipart, `name` +
`artwork`), and a template created that way starts empty apart from the
barcode, like any other. The create-batch form no longer has its own artwork
input — it picks a design and offers shortcuts into the designer instead (see
below), which is the same upload one screen along.

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

**Downloads go through the authenticated API, not a media URL.** `GET
batches/{id}/download/` and `GET vouchers/{id}/download/` stream the stored
PDF back through `storage.open_file`, subject to the same role and
department checks as everything else. Two reasons this isn't just a link:
these PDFs carry recipient data and §13 requires them to be reachable only
by authorised users, and the stored media URL is *host-relative* - a bare
`<a href="/media/...">` on the console resolves against the console's own
origin, hits Amplify's catch-all rewrite, and lands the user on the login
screen instead of a PDF. The frontend fetches them with `fmsRequestRaw` and
saves the blob, so the auth token travels with the request.

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

## Card designer (§5 "Configurable templates")

The template screen is a card designer, not a field-nudger. A design starts
empty and the user builds it:

- **From the create-batch form** — the form carries a card-design picker
  (miniatures, not a dropdown of names), defaulting to the `is_default`
  template so the plain "just make me a batch" path needs no interaction with
  it. Next to it sit two shortcuts: *Edit this design* and *New design*. Both
  hand the user to the designer and hand them back to the same half-filled
  form afterwards, with the design they just worked on selected and the
  preview invalidated. Because the design can change between preview and save,
  `payload_hash` now covers the template's `field_geometry` as well as its id —
  restyling a card after previewing invalidates the preview server-side, not
  just in the browser.
- **Library screen** — every design, each shown as a live miniature of its
  actual layout (the same renderer the designer canvas uses, so a thumbnail
  can't disagree with the editor). Create a new card with a name, a size
  preset and optional artwork, and land straight in the designer.
- **Designer** — add text, voucher fields, boxes, lines and barcodes from a
  palette; drag to move, drag a corner to resize, nudge with the arrow keys
  (Shift = 10pt, Alt = finer than the 0.5pt snap); reorder, hide, duplicate
  and delete via the layers list; align to any card edge or centre; undo/redo
  (Ctrl+Z / Ctrl+Shift+Z). The canvas is the card's own coordinate space
  scaled to fit and every element is drawn the way it prints — same fonts,
  sizes, colours and sample values as the PDF — so what you drag really is
  what prints.
- **Live PDF proof** — the server-rendered card, refreshed ~0.9s after you
  stop editing, sitting under the canvas. It is the source of truth, and it
  surfaces the same validation the save will run, so a design can't be a
  surprise at print time.
- **Per-element controls** — wording or bound variable (with `before`/`after`
  text around it), position, size, font, colour, alignment, line spacing, line
  limit, fill/opacity/border/radius for boxes, bar colour and whether the
  number prints under the barcode. Any element can be set to *only show when*
  a chosen variable has a value (`hide_if_empty`), which is how a
  "Restrictions:" label disappears on a batch with no restrictions.
- **Starters** — "Blank card" and "ADCOOP coupon", both of which drop in as
  ordinary editable elements. Opt-in only.
- `GET templates/field-catalogue/` is the single source of truth for what the
  designer may offer: element types, bindable variables (with the sample
  values the canvas and the proof both draw), fonts, alignments, card-size
  presets, the blank document and the starters. `pdf.py` draws exactly these,
  `validate_field_geometry` accepts exactly these — so the browser can't offer
  a font or a variable the renderer will refuse.
- `GET|POST templates/{id}/preview/` renders a sample card — POST unsaved
  geometry to see an edit *before* committing it, without creating a batch
  and burning real voucher numbers.
- `POST templates/{id}/reset-geometry/` empties the card back to just the
  barcode. Administrator-only, unlike the rest of the designer.

**Who may design.** Designing a card is part of creating a batch, and
requesters are who use that form, so anyone with `create` may add a template
and change its design (`POST`/`PATCH` on `templates/`). What stays
administrator-only is which design *everyone else* gets — `is_default` and
`is_active` — plus deleting a template and resetting a layout. A role without
`create` (Report Viewer, Approver) can look but not touch.

`validators.validate_field_geometry` is what stands between a careless drag
and a print run of unreadable vouchers. It rejects: unknown element types,
duplicate ids, unknown variables, fonts reportlab doesn't have, malformed
colours, non-numeric values, negatives, any position outside the card's own
dimensions, empty text elements, a missing or hidden barcode, and a barcode
too small to scan. The font and colour checks matter more than they look:
both raise inside the *background* PDF-generation thread, which means a batch
that fails minutes after a layout that looked fine on screen.

One gotcha worth knowing if you extend the template endpoints: they accept
`MultiPartParser`, `FormParser` **and** `JSONParser`. Multipart carries the
artwork upload; `field_geometry` is a nested structure a form encoder can't
represent, so dropping `JSONParser` makes every geometry save fail with a 415.

## Reference data from the UI (Setup screen)

Departments, voucher types and numbering prefixes used to be addable only
through the database or the Django admin, which made "we need a new voucher
type" a developer's errand. The **Setup** screen (administrator-only, matching
what the API already enforced through `AdminWriteMixin`) adds and
activates/deactivates all three, in the order they depend on each other — a
type belongs to a department, a prefix numbers one type. Each write refreshes
the create-batch form's own copy, so a type added here is selectable there
without a reload.

Prefixes show `next_sequence` as the number the next voucher will take. It is
deliberately not editable from this screen: winding it back re-issues codes
that are already printed, and `PortalVoucher.number` is unique.

## Every batch field has a placeholder

Everything the create form collects can be placed on a card, so no value is
collected from a requester with nowhere to print. The mapping is pinned by
`BatchFieldCoverageTests`: add a field to `BatchFormSerializer` and the test
fails until a placeholder exists for it.

| Create form | Placeholder |
| --- | --- |
| Voucher name | `batch_name` |
| Quantity | `quantity` |
| Department / Voucher type | `department` / `voucher_type` |
| Prefix | `prefix` |
| Currency | `currency` |
| Description | `description` |
| Discount type / value | `discount_type`, `discount_value`, `discount_numeral`, `discount_unit` |
| Maximum discount | `discount_cap` (formatted line), `max_discount_value` (bare amount) |
| Valid from / until | `valid_from` / `valid_to` |
| Restrictions / Terms | `restrictions` / `terms` |

Placing one is the designer's *Voucher field* palette entry. The create form
closes the loop from the other side: under the card picker it lists what the
chosen design prints, and warns when a customer-facing value has been typed
with nowhere to go ("Not on this card: Terms and conditions"), with a link
straight into the designer. It stays quiet about operational values like
quantity and prefix — they are placeable, but flagging them on every batch
would be noise.

## Which frontends may call the API

The portal is a browser app on a different origin from the API, so
`CORS_ALLOWED_ORIGINS` decides whether it can talk to it at all. An origin
that isn't listed fails as a CORS error in the browser with a perfectly
healthy API behind it and nothing in its log to explain the failure.

Amplify complicates the list: every branch is served from its own subdomain of
one app id (`main.<app>.amplifyapp.com`, `my-branch.<app>.amplifyapp.com`), so
a fixed list stops working each time a new branch is deployed.
`CORS_ALLOWED_ORIGIN_REGEXES` takes comma-separated patterns for that case.
Anchor them at both ends and scope them to one app id — unanchored,
`^https://[a-z0-9-]+[.]<app>[.]amplifyapp[.]com` also matches
`https://x.<app>.amplifyapp.com.attacker.example`. `CorsTests` covers the
allowed, the unlisted and the lookalike cases.

## Django admin

`https://api-test.phloz.app/fms/admin/` — nginx routes `/fms/` to this app, so
the admin sits under the same prefix as the API.

**Getting in requires `is_staff`.** That is a different thing from portal
access: a Voucher Portal login (a `PortalUserAccess` row) does not let you into
the admin, and Django staff status is not something the portal grants. To make
an existing FMS user a Django admin, an existing superuser flips
`is_staff`/`is_superuser` on them under *Authentication and Authorisation →
Users*, or from the box:

```bash
cd /opt/phloz/fms/current && set -a && . /opt/phloz/fms/shared/fms.env && set +a
/opt/phloz/fms/venv/bin/python manage.py createsuperuser          # a new one
/opt/phloz/fms/venv/bin/python manage.py shell -c "
from django.contrib.auth.models import User
u = User.objects.get(username='someone'); u.is_staff = u.is_superuser = True; u.save()"
```

Note the knock-on: `services/access.py` gives any Django staff user or
superuser **implicit portal Administrator rights**, so making someone a Django
admin also hands them every department's vouchers. That is deliberate — it is
what keeps the bootstrap login working before any grant exists — but it means
`is_staff` is not a small thing to hand out.

`DJANGO_SCRIPT_NAME=/fms` has to be set in the environment (the deploy script
writes it, and back-fills it on installs that predate it). nginx strips the
prefix before proxying, so without it Django generates every link, form action
and stylesheet URL against the domain root — a *different* application on that
host. The symptom is an unstyled admin whose login form posts into the void.
`STATIC_URL` follows the same prefix, and WhiteNoise strips it back off when
matching, so no nginx change is needed.

**What the admin is for**, given the portal has its own screens: seeding and
correcting reference data (departments, types, prefixes), granting portal
access, repairing a card layout by hand, and reading the audit trail. Two
things it deliberately won't do:

- **Batches and vouchers can't be created here.** They are only correct when
  `services/generation.py` builds them — numbers allocated under a row lock,
  prefix and template snapshotted onto the row. A row typed in by hand would
  fail at print time instead of at save time.
- **`StatusChange` is read-only.** An audit trail you can edit isn't one.

Editing a template's `field_geometry` in the admin is validated by
`VoucherTemplate.clean()` — the same rules the designer is held to, since the
admin doesn't pass through the API serializer.

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

`python manage.py test voucher_portal` — 132 tests: numbering (including a real
concurrent-allocation test across 8 threads), discount validation, the
preview-hash invalidation flow, the full draft → submit → approve → generate
→ issue → redeem workflow (both via `services/workflow.py` directly and
through the HTTP API), self-approval blocking, role and department-scope
enforcement, notifications, reporting (summary, breakdowns, the dense monthly
trend, batch-level rows, every filter, and department-scoped visibility on
each), the template library, the card designer (a new template starting empty,
user-added elements saving and rendering, out-of-bounds/unknown-type/unknown-
variable/unknown-font/bad-colour/duplicate-id rejection, the mandatory barcode
being un-deletable and un-hideable, catalogue/renderer agreement, unsaved-
geometry preview, reset-to-empty, card resizing, who may design a card versus
who may change which design everyone else gets, and non-admin lockout), the
Django admin (every changelist and change form opening, the audit trail being
read-only, batches and vouchers not being hand-creatable, and a broken layout
being refused at the model), card
rendering (every catalogue variable resolving for a real voucher, per-voucher
barcode uniqueness, hidden and `hide_if_empty` elements, prefix/suffix, and
version 1/2 layouts still printing), team-access management,
and artwork upload (aspect ratio/size rejection, serving artwork back through
the API, a clear error when the file has gone missing, and the `is_active`
multipart regression noted above), and authenticated PDF downloads
(streaming, auth, department scope, role gating, and a clear error when a
stored file has gone missing).
