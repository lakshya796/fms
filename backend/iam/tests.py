"""Tests for organisation structure and role based access."""
from django.contrib.auth.models import User
from django.test import TestCase
from rest_framework.test import APIClient

from .models import AuditLog, Branch, PERMISSION_CODES, Role, UserProfile


class IamTestCase(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser("owner", "owner@example.com", "sup3r-secret-pass")
        self.client = APIClient()
        self.client.force_authenticate(self.admin)
        self.branch = Branch.objects.create(name="Bhiwandi depot", code="BHW", city="Bhiwandi", state="Maharashtra")
        self.dispatcher_role = Role.objects.create(
            name="Dispatcher", code="dispatcher",
            permissions=["operations.view", "operations.manage", "masters.view"])


class UserManagementTests(IamTestCase):
    def test_creating_a_user_sets_a_usable_password(self):
        response = self.client.post("/api/v1/iam/users/", {
            "username": "ramesh.d", "first_name": "Ramesh", "email": "ramesh@example.com",
            "password": "dispatch-desk-2026", "employee_code": "EMP-001",
            "designation": "Dispatch executive", "branch": self.branch.id, "role": self.dispatcher_role.id},
            format="json")
        self.assertEqual(response.status_code, 201, response.data)
        created = User.objects.get(username="ramesh.d")
        self.assertTrue(created.check_password("dispatch-desk-2026"))
        self.assertNotIn("password", response.data)

    def test_weak_passwords_are_rejected(self):
        response = self.client.post("/api/v1/iam/users/", {
            "username": "weak", "password": "12345678", "employee_code": "EMP-002"}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("password", response.data)

    def test_duplicate_username_is_rejected(self):
        self.client.post("/api/v1/iam/users/", {"username": "dup", "password": "a-good-long-pass-2026",
                                                "employee_code": "EMP-003"}, format="json")
        again = self.client.post("/api/v1/iam/users/", {"username": "dup", "password": "a-good-long-pass-2026",
                                                        "employee_code": "EMP-004"}, format="json")
        self.assertEqual(again.status_code, 400)

    def test_deactivate_blocks_the_login_and_cannot_target_yourself(self):
        created = self.client.post("/api/v1/iam/users/", {
            "username": "temp.staff", "password": "temp-staff-pass-2026", "employee_code": "EMP-005"}, format="json")
        profile_id = created.data["id"]
        response = self.client.post(f"/api/v1/iam/users/{profile_id}/deactivate/")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.get(username="temp.staff").is_active)

        own_profile = UserProfile.objects.create(user=self.admin, employee_code="EMP-OWNER")
        blocked = self.client.post(f"/api/v1/iam/users/{own_profile.id}/deactivate/")
        self.assertEqual(blocked.status_code, 400)

    def test_role_rejects_unknown_permissions(self):
        response = self.client.post("/api/v1/iam/roles/", {
            "name": "Bad role", "code": "bad", "permissions": ["operations.view", "nonsense.destroy"]}, format="json")
        self.assertEqual(response.status_code, 400)
        self.assertIn("nonsense.destroy", str(response.data))

    def test_permission_catalogue_is_published_for_the_role_editor(self):
        response = self.client.get("/api/v1/iam/permissions/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), len(PERMISSION_CODES))
        self.assertIn("group", response.data[0])

    def test_me_reports_identity_branch_and_permissions(self):
        UserProfile.objects.create(user=self.admin, employee_code="EMP-OWNER", branch=self.branch)
        response = self.client.get("/api/v1/iam/me/")
        self.assertEqual(response.data["username"], "owner")
        self.assertEqual(response.data["branch"], "Bhiwandi depot")
        self.assertEqual(sorted(response.data["permissions"]), sorted(PERMISSION_CODES))


class AccessControlTests(IamTestCase):
    def staff_client(self, role, permissions=None):
        user = User.objects.create_user(f"staff-{role.code}", password="staff-login-pass-2026")
        UserProfile.objects.create(user=user, employee_code=f"E-{role.code}", role=role, branch=self.branch)
        client = APIClient()
        client.force_authenticate(user)
        return client

    def test_a_role_without_accounting_cannot_reach_the_ledger(self):
        client = self.staff_client(self.dispatcher_role)
        self.assertEqual(client.get("/api/v1/accounting/accounts/").status_code, 403)
        self.assertEqual(client.get("/api/v1/iam/users/").status_code, 403)

    def test_a_role_with_accounting_view_can_read_but_not_write(self):
        role = Role.objects.create(name="Accounts viewer", code="acc_view", permissions=["accounting.view"])
        client = self.staff_client(role)
        self.assertEqual(client.get("/api/v1/accounting/accounts/").status_code, 200)
        created = client.post("/api/v1/accounting/accounts/",
                              {"code": "9999", "name": "Sneaky", "account_type": "expense"}, format="json")
        self.assertEqual(created.status_code, 403)

    def test_superuser_bypasses_role_checks(self):
        self.assertEqual(self.client.get("/api/v1/accounting/accounts/").status_code, 200)

    def test_a_login_without_a_role_keeps_working(self):
        """Existing deployments must not break the moment RBAC ships."""
        legacy = User.objects.create_user("legacy.user", password="legacy-login-pass-2026")
        client = APIClient()
        client.force_authenticate(legacy)
        self.assertEqual(client.get("/api/v1/accounting/accounts/").status_code, 200)

    def test_a_deactivated_profile_is_refused(self):
        user = User.objects.create_user("suspended", password="suspended-pass-2026")
        UserProfile.objects.create(user=user, employee_code="E-SUS", role=self.dispatcher_role, status="inactive")
        client = APIClient()
        client.force_authenticate(user)
        self.assertEqual(client.get("/api/v1/orders/").status_code, 403)


class AuditTrailTests(IamTestCase):
    def test_writes_are_recorded_with_the_actor(self):
        self.client.post("/api/v1/iam/branches/", {"name": "Chakan depot", "code": "CKN", "city": "Chakan"}, format="json")
        entry = AuditLog.objects.filter(entity="Branch").first()
        self.assertIsNotNone(entry)
        self.assertEqual(entry.action, "create")
        self.assertEqual(entry.username, "owner")

    def test_audit_log_is_read_only_over_the_api(self):
        self.client.post("/api/v1/iam/branches/", {"name": "Depot", "code": "DPT", "city": "Pune"}, format="json")
        listed = self.client.get("/api/v1/iam/audit-log/")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(self.client.post("/api/v1/iam/audit-log/", {}, format="json").status_code, 405)
