import copy
import json
import os
import tempfile
import threading
import time
from unittest import mock
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.conf import settings
from django.core.exceptions import ValidationError
from django.test import Client, TestCase, TransactionTestCase, override_settings
from django.urls import get_script_prefix, set_script_prefix
from django.utils import timezone
from rest_framework.test import APIClient

from . import storage
from .geometry import (BLANK_GEOMETRY, LEGACY_COUPON_GEOMETRY, VARIABLE_KEYS, VARIABLES,
                       blank_geometry, to_elements)
from .pdf import _draw_box, build_batch_pdf, build_context, build_voucher_pdf
from .models import (Department, Notification, PortalBatch, PortalUserAccess, PortalVoucher, StatusChange, VoucherPrefix,
                     VoucherTemplate, VoucherType)
from .services import workflow
from .services.generation import _template_snapshot, create_draft_batch, generate_vouchers, payload_hash, render_preview
from .services.numbering import NumberingError, allocate


def dates(days_from=0, days_to=365):
    today = timezone.localdate()
    return today + timedelta(days=days_from), today + timedelta(days=days_to)


def make_reference_data():
    dept = Department.objects.create(code="HR", name="HR")
    vtype = VoucherType.objects.create(code="EMP", name="Employee Voucher", department=dept)
    prefix = VoucherPrefix.objects.create(prefix="EMP", label="Employee", department=dept, voucher_type=vtype,
                                          sequence_length=4)
    template = VoucherTemplate.objects.create(name="Default", field_geometry=copy.deepcopy(BLANK_GEOMETRY),
                                              is_default=True)
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
        from django.db import connections
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
                    connections.close_all()  # this worker thread is done with its connection

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

    def test_batch_artwork_override_enables_a_blank_template_artwork_layer(self):
        self.template.field_geometry.pop("artwork", None)
        snapshot = _template_snapshot(self.template, artwork_path="/tmp/batch.png")
        self.assertEqual(snapshot["artwork_path"], "/tmp/batch.png")
        self.assertEqual(snapshot["artwork"]["w"], self.template.coupon_width)
        self.assertEqual(snapshot["artwork"]["h"], self.template.coupon_height)
        self.assertFalse(snapshot["artwork"]["hidden"])

    def test_hash_changes_when_form_changes(self):
        base = payload_hash(self._form())
        changed = payload_hash(self._form(quantity=6))
        self.assertNotEqual(base, changed)

    def test_hash_stable_for_identical_payload(self):
        self.assertEqual(payload_hash(self._form()), payload_hash(self._form()))

    def test_hash_changes_when_the_card_design_changes(self):
        """The create form can open the designer and come back, so restyling
        the card between preview and save has to invalidate the preview - the
        template id alone stays the same."""
        before = payload_hash(self._form())
        self.template.field_geometry = designed_geometry(text_element(text="Restyled"))
        self.template.save()
        self.template.refresh_from_db()
        self.assertNotEqual(before, payload_hash(self._form(template=self.template)))

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

    @mock.patch.object(storage, "_S3_BUCKET", "")
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


def _make_image_upload(width, height, name="art.png", color=(200, 50, 50)):
    import io
    from PIL import Image
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), color=color).save(buffer, format="PNG")
    return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/png")


@override_settings(MEDIA_ROOT=tempfile.mkdtemp(prefix="voucher-artwork-tests-"))
class ArtworkUploadTests(TestCase):
    """Covers uploading a card's background artwork - the designer's "new card"
    form and its Replace artwork control both POST/PATCH straight to the
    templates/ endpoint - see validators.py for the size/ratio rules, derived
    from the card's own proportions - and serving those files back through the
    API."""

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
        # A new template is an empty card carrying only the mandatory barcode -
        # no prefilled coupon fields to delete before designing anything.
        self.assertEqual(template.field_geometry, BLANK_GEOMETRY)
        self.assertEqual([e["type"] for e in template.field_geometry["elements"]], ["barcode"])

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

    def test_artwork_is_served_through_the_api_not_a_media_url(self):
        """The `artwork` field's own media URL is a 404 in production: Django
        registers those routes through `static()`, which does nothing when
        DEBUG is false, and nginx only proxies the API's prefix. `artwork_path`
        is the route that actually works."""
        upload = _make_image_upload(1987, 725)
        created = self.client.post("/api/v1/voucher-portal/templates/",
                                   {"name": "With artwork", "artwork": upload}, format="multipart")
        self.assertEqual(created.status_code, 201, created.data)
        path = created.data["artwork_path"]
        self.assertEqual(path, f"voucher-portal/templates/{created.data['id']}/artwork/")

        response = self.client.get(f"/api/v1/{path}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "image/png")
        self.assertTrue(b"".join(response.streaming_content).startswith(b"\x89PNG"))

    def test_a_template_without_artwork_has_no_artwork_path(self):
        template = VoucherTemplate.objects.create(name="Plain")
        response = self.client.get(f"/api/v1/voucher-portal/templates/{template.id}/")
        self.assertIsNone(response.data["artwork_path"])
        self.assertEqual(self.client.get(f"/api/v1/voucher-portal/templates/{template.id}/artwork/").status_code, 400)

    def test_artwork_missing_from_disk_reports_clearly(self):
        """Files uploaded before MEDIA_ROOT moved out of the release directory
        are gone after a deploy - say so instead of raising a 500."""
        upload = _make_image_upload(1987, 725)
        created = self.client.post("/api/v1/voucher-portal/templates/",
                                   {"name": "Orphaned", "artwork": upload}, format="multipart")
        template = VoucherTemplate.objects.get(pk=created.data["id"])
        os.remove(template.artwork.path)
        response = self.client.get(f"/api/v1/voucher-portal/templates/{template.id}/artwork/")
        self.assertEqual(response.status_code, 400)
        self.assertIn("missing", str(response.data))

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

    def test_batch_artwork_is_previewed_saved_and_snapshotted(self):
        dept, vtype, prefix, template = make_reference_data()
        payload = {
            "name": "Batch artwork", "department": dept.id, "voucher_type": vtype.id,
            "quantity": 2, "discount_type": "fixed", "fixed_value": "75", "currency": "AED",
            "valid_to": dates()[1].isoformat(), "terms": "Form values print here",
            "prefix": prefix.id, "template": template.id,
        }
        preview = self.client.post(
            "/api/v1/voucher-portal/batches/preview/",
            {**payload, "artwork": _make_image_upload(1987, 725)}, format="multipart",
        )
        self.assertEqual(preview.status_code, 200, preview.data if hasattr(preview, "data") else "")
        self.assertTrue(preview.content.startswith(b"%PDF"))

        created = self.client.post(
            "/api/v1/voucher-portal/batches/",
            {**payload, "artwork": _make_image_upload(1987, 725),
             "preview_hash": preview["X-Preview-Hash"]}, format="multipart",
        )
        self.assertEqual(created.status_code, 201, created.data)
        batch = PortalBatch.objects.get(pk=created.data["id"])
        self.assertTrue(batch.artwork.name.startswith("voucher-portal/batches/"))
        self.assertEqual(batch.template_snapshot["artwork_path"], batch.artwork.path)

    def test_changing_batch_artwork_invalidates_the_preview_hash(self):
        dept, vtype, prefix, template = make_reference_data()
        payload = {
            "name": "Changed artwork", "department": dept.id, "voucher_type": vtype.id,
            "quantity": 1, "discount_type": "fixed", "fixed_value": "25", "currency": "AED",
            "valid_to": dates()[1].isoformat(), "prefix": prefix.id, "template": template.id,
        }
        preview = self.client.post(
            "/api/v1/voucher-portal/batches/preview/",
            {**payload, "artwork": _make_image_upload(1987, 725, color=(200, 50, 50))}, format="multipart",
        )
        created = self.client.post(
            "/api/v1/voucher-portal/batches/",
            {**payload, "artwork": _make_image_upload(1987, 725, color=(50, 50, 200)),
             "preview_hash": preview["X-Preview-Hash"]}, format="multipart",
        )
        self.assertEqual(created.status_code, 400)


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


def text_element(**overrides):
    element = {"id": "headline", "type": "text", "name": "Headline", "text": "50% OFF",
               "x": 20, "y": 20, "size": 18, "font": "Helvetica-Bold", "color": "#231B36",
               "align": "left", "line_height": 20}
    element.update(overrides)
    return element


def designed_geometry(*elements):
    """A blank card (barcode only) plus whatever the test is designing."""
    geometry = blank_geometry()
    geometry["elements"].extend(elements)
    return geometry


class GeometryValidationTests(TestCase):
    """The designer is the only thing that can put anything on a printed
    voucher, so a bad layout has to be rejected before it's saved, not
    discovered on a print run."""

    def setUp(self):
        self.user = User.objects.create_user("geo_admin", password="x", is_staff=True)
        self.client = APIClient()
        self.client.force_authenticate(self.user)
        self.template = VoucherTemplate.objects.create(name="Editable", is_default=True)

    def _patch(self, geometry):
        return self.client.patch(f"/api/v1/voucher-portal/templates/{self.template.id}/",
                                 {"field_geometry": geometry}, format="json")

    def test_box_colour_is_set_before_its_transparency(self):
        """ReportLab applies a colour's default alpha when setFillColor runs."""
        pdf_canvas = mock.Mock()
        _draw_box(pdf_canvas, {"x": 0, "y": 0, "w": 100, "h": 50,
                               "fill": "#FFFFFF", "opacity": 0.35})
        calls = [call[0] for call in pdf_canvas.method_calls]
        self.assertLess(calls.index("setFillColor"), calls.index("setFillAlpha"))

    def test_a_new_template_starts_empty_except_for_the_barcode(self):
        self.assertEqual([e["type"] for e in self.template.field_geometry["elements"]], ["barcode"])

    def test_user_added_elements_are_saved(self):
        geometry = designed_geometry(
            text_element(),
            {"id": "valid", "type": "field", "name": "Valid until", "source": "valid_to",
             "prefix": "Valid until ", "x": 20, "y": 60, "size": 9, "font": "Helvetica", "color": "#4A4160"},
            {"id": "panel", "type": "box", "name": "Panel", "x": 5, "y": 5, "w": 140, "h": 100,
             "fill": "#FFFFFF", "opacity": 0.9},
            {"id": "rule", "type": "line", "name": "Divider", "x": 20, "y": 50, "w": 100, "h": 1,
             "color": "#DCD7E8"},
        )
        response = self._patch(geometry)
        self.assertEqual(response.status_code, 200, response.data)
        self.template.refresh_from_db()
        self.assertEqual([e["id"] for e in self.template.field_geometry["elements"]],
                         ["barcode", "headline", "valid", "panel", "rule"])

    def test_valid_move_is_accepted(self):
        geometry = designed_geometry(text_element(x=40, y=25))
        self.assertEqual(self._patch(geometry).status_code, 200)
        self.template.refresh_from_db()
        self.assertEqual(self.template.field_geometry["elements"][1]["x"], 40)

    def test_element_outside_the_card_is_rejected(self):
        response = self._patch(designed_geometry(text_element(y=900)))  # card is only 178pt tall
        self.assertEqual(response.status_code, 400)
        self.assertIn("field_geometry", response.data)

    def test_negative_position_is_rejected(self):
        self.assertEqual(self._patch(designed_geometry(text_element(x=-5))).status_code, 400)

    def test_unknown_element_type_is_rejected(self):
        response = self._patch(designed_geometry(text_element(type="hologram")))
        self.assertEqual(response.status_code, 400)
        self.assertIn("hologram", str(response.data))

    def test_unknown_voucher_field_is_rejected(self):
        response = self._patch(designed_geometry(
            {"id": "f", "type": "field", "source": "customer_loyalty_tier", "x": 10, "y": 10, "size": 8}))
        self.assertEqual(response.status_code, 400)
        self.assertIn("customer_loyalty_tier", str(response.data))

    def test_unknown_font_is_rejected(self):
        """reportlab raises on an unknown face inside the background generation
        thread - long after the layout looked fine on screen."""
        response = self._patch(designed_geometry(text_element(font="Comic Sans")))
        self.assertEqual(response.status_code, 400)
        self.assertIn("Comic Sans", str(response.data))

    def test_malformed_colour_is_rejected(self):
        self.assertEqual(self._patch(designed_geometry(text_element(color="dark purple"))).status_code, 400)

    def test_non_numeric_position_is_rejected(self):
        self.assertEqual(self._patch(designed_geometry(text_element(x="left-ish"))).status_code, 400)

    def test_duplicate_element_id_is_rejected(self):
        response = self._patch(designed_geometry(text_element(), text_element()))
        self.assertEqual(response.status_code, 400)
        self.assertIn("headline", str(response.data))

    def test_empty_text_element_is_rejected(self):
        self.assertEqual(self._patch(designed_geometry(text_element(text="   "))).status_code, 400)

    def test_barcode_is_mandatory(self):
        geometry = blank_geometry()
        geometry["elements"] = [text_element()]
        response = self._patch(geometry)
        self.assertEqual(response.status_code, 400)
        self.assertIn("barcode", str(response.data).lower())

    def test_a_layout_with_nothing_in_it_is_rejected(self):
        response = self._patch({})
        self.assertEqual(response.status_code, 400)
        self.assertIn("barcode", str(response.data).lower())

    def test_barcode_cannot_be_hidden(self):
        geometry = blank_geometry()
        geometry["elements"][0]["hidden"] = True
        response = self._patch(geometry)
        self.assertEqual(response.status_code, 400)
        self.assertIn("mandatory", str(response.data))

    def test_unscannably_small_barcode_is_rejected(self):
        geometry = blank_geometry()
        geometry["elements"][0].update({"w": 12, "h": 3})
        response = self._patch(geometry)
        self.assertEqual(response.status_code, 400)
        self.assertIn("scan", str(response.data))

    def test_hide_if_empty_must_name_a_real_field(self):
        self.assertEqual(self._patch(designed_geometry(text_element(hide_if_empty="nope"))).status_code, 400)
        self.assertEqual(self._patch(designed_geometry(text_element(hide_if_empty="restrictions"))).status_code, 200)

    def test_catalogue_offers_only_what_the_renderer_can_draw(self):
        response = self.client.get("/api/v1/voucher-portal/templates/field-catalogue/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual({entry["type"] for entry in response.data["palette"]},
                         set(response.data["element_types"]))
        self.assertEqual([e["type"] for e in response.data["blank"]["elements"]], ["barcode"])
        self.assertTrue(all(entry["defaults"] for entry in response.data["palette"]))

    def test_catalogue_still_answers_a_browser_from_before_the_designer(self):
        """The page and this API deploy separately. A tab still running the old
        editor reads `fields` and `defaults`; dropping them breaks that tab
        mid-rollout for no reason."""
        response = self.client.get("/api/v1/voucher-portal/templates/field-catalogue/")
        self.assertIn("barcode", {field["key"] for field in response.data["fields"]})
        self.assertEqual(response.data["defaults"]["version"], 2)

    def test_catalogue_starters_are_valid_layouts(self):
        response = self.client.get("/api/v1/voucher-portal/templates/field-catalogue/")
        for starter in response.data["starters"]:
            with self.subTest(starter=starter["key"]):
                self.assertEqual(self._patch(starter["geometry"]).status_code, 200)

    def test_template_preview_renders_without_a_batch(self):
        response = self.client.get(f"/api/v1/voucher-portal/templates/{self.template.id}/preview/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertTrue(response.content.startswith(b"%PDF"))
        # and nothing was persisted to get that preview
        self.assertEqual(PortalBatch.objects.count(), 0)
        self.assertEqual(PortalVoucher.objects.count(), 0)

    def test_template_preview_renders_unsaved_geometry(self):
        response = self.client.post(f"/api/v1/voucher-portal/templates/{self.template.id}/preview/",
                                    {"field_geometry": designed_geometry(text_element(x=60))}, format="json")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.content.startswith(b"%PDF"))
        self.template.refresh_from_db()  # preview must not save the edit
        self.assertEqual(len(self.template.field_geometry["elements"]), 1)

    def test_template_preview_accepts_batch_artwork_with_unsaved_geometry(self):
        geometry = designed_geometry(
            {"id": "panel", "type": "box", "x": 5, "y": 5, "w": 140, "h": 100,
             "fill": "#FFFFFF", "opacity": 0.35},
        )
        response = self.client.post(
            f"/api/v1/voucher-portal/templates/{self.template.id}/preview/",
            {"field_geometry": json.dumps(geometry),
             "artwork": _make_image_upload(1987, 725, color=(80, 40, 160))},
            format="multipart",
        )
        self.assertEqual(response.status_code, 200, response.data if hasattr(response, "data") else "")
        self.assertTrue(response.content.startswith(b"%PDF"))
        self.assertFalse(self.template.artwork)

    def test_template_preview_rejects_invalid_unsaved_geometry(self):
        response = self.client.post(f"/api/v1/voucher-portal/templates/{self.template.id}/preview/",
                                    {"field_geometry": designed_geometry(text_element(y=5000))}, format="json")
        self.assertEqual(response.status_code, 400)

    def test_reset_geometry_empties_the_card(self):
        self._patch(designed_geometry(text_element()))
        response = self.client.post(f"/api/v1/voucher-portal/templates/{self.template.id}/reset-geometry/")
        self.assertEqual(response.status_code, 200)
        self.template.refresh_from_db()
        self.assertEqual(self.template.field_geometry, BLANK_GEOMETRY)

    def test_card_size_that_would_orphan_the_layout_is_rejected(self):
        self._patch(designed_geometry(text_element(x=400, y=150)))
        response = self.client.patch(f"/api/v1/voucher-portal/templates/{self.template.id}/",
                                     {"coupon_width": 200, "coupon_height": 120}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("coupon_width", response.data)

    def test_card_size_can_be_changed_with_a_layout_that_fits(self):
        geometry = blank_geometry(242.6, 153.0)  # barcode already inside the smaller card
        geometry["elements"].append(text_element(x=20, y=20))
        self.assertEqual(self._patch(geometry).status_code, 200)
        response = self.client.patch(f"/api/v1/voucher-portal/templates/{self.template.id}/",
                                     {"coupon_width": 242.6, "coupon_height": 153.0}, format="json")
        self.assertEqual(response.status_code, 200, response.data)

    def test_non_admin_cannot_reset_geometry(self):
        requester = User.objects.create_user("geo_requester", password="x")
        dept = Department.objects.create(code="GEO", name="Geo")
        grant(requester, "requester", [dept])
        client = APIClient()
        client.force_authenticate(requester)
        response = client.post(f"/api/v1/voucher-portal/templates/{self.template.id}/reset-geometry/")
        self.assertEqual(response.status_code, 403)

    def _as(self, role):
        user = User.objects.create_user(f"designer_{role}", password="x")
        dept = Department.objects.create(code=role[:4].upper(), name=role)
        grant(user, role, [dept])
        client = APIClient()
        client.force_authenticate(user)
        return client

    def test_a_batch_creator_can_change_a_card_design(self):
        """Designing is part of creating a batch - the create form offers
        "edit this design" next to the template it picks - so a requester has
        to be able to save one."""
        client = self._as("requester")
        response = client.patch(f"/api/v1/voucher-portal/templates/{self.template.id}/",
                                {"field_geometry": designed_geometry(text_element())}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        self.template.refresh_from_db()
        self.assertEqual(len(self.template.field_geometry["elements"]), 2)

    def test_a_batch_creator_cannot_change_which_design_everyone_else_gets(self):
        client = self._as("requester")
        other = VoucherTemplate.objects.create(name="Someone else's")
        self.assertEqual(client.patch(f"/api/v1/voucher-portal/templates/{other.id}/",
                                      {"is_default": True}, format="json").status_code, 403)
        self.assertEqual(client.patch(f"/api/v1/voucher-portal/templates/{other.id}/",
                                      {"is_active": False}, format="json").status_code, 403)
        other.refresh_from_db()
        self.assertFalse(other.is_default)
        self.assertTrue(other.is_active)

    def test_a_reader_cannot_change_a_card_design(self):
        client = self._as("report_viewer")
        response = client.patch(f"/api/v1/voucher-portal/templates/{self.template.id}/",
                                {"field_geometry": designed_geometry(text_element())}, format="json")
        self.assertEqual(response.status_code, 403)


class CardRenderingTests(TestCase):
    """What the designer saves is what `pdf.py` has to print, including for
    templates and batch snapshots authored before the designer existed."""

    def setUp(self):
        self.dept, self.vtype, self.prefix, self.template = make_reference_data()
        self.requester = User.objects.create_user("render_requester", password="x", is_staff=True)
        self.approver = User.objects.create_user("render_approver", password="x", is_staff=True)

    def _generated_batch(self, **overrides):
        batch = approved_batch(self.dept, self.vtype, self.prefix, self.template,
                               self.requester, self.approver, **overrides)
        generate_vouchers(batch)
        batch.refresh_from_db()
        return batch

    def test_every_offered_variable_resolves_for_a_real_voucher(self):
        """The designer only offers variables the catalogue lists, so anything
        listed has to come back with a value (or a blank) rather than a
        KeyError at print time."""
        batch = self._generated_batch(restrictions="Brands : Sample")
        context = build_context(batch, batch.vouchers.first())
        for variable in VARIABLES:
            self.assertIn(variable["key"], context)
        self.assertEqual(context["voucher_code"], batch.vouchers.first().number)
        self.assertEqual(context["department"], "HR")
        self.assertEqual(context["restrictions"], "Brands : Sample")

    def test_designed_card_renders(self):
        self.template.field_geometry = designed_geometry(
            text_element(),
            {"id": "cap", "type": "field", "source": "discount_cap", "x": 20, "y": 60,
             "size": 8, "font": "Helvetica", "color": "#4A4160"},
            {"id": "terms", "type": "field", "source": "terms", "x": 20, "y": 90, "w": 150,
             "size": 6, "font": "Helvetica", "color": "#6B6480", "line_height": 8, "max_lines": 4},
            {"id": "panel", "type": "box", "x": 5, "y": 5, "w": 140, "h": 100, "fill": "#FFFFFF", "opacity": 0.8},
            {"id": "rule", "type": "line", "x": 20, "y": 55, "w": 100, "h": 0.75, "color": "#DCD7E8"},
        )
        self.template.save()
        batch = self._generated_batch()
        self.assertTrue(build_voucher_pdf(batch, batch.vouchers.first()).startswith(b"%PDF"))
        self.assertTrue(build_batch_pdf(batch, list(batch.vouchers.all())).startswith(b"%PDF"))

    def test_barcode_carries_each_voucher_s_own_unique_number(self):
        batch = self._generated_batch(quantity=5)
        numbers = [v.number for v in batch.vouchers.all()]
        self.assertEqual(len(set(numbers)), 5)
        for voucher in batch.vouchers.all():
            self.assertEqual(build_context(batch, voucher)["voucher_code"], voucher.number)
        # and every rendered card is a distinct document, not five copies of one
        pdfs = {build_voucher_pdf(batch, voucher) for voucher in batch.vouchers.all()}
        self.assertEqual(len(pdfs), 5)

    def _drawn_text(self, batch, geometry):
        """Every string the renderer actually put on the card."""
        batch.template_snapshot = {**batch.template_snapshot, **geometry}
        with mock.patch("voucher_portal.pdf._draw_text") as draw:
            build_voucher_pdf(batch, batch.vouchers.first())
        return [call.args[2] for call in draw.call_args_list]

    def test_hidden_element_is_not_drawn(self):
        batch = self._generated_batch()
        self.assertIn("PRINT ME", self._drawn_text(batch, designed_geometry(text_element(text="PRINT ME"))))
        self.assertNotIn("PRINT ME",
                         self._drawn_text(batch, designed_geometry(text_element(text="PRINT ME", hidden=True))))

    def test_hide_if_empty_drops_a_label_with_nothing_to_label(self):
        label = {"id": "restrictions_label", "type": "text", "text": "Coupon Restrictions :",
                 "x": 5, "y": 106, "size": 5, "font": "Helvetica", "color": "#6B6480",
                 "hide_if_empty": "restrictions"}
        batch = self._generated_batch(restrictions="")
        self.assertNotIn("Coupon Restrictions :", self._drawn_text(batch, designed_geometry(label)))
        batch.restrictions = "Brands : Sample"
        self.assertIn("Coupon Restrictions :", self._drawn_text(batch, designed_geometry(label)))

    def test_a_field_prints_its_prefix_and_suffix_around_the_value(self):
        batch = self._generated_batch()
        drawn = self._drawn_text(batch, designed_geometry(
            {"id": "code", "type": "field", "source": "voucher_code", "prefix": "No. ", "suffix": " *",
             "x": 20, "y": 20, "size": 8, "font": "Courier", "color": "#231B36"}))
        self.assertIn(f"No. {batch.vouchers.first().number} *", drawn)

    def test_an_empty_stored_layout_still_prints_a_barcode(self):
        """Rows stored as `{}` before the model had a real default must not
        produce a card with nothing to scan."""
        converted = to_elements({})
        self.assertEqual([element["type"] for element in converted["elements"]], ["barcode"])

    def test_a_legacy_layout_still_prints(self):
        """Batches generated before the designer existed carry a version 2
        snapshot on the row itself; those have to keep printing."""
        batch = self._generated_batch()
        batch.template_snapshot = {**batch.template_snapshot, **copy.deepcopy(LEGACY_COUPON_GEOMETRY)}
        self.assertTrue(build_voucher_pdf(batch, batch.vouchers.first()).startswith(b"%PDF"))

    def test_legacy_conversion_keeps_positions_and_paint_order(self):
        converted = to_elements(LEGACY_COUPON_GEOMETRY)
        self.assertEqual(converted["version"], 3)
        by_id = {element["id"]: element for element in converted["elements"]}
        legacy = {field["key"]: field for field in LEGACY_COUPON_GEOMETRY["fields"]}
        for key, field in legacy.items():
            self.assertEqual((by_id[key]["x"], by_id[key]["y"]), (field["x"], field["y"]))
        # the panel stays underneath and the barcode on top, as version 2 drew them
        order = [element["id"] for element in converted["elements"]]
        self.assertEqual(order[0], "content_panel")
        self.assertLess(order.index("barcode_plate"), order.index("barcode"))
        # fields that were switched off come back as hidden, not deleted
        self.assertTrue(by_id["recipient_name"]["hidden"])
        # and static wording becomes ordinary editable text
        self.assertEqual(by_id["valid_label"]["text"], "Discount Valid Until :")
        self.assertEqual(by_id["valid_date"]["source"], "valid_to")

    def test_serializer_exposes_the_converted_layout(self):
        self.template.field_geometry = copy.deepcopy(LEGACY_COUPON_GEOMETRY)
        self.template.save()
        client = APIClient()
        client.force_authenticate(self.requester)
        response = client.get(f"/api/v1/voucher-portal/templates/{self.template.id}/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["field_geometry"]["version"], 2)  # untouched on disk
        self.assertEqual(response.data["layout"]["version"], 3)  # editable by the designer
        self.assertIn("barcode", [e["id"] for e in response.data["layout"]["elements"]])

    def test_a_card_bigger_than_its_page_still_lands_on_the_page(self):
        self.template.coupon_width = 700
        self.template.coupon_height = 400
        self.template.field_geometry = blank_geometry(700, 400)
        self.template.save()
        batch = self._generated_batch()
        self.assertTrue(build_voucher_pdf(batch, batch.vouchers.first()).startswith(b"%PDF"))


class AdminTests(TestCase):
    """The admin is where reference data gets seeded and the audit trail gets
    read, so every page has to actually open - a bad `list_display` or a stale
    field name is a 500 nobody notices until they need it."""

    def setUp(self):
        self.dept, self.vtype, self.prefix, self.template = make_reference_data()
        self.requester = User.objects.create_user("admin_requester", password="x", is_staff=True)
        self.approver = User.objects.create_user("admin_approver", password="x", is_staff=True)
        self.batch = approved_batch(self.dept, self.vtype, self.prefix, self.template,
                                    self.requester, self.approver, quantity=2)
        generate_vouchers(self.batch)
        self.superuser = User.objects.create_superuser("root", "root@example.com", "x")
        self.client = Client()
        self.client.force_login(self.superuser)

    def test_every_changelist_opens(self):
        for model in ["department", "vouchertype", "voucherprefix", "vouchertemplate",
                      "portalbatch", "portalvoucher", "statuschange", "notification", "portaluseraccess"]:
            with self.subTest(model=model):
                response = self.client.get(f"/admin/voucher_portal/{model}/")
                self.assertEqual(response.status_code, 200)

    def test_every_change_form_opens(self):
        grant(User.objects.create_user("granted", password="x"), "requester", [self.dept])
        rows = {
            "department": self.dept.pk,
            "vouchertype": self.vtype.pk,
            "voucherprefix": self.prefix.pk,
            "vouchertemplate": self.template.pk,
            "portalbatch": self.batch.pk,
            "portalvoucher": self.batch.vouchers.first().pk,
            "portaluseraccess": PortalUserAccess.objects.first().pk,
        }
        for model, pk in rows.items():
            with self.subTest(model=model):
                response = self.client.get(f"/admin/voucher_portal/{model}/{pk}/change/")
                self.assertEqual(response.status_code, 200)

    def test_the_audit_trail_cannot_be_edited(self):
        change = StatusChange.objects.filter(batch=self.batch).first()
        self.assertIsNotNone(change)
        self.assertEqual(self.client.get(f"/admin/voucher_portal/statuschange/{change.pk}/change/").status_code, 200)
        self.assertNotContains(
            self.client.get(f"/admin/voucher_portal/statuschange/{change.pk}/change/"), "Save and continue")

    def test_batches_and_vouchers_cannot_be_hand_created(self):
        """They only exist correctly when the service layer builds them - a row
        typed in here would have no snapshot and no allocated number."""
        self.assertEqual(self.client.get("/admin/voucher_portal/portalbatch/add/").status_code, 403)
        self.assertEqual(self.client.get("/admin/voucher_portal/portalvoucher/add/").status_code, 403)

    def test_a_broken_layout_cannot_be_saved_from_the_admin(self):
        """The admin doesn't go through the API serializer, so the model itself
        has to hold the line."""
        self.template.field_geometry = designed_geometry(text_element(y=900))
        with self.assertRaises(ValidationError):
            self.template.full_clean()

    def test_the_design_summary_lists_what_is_on_the_card(self):
        """Asserted on the rendered summary markup, not just the page: the raw
        `field_geometry` textarea further down contains the same words, so a
        looser check passes even when the summary is silently erroring (Django
        swallows a ValueError in a readonly callable and prints "-")."""
        self.template.field_geometry = designed_geometry(text_element(text="HEADLINE"))
        self.template.save()
        response = self.client.get(f"/admin/voucher_portal/vouchertemplate/{self.template.pk}/change/")
        self.assertContains(response, "<li>text <b>Headline</b> at 20, 20</li>", html=True)
        self.assertContains(response, "<li>barcode <b>Barcode</b> at 309.5, 128</li>", html=True)

    def test_admin_links_survive_the_production_url_prefix(self):
        """nginx serves the admin under /fms/, so a hardcoded /admin/... link
        would 404. Everything must go through reverse()."""
        # The test client doesn't apply FORCE_SCRIPT_NAME (only the WSGI
        # handler does), so set the prefix the way a real request would.
        original = get_script_prefix()
        set_script_prefix("/fms/")
        try:
            response = self.client.get("/admin/voucher_portal/portalbatch/")
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, "/fms/admin/voucher_portal/portalvoucher/?batch__id__exact=")
        finally:
            set_script_prefix(original)

    def test_making_a_design_default_clears_the_others(self):
        other = VoucherTemplate.objects.create(name="Other")
        response = self.client.post("/admin/voucher_portal/vouchertemplate/", {
            "action": "make_default", "_selected_action": [str(other.pk)],
        }, follow=True)
        self.assertEqual(response.status_code, 200)
        other.refresh_from_db(); self.template.refresh_from_db()
        self.assertTrue(other.is_default)
        self.assertFalse(self.template.is_default)


class BatchFieldCoverageTests(TestCase):
    """Everything the create-batch form asks for has to be placeable on a card.

    A field collected from the requester but with nowhere to print is a value
    that silently never reaches the voucher, so this pins the mapping: add a
    field to the form and you must add the placeholder to go with it."""

    # create-form field -> the variable a designer picks to print it
    FORM_FIELD_PLACEHOLDERS = {
        "name": "batch_name",
        "quantity": "quantity",
        "department": "department",
        "voucher_type": "voucher_type",
        "description": "description",
        "discount_type": "discount_type",
        "percentage_value": "discount_numeral",
        "fixed_value": "discount_numeral",
        "max_discount_value": "max_discount_value",
        "currency": "currency",
        "valid_from": "valid_from",
        "valid_to": "valid_to",
        "restrictions": "restrictions",
        "terms": "terms",
        "prefix": "prefix",
        # `template` is the card being printed on, not a value printed on it.
        "template": None,
        # Artwork changes the visual rather than providing a printable text variable.
        "artwork": None,
    }

    def test_every_create_form_field_has_a_placeholder(self):
        from .serializers import BatchFormSerializer
        self.assertEqual(set(BatchFormSerializer().fields), set(self.FORM_FIELD_PLACEHOLDERS),
                         "the create form changed - map the new field to a placeholder (or to None)")
        for field, variable in self.FORM_FIELD_PLACEHOLDERS.items():
            if variable is not None:
                with self.subTest(field=field):
                    self.assertIn(variable, VARIABLE_KEYS)

    def test_those_placeholders_carry_the_submitted_values(self):
        dept, vtype, prefix, template = make_reference_data()
        requester = User.objects.create_user("coverage_requester", password="x", is_staff=True)
        approver = User.objects.create_user("coverage_approver", password="x", is_staff=True)
        batch = approved_batch(dept, vtype, prefix, template, requester, approver,
                               quantity=7, restrictions="Brands : Sample", terms="No cash value")
        generate_vouchers(batch)
        context = build_context(batch, batch.vouchers.first())
        self.assertEqual(context["quantity"], "7")
        self.assertEqual(context["prefix"], "EMP")
        self.assertEqual(context["batch_name"], batch.name)
        self.assertEqual(context["discount_type"], "Fixed amount")
        self.assertEqual(context["terms"], "No cash value")


class CorsTests(TestCase):
    """The browser calls this API cross-origin from Amplify, so an origin the
    server doesn't recognise fails as a CORS error on the UI with nothing in
    the API logs to explain it."""

    AMPLIFY = "^https://[a-z0-9-]+[.]d12iaal63qqmzf[.]amplifyapp[.]com$"

    def setUp(self):
        make_reference_data()
        self.user = User.objects.create_user("cors_user", password="x", is_staff=True)
        self.client = APIClient()
        self.client.force_authenticate(self.user)

    def _origin(self, origin):
        response = self.client.get("/api/v1/voucher-portal/templates/", HTTP_ORIGIN=origin)
        self.assertEqual(response.status_code, 200)
        return response.headers.get("access-control-allow-origin")

    @override_settings(CORS_ALLOWED_ORIGINS=["https://track.phloz.app"], CORS_ALLOWED_ORIGIN_REGEXES=[])
    def test_an_origin_that_is_not_configured_gets_no_cors_header(self):
        """What the Amplify UI hit: the deployed origin list had only the
        console in it."""
        self.assertIsNone(self._origin("https://main.d12iaal63qqmzf.amplifyapp.com"))
        self.assertEqual(self._origin("https://track.phloz.app"), "https://track.phloz.app")

    @override_settings(CORS_ALLOWED_ORIGINS=[], CORS_ALLOWED_ORIGIN_REGEXES=[AMPLIFY])
    def test_every_branch_of_the_amplify_app_is_allowed_by_pattern(self):
        for origin in ["https://main.d12iaal63qqmzf.amplifyapp.com",
                       "https://claude-voucher-x.d12iaal63qqmzf.amplifyapp.com"]:
            with self.subTest(origin=origin):
                self.assertEqual(self._origin(origin), origin)

    @override_settings(CORS_ALLOWED_ORIGINS=[], CORS_ALLOWED_ORIGIN_REGEXES=[AMPLIFY])
    def test_the_pattern_is_anchored_against_lookalike_domains(self):
        for origin in ["https://main.d12iaal63qqmzf.amplifyapp.com.evil.example",
                       "https://main.someoneelse.amplifyapp.com",
                       "http://main.d12iaal63qqmzf.amplifyapp.com"]:
            with self.subTest(origin=origin):
                self.assertIsNone(self._origin(origin))


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
        self._production_bucket = storage._S3_BUCKET
        storage._S3_BUCKET = ""  # exercise the local storage adapter in tests
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

    def tearDown(self):
        storage._S3_BUCKET = self._production_bucket

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

    def test_batch_csv_export_contains_every_voucher_detail(self):
        voucher = self.batch.vouchers.first()
        voucher.issue(name="Sample User", phone="+971500000000", email="sample@example.com",
                      reference="REF-001", actor=self.requester)
        response = self.client.get(f"/api/v1/voucher-portal/batches/{self.batch.id}/export-csv/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/csv", response["Content-Type"])
        self.assertIn("attachment", response["Content-Disposition"])
        body = response.content.decode("utf-8-sig")
        self.assertEqual(body.count("\n"), self.batch.quantity + 1)
        self.assertIn("voucher_number", body)
        self.assertIn(voucher.number, body)
        self.assertIn("Sample User", body)
        self.assertIn("REF-001", body)

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
