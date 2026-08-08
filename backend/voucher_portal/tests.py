import copy
import os
import tempfile
import threading
import time
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.conf import settings
from django.test import TestCase, TransactionTestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient

from . import storage
from .geometry import DEFAULT_FIELD_GEOMETRY
from .pdf import build_batch_pdf, build_voucher_pdf
from .models import (Department, Notification, PortalBatch, PortalUserAccess, PortalVoucher, VoucherPrefix,
                     VoucherTemplate, VoucherType)
from .services import workflow
from .services.generation import create_draft_batch, generate_vouchers, payload_hash, render_preview
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


def grant(user, role, departments=None):
    access = PortalUserAccess.objects.create(user=user, role=role)
    if departments:
        access.departments.set(departments)
    return access


def approved_batch(dept, vtype, prefix, template, requester, approver, **overrides):
    """A batch already through submit + approve, ready for generate() - the
    state most issuing/reporting tests actually want to start from."""
    valid_from, valid_to = dates()
    data = {
        "name": "Diwali gift vouchers", "department": dept, "voucher_type": vtype, "description": "",
        "quantity": 5, "discount_type": "fixed", "fixed_value": Decimal("100"), "currency": "AED",
        "valid_from": valid_from, "valid_to": valid_to, "restrictions": "", "terms": "No cash value",
        "prefix": prefix, "template": template,
    }
    data.update(overrides)
    batch = create_draft_batch(data, requester)
    workflow.submit(batch, requester)
    workflow.approve(batch, approver)
    return batch


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

    def test_uses_supplied_text_and_width_over_live_prefix(self):
        """Generation formats numbers from the batch's snapshot, not whatever
        the live prefix looks like now - this is what makes that possible
        while still locking and advancing the live row's counter."""
        numbers, text, width = allocate(self.prefix.id, 2, prefix_text="OLD-", sequence_length=6)
        self.assertEqual(numbers, ["OLD-000001", "OLD-000002"])
        self.assertEqual((text, width), ("OLD-", 6))
        self.prefix.refresh_from_db()
        self.assertEqual(self.prefix.next_sequence, 3)  # the live counter still advanced normally


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

    def test_create_draft_batch_creates_no_vouchers_yet(self):
        """A draft is just the submitted settings - no numbers burned, no
        vouchers created, until it's approved and generated."""
        batch = create_draft_batch(self._form(), self.user)
        self.assertEqual(batch.status, "draft")
        self.assertEqual(batch.vouchers.count(), 0)
        self.prefix.refresh_from_db()
        self.assertEqual(self.prefix.next_sequence, 1)

    def test_generate_vouchers_creates_vouchers_and_advances_sequence(self):
        batch = create_draft_batch(self._form(), self.user)
        workflow.submit(batch, self.user)
        workflow.approve(batch, User.objects.create_user("approver", password="x"))
        generate_vouchers(batch)
        self.assertEqual(batch.vouchers.count(), 5)
        self.assertEqual(batch.status, "generating")
        self.prefix.refresh_from_db()
        self.assertEqual(self.prefix.next_sequence, 6)
        numbers = set(batch.vouchers.values_list("number", flat=True))
        self.assertEqual(numbers, {"EMP0001", "EMP0002", "EMP0003", "EMP0004", "EMP0005"})

    def test_batch_snapshot_survives_prefix_edit_before_generation(self):
        batch = create_draft_batch(self._form(), self.user)
        self.prefix.label = "Renamed"
        self.prefix.sequence_length = 6
        self.prefix.save()
        batch.refresh_from_db()
        self.assertEqual(batch.prefix_snapshot, "EMP")
        self.assertEqual(batch.sequence_length_snapshot, 4)
        # And generation still uses the snapshot, not the edited live prefix.
        workflow.submit(batch, self.user)
        workflow.approve(batch, User.objects.create_user("approver2", password="x"))
        generate_vouchers(batch)
        self.assertEqual(batch.vouchers.first().number[:3], "EMP")
        self.assertEqual(len(batch.vouchers.first().number), 3 + 4)  # unchanged 4-digit width, not the edited 6


class ApprovalWorkflowTests(TestCase):
    """The state machine and gating in services/workflow.py, independent of
    the API layer (permission enforcement is covered separately, in
    PortalApiPermissionTests)."""

    def setUp(self):
        self.dept, self.vtype, self.prefix, self.template = make_reference_data()
        self.requester = User.objects.create_user("requester", password="x")
        self.approver = User.objects.create_user("approver", password="x")

    def _draft(self, **overrides):
        valid_from, valid_to = dates()
        data = {
            "name": "Test batch", "department": self.dept, "voucher_type": self.vtype, "description": "",
            "quantity": 3, "discount_type": "fixed", "fixed_value": Decimal("50"), "currency": "AED",
            "valid_from": valid_from, "valid_to": valid_to, "restrictions": "", "terms": "",
            "prefix": self.prefix, "template": self.template,
        }
        data.update(overrides)
        return create_draft_batch(data, self.requester)

    def test_full_happy_path(self):
        batch = self._draft()
        workflow.submit(batch, self.requester)
        self.assertEqual(batch.status, "pending_approval")
        workflow.approve(batch, self.approver)
        self.assertEqual(batch.status, "approved")
        self.assertEqual(batch.approved_by, self.approver)
        workflow.generate(batch, self.requester)
        self.assertEqual(batch.status, "generating")

    def test_cannot_generate_before_approval(self):
        batch = self._draft()
        workflow.submit(batch, self.requester)
        with self.assertRaises(workflow.WorkflowError):
            workflow.generate(batch, self.requester)

    def test_reject_requires_a_reason(self):
        batch = self._draft()
        workflow.submit(batch, self.requester)
        with self.assertRaises(workflow.WorkflowError):
            workflow.reject(batch, self.approver, "")

    def test_reject_records_reason_and_notifies_requester(self):
        batch = self._draft()
        workflow.submit(batch, self.requester)
        workflow.reject(batch, self.approver, "Budget exceeded for this quarter.")
        self.assertEqual(batch.status, "rejected")
        self.assertEqual(batch.rejection_reason, "Budget exceeded for this quarter.")
        note = Notification.objects.get(user=self.requester, batch=batch, kind="rejected")
        self.assertIn("Budget exceeded", note.message)

    def test_approve_notifies_requester(self):
        batch = self._draft()
        workflow.submit(batch, self.requester)
        workflow.approve(batch, self.approver)
        self.assertTrue(Notification.objects.filter(user=self.requester, batch=batch, kind="approved").exists())

    def test_submit_notifies_approvers_in_department(self):
        grant(self.approver, "approver", [self.dept])
        batch = self._draft()
        workflow.submit(batch, self.requester)
        self.assertTrue(Notification.objects.filter(user=self.approver, batch=batch, kind="submitted").exists())

    def test_rejected_batch_can_be_resubmitted(self):
        batch = self._draft()
        workflow.submit(batch, self.requester)
        workflow.reject(batch, self.approver, "Not this time.")
        workflow.submit(batch, self.requester)
        self.assertEqual(batch.status, "pending_approval")
        self.assertEqual(batch.rejection_reason, "")  # cleared on resubmit

    def test_cannot_approve_twice(self):
        batch = self._draft()
        workflow.submit(batch, self.requester)
        workflow.approve(batch, self.approver)
        with self.assertRaises(workflow.WorkflowError):
            workflow.approve(batch, self.approver)

    def test_cancel_batch_from_various_states(self):
        batch = self._draft()
        workflow.cancel_batch(batch, self.approver, reason="No longer needed")
        self.assertEqual(batch.status, "cancelled")

    def test_cannot_cancel_already_cancelled_batch(self):
        batch = self._draft()
        workflow.cancel_batch(batch, self.approver)
        with self.assertRaises(workflow.WorkflowError):
            workflow.cancel_batch(batch, self.approver)


class RedeemAndCancelVoucherTests(TestCase):
    def setUp(self):
        self.dept, self.vtype, self.prefix, self.template = make_reference_data()
        self.requester = User.objects.create_user("requester", password="x")
        self.approver = User.objects.create_user("approver", password="x")
        batch = approved_batch(self.dept, self.vtype, self.prefix, self.template, self.requester, self.approver, quantity=2)
        generate_vouchers(batch)
        self.voucher = batch.vouchers.first()

    def test_cannot_redeem_before_issued(self):
        with self.assertRaises(workflow.WorkflowError):
            workflow.redeem_voucher(self.voucher, self.requester)

    def test_redeem_after_issued(self):
        self.voucher.issue(phone="+971500000000", actor=self.requester)
        workflow.redeem_voucher(self.voucher, self.requester)
        self.voucher.refresh_from_db()
        self.assertEqual(self.voucher.status, "redeemed")
        self.assertIsNotNone(self.voucher.redeemed_at)

    def test_cancel_voucher(self):
        workflow.cancel_voucher(self.voucher, self.approver, reason="Printing error")
        self.voucher.refresh_from_db()
        self.assertEqual(self.voucher.status, "cancelled")

    def test_cannot_cancel_twice(self):
        workflow.cancel_voucher(self.voucher, self.approver)
        with self.assertRaises(workflow.WorkflowError):
            workflow.cancel_voucher(self.voucher, self.approver)


class BatchIssueStatusTests(TestCase):
    def setUp(self):
        self.dept, self.vtype, self.prefix, self.template = make_reference_data()
        self.requester = User.objects.create_user("requester", password="x")
        self.approver = User.objects.create_user("approver", password="x")
        self.batch = approved_batch(self.dept, self.vtype, self.prefix, self.template, self.requester, self.approver, quantity=2)
        generate_vouchers(self.batch)

    def test_status_moves_through_partially_to_fully_issued(self):
        vouchers = list(self.batch.vouchers.all())
        vouchers[0].issue(actor=self.requester)
        self.batch.refresh_issue_status()
        self.assertEqual(self.batch.status, "partially_issued")
        vouchers[1].issue(actor=self.requester)
        self.batch.refresh_issue_status()
        self.assertEqual(self.batch.status, "fully_issued")


class PortalApiPermissionTests(TestCase):
    """Role and department-scope enforcement at the API layer - the part that
    actually matters, since services/workflow.py trusts its caller."""

    def setUp(self):
        self.hr, self.hr_type, self.hr_prefix, self.template = make_reference_data()
        self.mkt = Department.objects.create(code="MKT", name="Marketing")
        self.mkt_type = VoucherType.objects.create(code="MKTV", name="Marketing Voucher", department=self.mkt)
        self.mkt_prefix = VoucherPrefix.objects.create(prefix="MKT", label="Marketing", department=self.mkt,
                                                       voucher_type=self.mkt_type, sequence_length=4)

    def _payload(self, dept, vtype, prefix, **overrides):
        valid_from, valid_to = dates()
        data = {
            "name": "Test batch", "department": dept.id, "voucher_type": vtype.id, "quantity": 3,
            "discount_type": "fixed", "fixed_value": "50", "currency": "AED", "valid_to": valid_to.isoformat(),
            "terms": "No cash value", "prefix": prefix.id,
        }
        data.update(overrides)
        return data

    def test_no_access_grant_is_forbidden(self):
        user = User.objects.create_user("nobody", password="x")
        client = APIClient()
        client.force_authenticate(user)
        response = client.get("/api/v1/voucher-portal/batches/")
        self.assertEqual(response.status_code, 403)

    def test_django_staff_gets_implicit_admin_access(self):
        user = User.objects.create_user("staffer", password="x", is_staff=True)
        client = APIClient()
        client.force_authenticate(user)
        response = client.get("/api/v1/voucher-portal/batches/")
        self.assertEqual(response.status_code, 200)

    def test_requester_scoped_to_own_department_cannot_create_in_another(self):
        user = User.objects.create_user("hr_requester", password="x")
        grant(user, "requester", [self.hr])
        client = APIClient()
        client.force_authenticate(user)

        payload = self._payload(self.mkt, self.mkt_type, self.mkt_prefix)
        preview = client.post("/api/v1/voucher-portal/batches/preview/", payload, format="json")
        self.assertEqual(preview.status_code, 403)

    def test_requester_can_create_in_own_department(self):
        user = User.objects.create_user("hr_requester2", password="x")
        grant(user, "requester", [self.hr])
        client = APIClient()
        client.force_authenticate(user)

        payload = self._payload(self.hr, self.hr_type, self.hr_prefix)
        preview = client.post("/api/v1/voucher-portal/batches/preview/", payload, format="json")
        self.assertEqual(preview.status_code, 200)

    def test_report_viewer_cannot_create(self):
        user = User.objects.create_user("viewer", password="x")
        grant(user, "report_viewer")
        client = APIClient()
        client.force_authenticate(user)
        payload = self._payload(self.hr, self.hr_type, self.hr_prefix)
        response = client.post("/api/v1/voucher-portal/batches/preview/", payload, format="json")
        self.assertEqual(response.status_code, 403)

    def test_department_scoped_list_hides_other_departments(self):
        requester = User.objects.create_user("hr_requester3", password="x")
        grant(requester, "requester", [self.hr])
        create_draft_batch({
            "name": "HR batch", "department": self.hr, "voucher_type": self.hr_type, "quantity": 1,
            "discount_type": "fixed", "fixed_value": Decimal("10"), "currency": "AED",
            "valid_from": dates()[0], "valid_to": dates()[1], "prefix": self.hr_prefix, "template": self.template,
        }, requester)
        create_draft_batch({
            "name": "MKT batch", "department": self.mkt, "voucher_type": self.mkt_type, "quantity": 1,
            "discount_type": "fixed", "fixed_value": Decimal("10"), "currency": "AED",
            "valid_from": dates()[0], "valid_to": dates()[1], "prefix": self.mkt_prefix, "template": self.template,
        }, requester)

        client = APIClient()
        client.force_authenticate(requester)
        response = client.get("/api/v1/voucher-portal/batches/")
        names = {row["name"] for row in response.data["results"]}
        self.assertEqual(names, {"HR batch"})

    def test_self_approval_blocked_for_non_administrator(self):
        user = User.objects.create_user("dual_role", password="x")
        grant(user, "approver", [self.hr])
        # Give this same user requester powers too, by making a second grant impossible
        # (OneToOne) - instead, create the batch as staff, then have the approver try
        # to approve their own submission by acting as the batch's created_by.
        batch = create_draft_batch({
            "name": "Self-approval test", "department": self.hr, "voucher_type": self.hr_type, "quantity": 1,
            "discount_type": "fixed", "fixed_value": Decimal("10"), "currency": "AED",
            "valid_from": dates()[0], "valid_to": dates()[1], "prefix": self.hr_prefix, "template": self.template,
        }, user)
        workflow.submit(batch, user)

        client = APIClient()
        client.force_authenticate(user)
        response = client.post(f"/api/v1/voucher-portal/batches/{batch.id}/approve/", {}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("own request", response.data[0] if isinstance(response.data, list) else str(response.data))

    def test_administrator_can_approve_own_request(self):
        admin_user = User.objects.create_user("admin_user", password="x", is_staff=True)
        batch = create_draft_batch({
            "name": "Admin self-approval", "department": self.hr, "voucher_type": self.hr_type, "quantity": 1,
            "discount_type": "fixed", "fixed_value": Decimal("10"), "currency": "AED",
            "valid_from": dates()[0], "valid_to": dates()[1], "prefix": self.hr_prefix, "template": self.template,
        }, admin_user)
        workflow.submit(batch, admin_user)

        client = APIClient()
        client.force_authenticate(admin_user)
        response = client.post(f"/api/v1/voucher-portal/batches/{batch.id}/approve/", {}, format="json")
        self.assertEqual(response.status_code, 200, response.data)


class PortalApiWorkflowTests(TestCase):
    """The end-to-end HTTP flow: draft -> submit -> approve -> generate ->
    issue, exercised through the API rather than calling services directly."""

    def setUp(self):
        self.dept, self.vtype, self.prefix, self.template = make_reference_data()
        self.requester = User.objects.create_user("requester", password="x", is_staff=True)
        self.approver = User.objects.create_user("approver", password="x", is_staff=True)
        self.client = APIClient()
        self.client.force_authenticate(self.requester)

    def _payload(self, **overrides):
        valid_from, valid_to = dates()
        data = {
            "name": "Diwali gift vouchers", "department": self.dept.id, "voucher_type": self.vtype.id,
            "quantity": 3, "discount_type": "percentage", "percentage_value": "20", "max_discount_value": "50",
            "currency": "AED", "valid_to": valid_to.isoformat(), "terms": "No cash value", "prefix": self.prefix.id,
        }
        data.update(overrides)
        return data

    def test_valid_from_defaults_to_today_when_omitted(self):
        payload = self._payload()
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

    def test_create_lands_in_draft_not_generated(self):
        payload = self._payload()
        preview = self.client.post("/api/v1/voucher-portal/batches/preview/", payload, format="json")
        response = self.client.post("/api/v1/voucher-portal/batches/",
                                    {**payload, "preview_hash": preview["X-Preview-Hash"]}, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["status"], "draft")
        self.assertEqual(response.data["quantity"], 3)

    def _create_draft(self, **overrides):
        payload = self._payload(**overrides)
        preview = self.client.post("/api/v1/voucher-portal/batches/preview/", payload, format="json")
        response = self.client.post("/api/v1/voucher-portal/batches/",
                                    {**payload, "preview_hash": preview["X-Preview-Hash"]}, format="json")
        return response.data["id"]

    def test_full_http_workflow_to_issuing(self):
        batch_id = self._create_draft(quantity=5, discount_type="fixed", fixed_value="100",
                                      percentage_value=None, max_discount_value=None)

        submit = self.client.post(f"/api/v1/voucher-portal/batches/{batch_id}/submit/")
        self.assertEqual(submit.status_code, 200, submit.data)
        self.assertEqual(submit.data["status"], "pending_approval")

        approver_client = APIClient()
        approver_client.force_authenticate(self.approver)
        approve = approver_client.post(f"/api/v1/voucher-portal/batches/{batch_id}/approve/", {}, format="json")
        self.assertEqual(approve.status_code, 200, approve.data)
        self.assertEqual(approve.data["status"], "approved")

        generate = self.client.post(f"/api/v1/voucher-portal/batches/{batch_id}/generate/")
        self.assertEqual(generate.status_code, 200, generate.data)
        self.assertEqual(generate.data["status"], "generating")

        batch = PortalBatch.objects.get(pk=batch_id)
        self.assertEqual(batch.vouchers.count(), 5)
        voucher_ids = list(batch.vouchers.values_list("id", flat=True))

        manual = self.client.post("/api/v1/voucher-portal/vouchers/issue/",
                                  {"voucher_ids": [voucher_ids[0]], "phone": "+971500000000"}, format="json")
        self.assertEqual(manual.status_code, 200)
        self.assertEqual(PortalVoucher.objects.get(pk=voucher_ids[0]).status, "issued")

        csv_content = "name,phone,email,reference\nAli,+9715,,\nSara,+9716,,\n"
        upload = SimpleUploadedFile("recipients.csv", csv_content.encode(), content_type="text/csv")
        bulk = self.client.post(f"/api/v1/voucher-portal/batches/{batch_id}/issue_bulk/", {"file": upload}, format="multipart")
        self.assertEqual(bulk.status_code, 200, bulk.data)
        self.assertEqual(bulk.data["assigned"], 2)
        self.assertEqual(bulk.data["remaining_available"], 2)

        batch.refresh_from_db()
        self.assertEqual(batch.status, "partially_issued")

    def test_generate_before_approval_rejected(self):
        batch_id = self._create_draft()
        self.client.post(f"/api/v1/voucher-portal/batches/{batch_id}/submit/")
        response = self.client.post(f"/api/v1/voucher-portal/batches/{batch_id}/generate/")
        self.assertEqual(response.status_code, 400)

    def test_reject_without_reason_rejected_by_api(self):
        batch_id = self._create_draft()
        self.client.post(f"/api/v1/voucher-portal/batches/{batch_id}/submit/")
        approver_client = APIClient()
        approver_client.force_authenticate(self.approver)
        response = approver_client.post(f"/api/v1/voucher-portal/batches/{batch_id}/reject/", {}, format="json")
        self.assertEqual(response.status_code, 400)

    def test_bulk_csv_rejects_more_recipients_than_available_vouchers(self):
        batch_id = self._create_draft(quantity=1, discount_type="fixed", fixed_value="100",
                                      percentage_value=None, max_discount_value=None)
        self.client.post(f"/api/v1/voucher-portal/batches/{batch_id}/submit/")
        approver_client = APIClient()
        approver_client.force_authenticate(self.approver)
        approver_client.post(f"/api/v1/voucher-portal/batches/{batch_id}/approve/", {}, format="json")
        self.client.post(f"/api/v1/voucher-portal/batches/{batch_id}/generate/")

        csv_content = "name,phone,email,reference\nAli,+9715,,\nSara,+9716,,\n"
        upload = SimpleUploadedFile("recipients.csv", csv_content.encode(), content_type="text/csv")
        response = self.client.post(f"/api/v1/voucher-portal/batches/{batch_id}/issue_bulk/", {"file": upload}, format="multipart")
        self.assertEqual(response.status_code, 400)


class NotificationApiTests(TestCase):
    def setUp(self):
        self.dept, self.vtype, self.prefix, self.template = make_reference_data()
        self.requester = User.objects.create_user("requester", password="x")
        self.approver = User.objects.create_user("approver", password="x")
        grant(self.requester, "requester", [self.dept])
        grant(self.approver, "approver", [self.dept])

    def test_notification_appears_and_can_be_marked_read(self):
        batch = approved_batch(self.dept, self.vtype, self.prefix, self.template, self.requester, self.approver)
        client = APIClient()
        client.force_authenticate(self.requester)
        response = client.get("/api/v1/voucher-portal/notifications/")
        self.assertEqual(response.data["count"], 1)
        note_id = response.data["results"][0]["id"]
        self.assertIsNone(response.data["results"][0]["read_at"])

        mark = client.post(f"/api/v1/voucher-portal/notifications/{note_id}/read/")
        self.assertEqual(mark.status_code, 200)
        self.assertIsNotNone(mark.data["read_at"])

    def test_notifications_are_scoped_to_the_user(self):
        approved_batch(self.dept, self.vtype, self.prefix, self.template, self.requester, self.approver)
        other = User.objects.create_user("stranger", password="x")
        grant(other, "requester", [self.dept])
        client = APIClient()
        client.force_authenticate(other)
        response = client.get("/api/v1/voucher-portal/notifications/")
        self.assertEqual(response.data["count"], 0)


class ReportsApiTests(TestCase):
    def setUp(self):
        self.dept, self.vtype, self.prefix, self.template = make_reference_data()
        self.mkt = Department.objects.create(code="MKT", name="Marketing")
        self.mkt_type = VoucherType.objects.create(code="MKTV", name="Marketing Voucher", department=self.mkt)
        self.mkt_prefix = VoucherPrefix.objects.create(prefix="MKT", label="Marketing", department=self.mkt,
                                                       voucher_type=self.mkt_type, sequence_length=4)
        self.requester = User.objects.create_user("requester", password="x", is_staff=True)
        self.approver = User.objects.create_user("approver", password="x", is_staff=True)

        hr_batch = approved_batch(self.dept, self.vtype, self.prefix, self.template, self.requester, self.approver, quantity=3)
        generate_vouchers(hr_batch)
        list(hr_batch.vouchers.all())[0].issue(actor=self.requester)

        mkt_batch = approved_batch(self.mkt, self.mkt_type, self.mkt_prefix, self.template, self.requester, self.approver, quantity=2)
        generate_vouchers(mkt_batch)

    def test_administrator_summary_covers_all_departments(self):
        client = APIClient()
        client.force_authenticate(self.requester)
        response = client.get("/api/v1/voucher-portal/reports/summary/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["total"], 5)
        self.assertEqual(response.data["issued"], 1)

    def test_report_viewer_scoped_to_one_department(self):
        viewer = User.objects.create_user("viewer", password="x")
        grant(viewer, "report_viewer", [self.dept])
        client = APIClient()
        client.force_authenticate(viewer)
        response = client.get("/api/v1/voucher-portal/reports/summary/")
        self.assertEqual(response.data["total"], 3)  # HR only, not Marketing's 2

    def test_by_department_breakdown(self):
        client = APIClient()
        client.force_authenticate(self.requester)
        response = client.get("/api/v1/voucher-portal/reports/by-department/")
        totals = {row["batch__department__name"]: row["total"] for row in response.data}
        self.assertEqual(totals, {"HR": 3, "Marketing": 2})

    def test_csv_export(self):
        client = APIClient()
        client.force_authenticate(self.requester)
        response = client.get("/api/v1/voucher-portal/reports/export/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/csv")
        body = response.content.decode()
        self.assertEqual(body.count("\n"), 6)  # header + 5 vouchers (3 HR + 2 MKT)


def _make_image_upload(width, height, name="art.png"):
    import io
    from PIL import Image
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), color=(200, 50, 50)).save(buffer, format="PNG")
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/png")


class ArtworkUploadTests(TestCase):
    """Covers the create-batch form's inline artwork upload, which POSTs straight
    to the templates/ endpoint - see validators.py for the size/ratio rules,
    derived from the approved coupon's own proportions (2.74:1)."""

    def setUp(self):
        self.user = User.objects.create_user("tester", password="x", is_staff=True)
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

    def test_non_admin_requester_can_still_upload_artwork_for_their_own_batch(self):
        requester = User.objects.create_user("plain_requester", password="x")
        dept = Department.objects.create(code="HR2", name="HR Two")
        grant(requester, "requester", [dept])
        client = APIClient()
        client.force_authenticate(requester)
        upload = _make_image_upload(1987, 725)
        response = client.post("/api/v1/voucher-portal/templates/",
                               {"name": "Requester artwork", "artwork": upload}, format="multipart")
        self.assertEqual(response.status_code, 201, response.data)


class PortalUserAccessApiTests(TestCase):
    def setUp(self):
        self.dept = Department.objects.create(code="HR", name="HR")
        self.admin = User.objects.create_user("admin_user", password="x", is_staff=True)
        self.target = User.objects.create_user("new_requester", password="x")
        self.client = APIClient()
        self.client.force_authenticate(self.admin)

    def test_admin_can_grant_access(self):
        response = self.client.post("/api/v1/voucher-portal/access/",
                                    {"user": "new_requester", "role": "requester", "department_ids": [self.dept.id]},
                                    format="json")
        self.assertEqual(response.status_code, 201, response.data)
        access = PortalUserAccess.objects.get(user=self.target)
        self.assertEqual(access.role, "requester")
        self.assertEqual(list(access.departments.all()), [self.dept])

    def test_me_endpoint_reflects_own_role_and_scope(self):
        requester = User.objects.create_user("plain2", password="x")
        grant(requester, "requester", [self.dept])
        client = APIClient()
        client.force_authenticate(requester)
        response = client.get("/api/v1/voucher-portal/access/me/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["role"], "requester")
        self.assertEqual(response.data["department_ids"], [self.dept.id])
        self.assertIn("create", response.data["actions"])

    def test_non_admin_cannot_grant_access(self):
        requester = User.objects.create_user("plain", password="x")
        grant(requester, "requester", [self.dept])
        client = APIClient()
        client.force_authenticate(requester)
        response = client.post("/api/v1/voucher-portal/access/",
                               {"user": "new_requester", "role": "requester"}, format="json")
        self.assertEqual(response.status_code, 403)


class GeometryValidationTests(TestCase):
    """The geometry editor is the only thing that can move fields on a printed
    voucher, so a bad edit has to be rejected before it's saved, not
    discovered on a print run."""

    def setUp(self):
        self.user = User.objects.create_user("geo_admin", password="x", is_staff=True)
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.template = VoucherTemplate.objects.create(name="Editable", is_default=True)

    def _patch(self, geometry):
        return self.client.patch(f"/api/v1/voucher-portal/templates/{self.template.id}/",
                                 {"field_geometry": geometry}, format="json")

    def test_valid_move_is_accepted(self):
        geometry = copy.deepcopy(DEFAULT_FIELD_GEOMETRY)
        geometry["fields"][0]["x"] = 40
        geometry["fields"][0]["y"] = 25
        response = self._patch(geometry)
        self.assertEqual(response.status_code, 200, response.data)
        self.template.refresh_from_db()
        self.assertEqual(self.template.field_geometry["fields"][0]["x"], 40)

    def test_field_outside_the_coupon_is_rejected(self):
        geometry = copy.deepcopy(DEFAULT_FIELD_GEOMETRY)
        geometry["fields"][0]["y"] = 900  # coupon is only 178pt tall
        response = self._patch(geometry)
        self.assertEqual(response.status_code, 400)
        self.assertIn("field_geometry", response.data)

    def test_negative_position_is_rejected(self):
        geometry = copy.deepcopy(DEFAULT_FIELD_GEOMETRY)
        geometry["fields"][0]["x"] = -5
        self.assertEqual(self._patch(geometry).status_code, 400)

    def test_unknown_field_key_is_rejected(self):
        geometry = copy.deepcopy(DEFAULT_FIELD_GEOMETRY)
        geometry["fields"].append({"key": "not_a_real_field", "x": 10, "y": 10, "size": 8})
        response = self._patch(geometry)
        self.assertEqual(response.status_code, 400)
        self.assertIn("not_a_real_field", str(response.data))

    def test_non_numeric_position_is_rejected(self):
        geometry = copy.deepcopy(DEFAULT_FIELD_GEOMETRY)
        geometry["fields"][0]["x"] = "left-ish"
        self.assertEqual(self._patch(geometry).status_code, 400)

    def test_duplicate_field_key_is_rejected(self):
        geometry = copy.deepcopy(DEFAULT_FIELD_GEOMETRY)
        geometry["fields"].append(copy.deepcopy(geometry["fields"][0]))
        self.assertEqual(self._patch(geometry).status_code, 400)

    def test_field_catalogue_matches_what_the_renderer_draws(self):
        response = self.client.get("/api/v1/voucher-portal/templates/field-catalogue/")
        self.assertEqual(response.status_code, 200)
        catalogue_keys = {f["key"] for f in response.data["fields"]}
        default_keys = {f["key"] for f in DEFAULT_FIELD_GEOMETRY["fields"]}
        self.assertEqual(catalogue_keys, default_keys)

    def test_template_preview_renders_without_a_batch(self):
        response = self.client.get(f"/api/v1/voucher-portal/templates/{self.template.id}/preview/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertTrue(response.content.startswith(b"%PDF"))
        # and nothing was persisted to get that preview
        self.assertEqual(PortalBatch.objects.count(), 0)
        self.assertEqual(PortalVoucher.objects.count(), 0)

    def test_template_preview_renders_unsaved_geometry(self):
        geometry = copy.deepcopy(DEFAULT_FIELD_GEOMETRY)
        geometry["fields"][0]["x"] = 60
        response = self.client.post(f"/api/v1/voucher-portal/templates/{self.template.id}/preview/",
                                    {"field_geometry": geometry}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.content.startswith(b"%PDF"))
        self.template.refresh_from_db()  # preview must not save the edit
        self.assertEqual(self.template.field_geometry["fields"][0]["x"], DEFAULT_FIELD_GEOMETRY["fields"][0]["x"])

    def test_template_preview_rejects_invalid_unsaved_geometry(self):
        geometry = copy.deepcopy(DEFAULT_FIELD_GEOMETRY)
        geometry["fields"][0]["y"] = 5000
        response = self.client.post(f"/api/v1/voucher-portal/templates/{self.template.id}/preview/",
                                    {"field_geometry": geometry}, format="json")
        self.assertEqual(response.status_code, 400)

    def test_reset_geometry_restores_the_default_layout(self):
        geometry = copy.deepcopy(DEFAULT_FIELD_GEOMETRY)
        geometry["fields"][0]["x"] = 77
        self._patch(geometry)
        response = self.client.post(f"/api/v1/voucher-portal/templates/{self.template.id}/reset-geometry/")
        self.assertEqual(response.status_code, 200)
        self.template.refresh_from_db()
        self.assertEqual(self.template.field_geometry, DEFAULT_FIELD_GEOMETRY)

    def test_non_admin_cannot_reset_geometry(self):
        requester = User.objects.create_user("geo_requester", password="x")
        dept = Department.objects.create(code="GEO", name="Geo")
        grant(requester, "requester", [dept])
        client = APIClient()
        client.force_authenticate(requester)
        response = client.post(f"/api/v1/voucher-portal/templates/{self.template.id}/reset-geometry/")
        self.assertEqual(response.status_code, 403)


class AdvancedReportsTests(TestCase):
    def setUp(self):
        self.dept, self.vtype, self.prefix, self.template = make_reference_data()
        self.mkt = Department.objects.create(code="MKT", name="Marketing")
        self.mkt_type = VoucherType.objects.create(code="MKTV", name="Marketing Voucher", department=self.mkt)
        self.mkt_prefix = VoucherPrefix.objects.create(prefix="MKT", label="Marketing", department=self.mkt,
                                                       voucher_type=self.mkt_type, sequence_length=4)
        self.requester = User.objects.create_user("requester", password="x", is_staff=True)
        self.approver = User.objects.create_user("approver", password="x", is_staff=True)

        hr_batch = approved_batch(self.dept, self.vtype, self.prefix, self.template, self.requester, self.approver, quantity=3)
        generate_vouchers(hr_batch)
        vouchers = list(hr_batch.vouchers.all())
        vouchers[0].issue(actor=self.requester)
        workflow.redeem_voucher(vouchers[0], self.requester)

        mkt_batch = approved_batch(self.mkt, self.mkt_type, self.mkt_prefix, self.template, self.requester, self.approver, quantity=2)
        generate_vouchers(mkt_batch)

        self.client = APIClient()
        self.client.force_authenticate(self.requester)

    def test_trend_returns_a_dense_monthly_series(self):
        response = self.client.get("/api/v1/voucher-portal/reports/trend/?months=6")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 6)  # empty months filled in, not skipped
        self.assertEqual({row["month"] for row in response.data}.__len__(), 6)
        current = timezone.localdate().strftime("%Y-%m")
        this_month = next(row for row in response.data if row["month"] == current)
        self.assertEqual(this_month["created"], 5)
        self.assertEqual(this_month["issued"], 1)   # issued counts redeemed too - it got there via issued
        self.assertEqual(this_month["redeemed"], 1)

    def test_batch_level_report(self):
        response = self.client.get("/api/v1/voucher-portal/reports/batches/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 2)
        by_department = {row["department"]: row for row in response.data}
        self.assertEqual(by_department["HR"]["generated"], 3)
        self.assertEqual(by_department["HR"]["redeemed"], 1)
        self.assertEqual(by_department["Marketing"]["generated"], 2)

    def test_department_filter_narrows_every_report(self):
        summary = self.client.get(f"/api/v1/voucher-portal/reports/summary/?department={self.dept.id}")
        self.assertEqual(summary.data["total"], 3)
        batches = self.client.get(f"/api/v1/voucher-portal/reports/batches/?department={self.dept.id}")
        self.assertEqual(len(batches.data), 1)

    def test_voucher_type_filter(self):
        response = self.client.get(f"/api/v1/voucher-portal/reports/summary/?voucher_type={self.mkt_type.id}")
        self.assertEqual(response.data["total"], 2)

    def test_status_filter(self):
        response = self.client.get("/api/v1/voucher-portal/reports/summary/?status=redeemed")
        self.assertEqual(response.data["total"], 1)

    def test_date_range_filter_excludes_everything_before_today(self):
        tomorrow = (timezone.localdate() + timedelta(days=1)).isoformat()
        response = self.client.get(f"/api/v1/voucher-portal/reports/summary/?from={tomorrow}")
        self.assertEqual(response.data["total"], 0)

    def test_report_viewer_scope_still_applies_to_trend_and_batches(self):
        viewer = User.objects.create_user("scoped_viewer", password="x")
        grant(viewer, "report_viewer", [self.dept])
        client = APIClient()
        client.force_authenticate(viewer)
        batches = client.get("/api/v1/voucher-portal/reports/batches/")
        self.assertEqual(len(batches.data), 1)
        self.assertEqual(batches.data[0]["department"], "HR")
        trend = client.get("/api/v1/voucher-portal/reports/trend/")
        current = timezone.localdate().strftime("%Y-%m")
        this_month = next(row for row in trend.data if row["month"] == current)
        self.assertEqual(this_month["created"], 3)  # HR's 3, not all 5


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="voucher-download-tests-"))
class DownloadTests(TestCase):
    """PDFs stream through the authenticated API rather than a public media
    URL - they carry recipient data, and the stored media path is
    host-relative (it would resolve against the console's origin, not the
    API's, which is how this surfaced: a download opened the login page)."""

    def setUp(self):
        self.dept, self.vtype, self.prefix, self.template = make_reference_data()
        self.requester = User.objects.create_user("requester", password="x", is_staff=True)
        self.approver = User.objects.create_user("approver", password="x", is_staff=True)
        self.batch = approved_batch(self.dept, self.vtype, self.prefix, self.template,
                                    self.requester, self.approver, quantity=2)
        generate_vouchers(self.batch)
        self.batch.refresh_from_db()
        # generate_vouchers hands PDF assembly to a background thread, which in a
        # TestCase can't see the uncommitted batch on its own connection. Do that
        # work synchronously here so the download endpoint has the same state a
        # finished job would leave behind.
        vouchers = list(self.batch.vouchers.all())
        for voucher in vouchers:
            voucher.pdf_url = storage.store_file(
                storage.voucher_pdf_key(self.batch.id, voucher.number),
                build_voucher_pdf(self.batch, voucher))
        PortalVoucher.objects.bulk_update(vouchers, ["pdf_url"])
        self.batch.combined_pdf_url = storage.store_file(
            storage.combined_pdf_key(self.batch.id), build_batch_pdf(self.batch, vouchers))
        self.batch.save(update_fields=["combined_pdf_url"])

        self.client = APIClient()
        self.client.force_authenticate(self.requester)

    def test_batch_download_streams_a_pdf(self):
        response = self.client.get(f"/api/v1/voucher-portal/batches/{self.batch.id}/download/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn("attachment", response["Content-Disposition"])
        self.assertTrue(b"".join(response.streaming_content).startswith(b"%PDF"))

    def test_voucher_download_streams_a_pdf(self):
        voucher = self.batch.vouchers.first()
        response = self.client.get(f"/api/v1/voucher-portal/vouchers/{voucher.id}/download/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(voucher.number, response["Content-Disposition"])
        self.assertTrue(b"".join(response.streaming_content).startswith(b"%PDF"))

    def test_download_requires_authentication(self):
        anon = APIClient()
        self.assertEqual(anon.get(f"/api/v1/voucher-portal/batches/{self.batch.id}/download/").status_code, 401)

    def test_download_is_department_scoped(self):
        """404 rather than 403 is intentional - the queryset is already
        department-scoped, so another department's batch simply doesn't exist
        for this caller and its existence isn't leaked."""
        other_dept = Department.objects.create(code="MKT", name="Marketing")
        outsider = User.objects.create_user("outsider", password="x")
        grant(outsider, "requester", [other_dept])
        client = APIClient()
        client.force_authenticate(outsider)
        response = client.get(f"/api/v1/voucher-portal/batches/{self.batch.id}/download/")
        self.assertEqual(response.status_code, 404)

    def test_report_viewer_cannot_download_voucher_pdfs(self):
        viewer = User.objects.create_user("viewer", password="x")
        grant(viewer, "report_viewer", [self.dept])
        client = APIClient()
        client.force_authenticate(viewer)
        response = client.get(f"/api/v1/voucher-portal/batches/{self.batch.id}/download/")
        self.assertEqual(response.status_code, 403)

    def test_missing_file_reports_clearly_rather_than_500(self):
        os.remove(os.path.join(settings.MEDIA_ROOT, storage.combined_pdf_key(self.batch.id)))
        response = self.client.get(f"/api/v1/voucher-portal/batches/{self.batch.id}/download/")
        self.assertEqual(response.status_code, 400)
        self.assertIn("missing", str(response.data).lower())
