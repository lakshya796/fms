from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from .models import GiftVoucher, VoucherBatch
from .services import SeriesError, generate_batch, parse_series


def dates(days_from=0, days_to=365):
    today = timezone.localdate()
    return today + timedelta(days=days_from), today + timedelta(days=days_to)


class ParseSeriesTests(TestCase):
    def test_valid_range(self):
        prefix, padding, start, end = parse_series("GV-2026-000101", "GV-2026-000150")
        self.assertEqual(prefix, "GV-2026-")
        self.assertEqual(padding, 6)
        self.assertEqual((start, end), (101, 150))

    def test_mismatched_prefix(self):
        with self.assertRaises(SeriesError):
            parse_series("GV-2026-000101", "GV-2027-000150")

    def test_mismatched_digit_width(self):
        with self.assertRaises(SeriesError):
            parse_series("GV-2026-000101", "GV-2026-99")

    def test_end_before_start(self):
        with self.assertRaises(SeriesError):
            parse_series("GV-2026-000150", "GV-2026-000101")

    def test_over_cap(self):
        with self.assertRaises(SeriesError):
            parse_series("GV-000001", "GV-002000")

    def test_missing_digits(self):
        with self.assertRaises(SeriesError):
            parse_series("GV-ABC", "GV-DEF")

    def test_blank_input(self):
        with self.assertRaises(SeriesError):
            parse_series("", "GV-000010")


class GenerateBatchTests(TestCase):
    def test_generates_every_voucher_in_range(self):
        valid_from, valid_to = dates()
        batch = generate_batch(
            start="GV-2026-000001", end="GV-2026-000005", value="100.00",
            currency="AED", valid_from=valid_from, valid_to=valid_to)
        self.assertEqual(batch.count, 5)
        self.assertEqual(GiftVoucher.objects.filter(batch=batch).count(), 5)
        self.assertTrue(GiftVoucher.objects.filter(number="GV-2026-000001").exists())
        self.assertTrue(GiftVoucher.objects.filter(number="GV-2026-000005").exists())
        voucher = GiftVoucher.objects.get(number="GV-2026-000003")
        self.assertEqual(voucher.status, "unassigned")
        self.assertEqual(voucher.value, batch.value)

    def test_rejects_overlapping_range(self):
        valid_from, valid_to = dates()
        generate_batch(start="GV-100", end="GV-105", value="50", currency="AED",
                       valid_from=valid_from, valid_to=valid_to)
        with self.assertRaises(SeriesError):
            generate_batch(start="GV-103", end="GV-110", value="50", currency="AED",
                           valid_from=valid_from, valid_to=valid_to)


class VoucherApiTests(TestCase):
    def setUp(self):
        self.client = APIClient()

    def test_endpoints_are_public(self):
        response = self.client.get("/api/v1/vouchers/vouchers/")
        self.assertEqual(response.status_code, 200)

    def test_create_batch_via_api(self):
        valid_from, valid_to = dates()
        response = self.client.post("/api/v1/vouchers/batches/", {
            "start": "GV-2026-000201",
            "end": "GV-2026-000210",
            "value": "250.00",
            "currency": "AED",
            "valid_from": valid_from.isoformat(),
            "valid_to": valid_to.isoformat(),
            "created_by": "Store Manager",
        }, format="json")
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data["count"], 10)
        self.assertEqual(GiftVoucher.objects.count(), 10)

    def test_create_batch_rejects_bad_dates(self):
        valid_from, valid_to = dates()
        response = self.client.post("/api/v1/vouchers/batches/", {
            "start": "GV-300", "end": "GV-305", "value": "10",
            "valid_from": valid_to.isoformat(), "valid_to": valid_from.isoformat(),
        }, format="json")
        self.assertEqual(response.status_code, 400)

    def test_issue_voucher_with_phone(self):
        valid_from, valid_to = dates()
        batch = generate_batch(start="GV-400", end="GV-401", value="75", currency="AED",
                               valid_from=valid_from, valid_to=valid_to)
        voucher = batch.vouchers.first()
        response = self.client.post(f"/api/v1/vouchers/vouchers/{voucher.id}/issue/",
                                    {"phone_number": "+971501234567"}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        voucher.refresh_from_db()
        self.assertEqual(voucher.status, "issued")
        self.assertEqual(voucher.phone_number, "+971501234567")
        self.assertIsNotNone(voucher.issued_at)

    def test_issue_voucher_without_phone_is_allowed(self):
        valid_from, valid_to = dates()
        batch = generate_batch(start="GV-500", end="GV-501", value="75", currency="AED",
                               valid_from=valid_from, valid_to=valid_to)
        voucher = batch.vouchers.first()
        response = self.client.post(f"/api/v1/vouchers/vouchers/{voucher.id}/issue/", {}, format="json")
        self.assertEqual(response.status_code, 200, response.data)
        voucher.refresh_from_db()
        self.assertEqual(voucher.status, "issued")
        self.assertEqual(voucher.phone_number, "")

    def test_cannot_issue_twice(self):
        valid_from, valid_to = dates()
        batch = generate_batch(start="GV-600", end="GV-601", value="75", currency="AED",
                               valid_from=valid_from, valid_to=valid_to)
        voucher = batch.vouchers.first()
        voucher.issue("+971500000000")
        response = self.client.post(f"/api/v1/vouchers/vouchers/{voucher.id}/issue/", {}, format="json")
        self.assertEqual(response.status_code, 400)

    def test_cannot_issue_expired_voucher(self):
        valid_from = timezone.localdate() - timedelta(days=30)
        valid_to = timezone.localdate() - timedelta(days=1)
        batch = generate_batch(start="GV-700", end="GV-701", value="75", currency="AED",
                               valid_from=valid_from, valid_to=valid_to)
        voucher = batch.vouchers.first()
        response = self.client.post(f"/api/v1/vouchers/vouchers/{voucher.id}/issue/", {}, format="json")
        self.assertEqual(response.status_code, 400)

    def test_pdf_download(self):
        valid_from, valid_to = dates()
        batch = generate_batch(start="GV-800", end="GV-801", value="120", currency="AED",
                               valid_from=valid_from, valid_to=valid_to)
        voucher = batch.vouchers.first()
        response = self.client.get(f"/api/v1/vouchers/vouchers/{voucher.id}/pdf/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertTrue(response.content.startswith(b"%PDF"))

    def test_summary_counts(self):
        valid_from, valid_to = dates()
        batch = generate_batch(start="GV-900", end="GV-904", value="10", currency="AED",
                               valid_from=valid_from, valid_to=valid_to)
        vouchers = list(batch.vouchers.all())
        vouchers[0].issue("+971501111111")
        response = self.client.get("/api/v1/vouchers/vouchers/summary/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["total"], 5)
        self.assertEqual(response.data["issued"], 1)
        self.assertEqual(response.data["unassigned"], 4)

    def test_lookup_by_number(self):
        valid_from, valid_to = dates()
        generate_batch(start="GV-950", end="GV-951", value="10", currency="AED",
                       valid_from=valid_from, valid_to=valid_to)
        response = self.client.get("/api/v1/vouchers/lookup/gv-950/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["number"], "GV-950")

    def test_lookup_unknown_number(self):
        response = self.client.get("/api/v1/vouchers/lookup/GV-NOPE/")
        self.assertEqual(response.status_code, 404)

    def test_filter_by_status(self):
        valid_from, valid_to = dates()
        batch = generate_batch(start="GV-960", end="GV-963", value="10", currency="AED",
                               valid_from=valid_from, valid_to=valid_to)
        list(batch.vouchers.all())[0].issue()
        response = self.client.get("/api/v1/vouchers/vouchers/?status=issued")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 1)

    def test_filter_by_expired_status_ignores_stored_status(self):
        past_from, past_to = dates(days_from=-60, days_to=-1)
        future_from, future_to = dates()
        expired_batch = generate_batch(start="GV-970", end="GV-971", value="10", currency="AED",
                                       valid_from=past_from, valid_to=past_to)
        live_batch = generate_batch(start="GV-980", end="GV-981", value="10", currency="AED",
                                    valid_from=future_from, valid_to=future_to)
        list(expired_batch.vouchers.all())[0].issue()

        response = self.client.get("/api/v1/vouchers/vouchers/?status=expired")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 2)
        numbers = {row["number"] for row in response.data["results"]}
        self.assertEqual(numbers, {"GV-970", "GV-971"})

        response = self.client.get("/api/v1/vouchers/vouchers/?status=issued")
        self.assertEqual(response.data["count"], 0)

        response = self.client.get("/api/v1/vouchers/vouchers/?status=unassigned")
        self.assertEqual(response.data["count"], 2)
        numbers = {row["number"] for row in response.data["results"]}
        self.assertEqual(numbers, {"GV-980", "GV-981"})
