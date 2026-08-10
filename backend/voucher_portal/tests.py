import copy
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
from .geometry import (BLANK_GEOMETRY, LEGACY_ADCOOP_GEOMETRY, VARIABLE_KEYS, VARIABLES,
                       blank_geometry, to_elements)
from .pdf import build_batch_pdf, build_context, build_voucher_pdf
from .models import (Department, Notification, PortalBatch, PortalUserAccess, PortalVoucher, StatusChange, VoucherPrefix,
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
    checks every returned code is unique - the row lock is what Â§4 requires.

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
        self.mkt_prefix = VoucherPrefix.objects.create(prefix="MKT", label="Marketing", departm…12099 tokens truncated…
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

