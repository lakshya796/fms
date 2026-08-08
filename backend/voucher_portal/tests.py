import threading
import time
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase, TransactionTestCase
from django.utils import timezone
from rest_framework.test import APIClient

from .geometry import DEFAULT_FIELD_GEOMETRY
from .models import Department, PortalBatch, PortalVoucher, VoucherPrefix, VoucherTemplate, VoucherType
from .services.generation import generate_batch, payload_hash, render_preview
from .services.numbering import NumberingError, allocate


def dates(days_from=0, days_to=365):
    today = timezone.localdate()
    return today + timedelta(days=days_from), today + timedelta(days=days_to)


def make_reference_data():
    dept = Department.objects.create(code="HR", name="HR")
    vtype = VoucherType.objects.create(code="EMP", name="Employee Voucher", department=dept)
    prefix = VoucherPrefix.objects.create(prefix="EMP", label="Employee", department=dept, voucher_type=vtype,
                                          sequence_length=4)
    template = VoucherTemplate.objects.create(name="Default", field_geometry=DEFAULT_FIELD_GEOMETRY, is_default=True)
    return dept, vtype, prefix, template


class NumberingTests(TestCase):
    def setUp(self):
        self.dept, self.vtype, self.prefix, self.template = make_reference_data()

    def test_allocates_sequential_codes(self):
        numbers, prefix_str, width = allocate(self.prefix.id, 5)
        self.assertEqual(numbers, ["EMP0001", "EMP0002", "EMP0003", "EMP0004", "EMP0005"])
        self.prefix.refresh_from_db()
        self.assertEqual(self.prefix.next_sequence, 6)

    def test_continues_from_last_sequence(self):
        allocate(self.prefix.id, 3)
        numbers, _, _ = allocate(self.prefix.id, 2)
        self.assertEqual(numbers, ["EMP0004", "EMP0005"])

    def test_inactive_prefix_rejected(self):
        self.prefix.is_active = False
        self.prefix.save()
        with self.assertRaises(NumberingError):
            allocate(self.prefix.id, 1)


class NumberingConcurrencyTests(TransactionTestCase):
    """Fires allocate() from several threads at once against one prefix and
    checks every returned code is unique - the row lock is what §4 requires.

    SQLite (dev/test only - see settings.py) serialises writes at the table
    level rather than the row level Postgres uses in production, so a burst of
    writer threads legitimately has to wait its turn and can still hit
    "database is locked" even with a generous busy timeout. Retrying on that
    specific error re-tests the actual property this test cares about - no
    duplicate numbers - without that SQLite-only limitation masking it."""

    def test_concurrent_allocation_has_no_duplicates(self):
        from django.db import close_old_connections
        from django.db.utils import OperationalError

        dept = Department.objects.create(code="HR", name="HR")
        vtype = VoucherType.objects.create(code="EMP", name="Employee Voucher", department=dept)
        prefix = VoucherPrefix.objects.create(prefix="EMP", label="Employee", department=dept, voucher_type=vtype,
                                              sequence_length=4)

        results = []
        errors = []

        def worker():
            for attempt in range(10):
                try:
                    numbers, _, _ = allocate(prefix.id, 10)
                    results.append(numbers)
                    return
                except OperationalError as error:
                    if "locked" not in str(error) or attempt == 9:
                        errors.append(error)
                        return
                    time.sleep(0.05 * (attempt + 1))
                except Exception as error:  # pragma: no cover - surfaced via assertion below
                    errors.append(error)
                    return
                finally:
                    close_old_connections()

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [])
        all_numbers = [n for batch in results for n in batch]
        self.assertEqual(len(all_numbers), len(set(all_numbers)), "duplicate voucher numbers allocated concurrently")
        self.assertEqual(len(all_numbers), 80)


class DiscountValidationTests(TestCase):
    def setUp(self):
        self.dept, self.vtype, self.prefix, self.template = make_reference_data()

    def test_percentage_batch_display_value(self):
        valid_from, valid_to = dates()
        batch = PortalBatch(name="Test", department=self.dept, voucher_type=self.vtype, quantity=1,
                            discount_type="percentage", percentage_value=Decimal("50"), max_discount_value=Decimal("50"),
                            currency="AED", valid_from=valid_from, valid_to=valid_to, prefix=self.prefix,
                            prefix_snapshot="EMP", sequence_length_snapshot=4, template=self.template)
        batch.full_clean(exclude=["template_snapshot"])
        self.assertEqual(batch.display_value, "50% OFF (up to AED 50.00)")

    def test_fixed_batch_display_value(self):
        valid_from, valid_to = dates()
        batch = PortalBatch(name="Test", department=self.dept, voucher_type=self.vtype, quantity=1,
                            discount_type="fixed", fixed_value=Decimal("500"), currency="AED",
                            valid_from=valid_from, valid_to=valid_to, prefix=self.prefix, prefix_snapshot="EMP",
                            sequence_length_snapshot=4, template=self.template)
        batch.full_clean(exclude=["template_snapshot"])
        self.assertEqual(batch.display_value, "AED 500.00 OFF")

    def test_percentage_over_100_rejected(self):
        valid_from, valid_to = dates()
        batch = PortalBatch(name="Test", department=self.dept, voucher_type=self.vtype, quantity=1,
                            discount_type="percentage", percentage_value=Decimal("150"), currency="AED",
                            valid_from=valid_from, valid_to=valid_to, prefix=self.prefix, prefix_snapshot="EMP",
                            sequence_length_snapshot=4, template=self.template)
        with self.assertRaises(Exception):
            batch.full_clean(exclude=["template_snapshot"])


class PreviewAndGenerationTests(TestCase):
    def setUp(self):
        self.dept, self.vtype, self.prefix, self.template = make_reference_data()
        self.user = User.objects.create_user("tester", password="x")

    def _form(self, **overrides):
        valid_from, valid_to = dates()
        data = {
            "name": "Diwali gift vouchers", "department": self.dept, "voucher_type": self.vtype,
            "description": "", "quantity": 5, "discount_type": "fixed", "fixed_value": Decimal("100"),
            "currency": "AED", "valid_from": valid_from, "valid_to": valid_to, "restrictions": "",
            "terms": "No cash value", "prefix": self.prefix, "template": self.template,
        }
        data.update(overrides)
        return data

    def test_preview_renders_pdf_without_persisting(self):
        pdf_bytes = render_preview(self._form())
        self.assertTrue(pdf_bytes.startswith(b"%PDF"))
        self.assertEqual(PortalBatch.objects.count(), 0)
        self.assertEqual(PortalVoucher.objects.count(), 0)

    def test_hash_changes_when_form_changes(self):
        base = payload_hash(self._form())
        changed = payload_hash(self._form(quantity=6))
        self.assertNotEqual(base, changed)

    def test_hash_stable_for_identical_payload(self):
        self.assertEqual(payload_hash(self._form()), payload_hash(self._form()))

    def test_generate_batch_creates_vouchers_and_advances_sequence(self):
        batch = generate_batch(self._form(), self.user)
        self.assertEqual(batch.vouchers.count(), 5)
        self.assertEqual(batch.status, "generating")
        self.prefix.refresh_from_db()
        self.assertEqual(self.prefix.next_sequence, 6)
        numbers = set(batch.vouchers.values_list("number", flat=True))
        self.assertEqual(numbers, {"EMP0001", "EMP0002", "EMP0003", "EMP0004", "EMP0005"})

    def test_batch_snapshot_survives_prefix_edit(self):
        batch = generate_batch(self._form(), self.user)
        self.prefix.label = "Renamed"
        self.prefix.sequence_length = 6
        self.prefix.save()
        batch.refresh_from_db()
        self.assertEqual(batch.prefix_snapshot, "EMP")
        self.assertEqual(batch.sequence_length_snapshot, 4)


class PortalApiTests(TestCase):
    def setUp(self):
        self.dept, self.vtype, self.prefix, self.template = make_reference_data()
        self.user = User.objects.create_user("tester", password="x")
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def _payload(self, **overrides):
        valid_from, valid_to = dates()
        data = {
            "name": "Diwali gift vouchers", "department": self.dept.id, "voucher_type": self.vtype.id,
            "quantity": 3, "discount_type": "percentage", "percentage_value": "20", "max_discount_value": "50",
            "currency": "AED", "valid_from": valid_from.isoformat(), "valid_to": valid_to.isoformat(),
            "terms": "No cash value", "prefix": self.prefix.id,
        }
        data.update(overrides)
        return data

    def test_valid_from_defaults_to_today_when_omitted(self):
        """The create form no longer collects a start date - the API must still work
        when the caller sends nothing for it."""
        payload = self._payload()
        del payload["valid_from"]
        preview = self.client.post("/api/v1/voucher-portal/batches/preview/", payload, format="json")
        self.assertEqual(preview.status_code, 200)
        response = self.client.post("/api/v1/voucher-portal/batches/",
                                    {**payload, "preview_hash": preview["X-Preview-Hash"]}, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        batch = PortalBatch.objects.get(pk=response.data["id"])
        self.assertEqual(batch.valid_from, timezone.localdate())

    def test_endpoints_require_authentication(self):
        anon = APIClient()
        response = anon.get("/api/v1/voucher-portal/batches/")
        self.assertEqual(response.status_code, 401)

    def test_preview_endpoint_returns_pdf_and_hash_header(self):
        response = self.client.post("/api/v1/voucher-portal/batches/preview/", self._payload(), format="json")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn("X-Preview-Hash", response)

    def test_create_without_preview_hash_rejected(self):
        response = self.client.post("/api/v1/voucher-portal/batches/", self._payload(), format="json")
        self.assertEqual(response.status_code, 400)

    def test_create_with_stale_hash_rejected(self):
        preview = self.client.post("/api/v1/voucher-portal/batches/preview/", self._payload(), format="json")
        stale_hash = preview["X-Preview-Hash"]
        response = self.client.post("/api/v1/voucher-portal/batches/",
                                    {**self._payload(quantity=9), "preview_hash": stale_hash}, format="json")
        self.assertEqual(response.status_code, 400)

    def test_create_with_matching_hash_succeeds(self):
        payload = self._payload()
        preview = self.client.post("/api/v1/voucher-portal/batches/preview/", payload, format="json")
        response = self.client.post("/api/v1/voucher-portal/batches/",
                                    {**payload, "preview_hash": preview["X-Preview-Hash"]}, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["quantity"], 3)

    def test_manual_issue_and_bulk_csv_issue(self):
        payload = self._payload(quantity=5)
        preview = self.client.post("/api/v1/voucher-portal/batches/preview/", payload, format="json")
        created = self.client.post("/api/v1/voucher-portal/batches/",
                                   {**payload, "preview_hash": preview["X-Preview-Hash"]}, format="json").data
        batch = PortalBatch.objects.get(pk=created["id"])
        voucher_ids = list(batch.vouchers.values_list("id", flat=True))

        manual = self.client.post("/api/v1/voucher-portal/vouchers/issue/",
                                  {"voucher_ids": [voucher_ids[0]], "phone": "+971500000000"}, format="json")
        self.assertEqual(manual.status_code, 200)
        self.assertEqual(PortalVoucher.objects.get(pk=voucher_ids[0]).status, "issued")

        csv_content = "name,phone,email,reference\nAli,+9715,,\nSara,+9716,,\n"
        from django.core.files.uploadedfile import SimpleUploadedFile
        upload = SimpleUploadedFile("recipients.csv", csv_content.encode(), content_type="text/csv")
        bulk = self.client.post(f"/api/v1/voucher-portal/batches/{batch.id}/issue_bulk/", {"file": upload}, format="multipart")
        self.assertEqual(bulk.status_code, 200, bulk.data)
        self.assertEqual(bulk.data["assigned"], 2)
        self.assertEqual(bulk.data["remaining_available"], 2)

    def test_bulk_csv_rejects_more_recipients_than_available_vouchers(self):
        payload = self._payload(quantity=1)
        preview = self.client.post("/api/v1/voucher-portal/batches/preview/", payload, format="json")
        created = self.client.post("/api/v1/voucher-portal/batches/",
                                   {**payload, "preview_hash": preview["X-Preview-Hash"]}, format="json").data

        csv_content = "name,phone,email,reference\nAli,+9715,,\nSara,+9716,,\n"
        from django.core.files.uploadedfile import SimpleUploadedFile
        upload = SimpleUploadedFile("recipients.csv", csv_content.encode(), content_type="text/csv")
        response = self.client.post(f"/api/v1/voucher-portal/batches/{created['id']}/issue_bulk/", {"file": upload}, format="multipart")
        self.assertEqual(response.status_code, 400)


def _make_image_upload(width, height, name="art.png"):
    import io
    from django.core.files.uploadedfile import SimpleUploadedFile
    from PIL import Image
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), color=(200, 50, 50)).save(buffer, format="PNG")
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/png")


class ArtworkUploadTests(TestCase):
    """Covers the create-batch form's inline artwork upload, which POSTs straight
    to the templates/ endpoint - see validators.py for the size/ratio rules,
    derived from the approved coupon's own proportions (2.74:1)."""

    def setUp(self):
        self.user = User.objects.create_user("tester", password="x")
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def test_correctly_proportioned_artwork_is_accepted(self):
        upload = _make_image_upload(1987, 725)  # the template's own native size
        response = self.client.post("/api/v1/voucher-portal/templates/",
                                    {"name": "Custom artwork", "artwork": upload}, format="multipart")
        self.assertEqual(response.status_code, 201, response.data)
        template = VoucherTemplate.objects.get(pk=response.data["id"])
        # A template created with no geometry of its own still gets the coupon's
        # known field positions, not an empty layout with nothing drawn on it.
        self.assertEqual(template.field_geometry, DEFAULT_FIELD_GEOMETRY)

    def test_uploaded_template_is_active_without_saying_so(self):
        """Regression: DRF's BooleanField treats an omitted key in a multipart
        upload like an unchecked HTML checkbox (False), overriding the model's
        own default=True, unless the serializer field says default=True itself.
        A template you can't select right after uploading it is a broken upload."""
        upload = _make_image_upload(1987, 725)
        response = self.client.post("/api/v1/voucher-portal/templates/",
                                    {"name": "Custom artwork", "artwork": upload}, format="multipart")
        template = VoucherTemplate.objects.get(pk=response.data["id"])
        self.assertTrue(template.is_active)
        # And usable as a batch's template right away, the way the create form uses it.
        self.assertIn(template, VoucherTemplate.objects.filter(is_active=True))

    def test_wrong_aspect_ratio_is_rejected(self):
        upload = _make_image_upload(2000, 2000)  # square, nowhere near 2.74:1
        response = self.client.post("/api/v1/voucher-portal/templates/",
                                    {"name": "Bad artwork", "artwork": upload}, format="multipart")
        self.assertEqual(response.status_code, 400)
        self.assertIn("artwork", response.data)

    def test_too_narrow_artwork_is_rejected(self):
        upload = _make_image_upload(800, 292)  # correct ratio, below the 1500px floor
        response = self.client.post("/api/v1/voucher-portal/templates/",
                                    {"name": "Too small", "artwork": upload}, format="multipart")
        self.assertEqual(response.status_code, 400)
        self.assertIn("artwork", response.data)
