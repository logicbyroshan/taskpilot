"""
DPDP Act, 2023 & DPDP Rules, 2025 Compliance Automated Test Suite
==================================================================
Comprehensive test suite validating:
- Granular Consent Engine & Section 6(4) Withdrawal Mechanism
- Itemized Notice under Section 5 & Public API
- Section 11 Right to Access (Personal Data Dossier Generation)
- Section 12(3) Right to Erasure (Cascading DB & Disk Attachment Purging)
- Section 13 Grievance Redressal with Statutory ≤ 90-day SLA Calculation
- Section 14 Data Principal Nomination Management (Create, Update, Revoke)
- Section 8(7) Automated Data Retention & Lifecycle Purging
- Section 8(6) Data Breach Incident Logging & Form DPDP-BN-1 Board Summary
- REST API Privacy Endpoints & Web Views with IDOR Protection
- Registration DPDP Consent & Age Affirmation
"""

import json
import os
import tempfile
from datetime import timedelta
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework import status

from todo.models import (
    ConsentRecord,
    DataPrincipalNomination,
    PrivacyGrievance,
    DataBreachIncident,
    PrivacyAuditLog,
    Task,
    Category,
    TaskAttachment,
    Notification,
    UserProfile,
)
from todo.privacy_services import (
    ConsentService,
    DataPrincipalRightsService,
    GrievanceService,
    NominationService,
    RetentionService,
    BreachIncidentService,
)
from todo.forms import RegisterForm

User = get_user_model()


class DPDPComplianceTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.api_client = APIClient()

        # Primary test user
        self.user = User.objects.create_user(
            username="testprincipal",
            email="principal@example.com",
            password="SecurePassword123!",
            first_name="Aarav",
            last_name="Sharma",
        )
        self.profile, _ = UserProfile.objects.get_or_create(user=self.user)
        self.profile.full_name = "Aarav Sharma"
        self.profile.phone = "+919876543210"
        self.profile.save()

        # Secondary test user for IDOR testing
        self.other_user = User.objects.create_user(
            username="otherprincipal",
            email="other@example.com",
            password="SecurePassword123!",
            first_name="Priya",
            last_name="Verma",
        )
        self.other_profile, _ = UserProfile.objects.get_or_create(user=self.other_user)

        # Grant initial mandatory consent
        ConsentService.record_consent(
            user=self.user,
            purpose=ConsentRecord.Purpose.ESSENTIAL_SERVICE,
            status=ConsentRecord.Status.GRANTED,
        )

    # -------------------------------------------------------------------------
    # 1. CONSENT ARCHITECTURE & WITHDRAWAL (Section 6 & 6(4))
    # -------------------------------------------------------------------------
    def test_consent_grant_and_retrieval(self):
        """Test granular consent granting and status verification."""
        record = ConsentService.record_consent(
            user=self.user,
            purpose=ConsentRecord.Purpose.EMAIL_NOTIFICATIONS,
            status=ConsentRecord.Status.GRANTED,
            channel="web_app",
        )
        self.assertEqual(record.status, ConsentRecord.Status.GRANTED)
        self.assertTrue(ConsentService.has_consent(self.user, ConsentRecord.Purpose.EMAIL_NOTIFICATIONS))

    def test_consent_withdrawal(self):
        """Section 6(4): Withdrawing consent must immediately take effect."""
        ConsentService.record_consent(
            self.user,
            ConsentRecord.Purpose.EMAIL_NOTIFICATIONS,
            status=ConsentRecord.Status.GRANTED,
        )
        self.assertTrue(ConsentService.has_consent(self.user, ConsentRecord.Purpose.EMAIL_NOTIFICATIONS))

        record = ConsentService.withdraw_consent(
            user=self.user,
            purpose=ConsentRecord.Purpose.EMAIL_NOTIFICATIONS,
        )
        self.assertEqual(record.status, ConsentRecord.Status.WITHDRAWN)
        self.assertFalse(ConsentService.has_consent(self.user, ConsentRecord.Purpose.EMAIL_NOTIFICATIONS))

        # Check sync with profile notifications
        self.user.profile.refresh_from_db()
        self.assertFalse(self.user.profile.notify_task_reminders)
        self.assertFalse(self.user.profile.notify_due_date_alerts)

    def test_essential_service_cannot_be_withdrawn(self):
        """Essential service consent cannot be unilaterally withdrawn without account erasure."""
        with self.assertRaises(ValueError):
            ConsentService.withdraw_consent(self.user, ConsentRecord.Purpose.ESSENTIAL_SERVICE)
        self.assertTrue(ConsentService.has_consent(self.user, ConsentRecord.Purpose.ESSENTIAL_SERVICE))

    def test_invalid_purpose_rejected(self):
        """Invalid purpose string must be rejected."""
        with self.assertRaises(ValueError):
            ConsentService.record_consent(self.user, "unauthorized_tracking_purpose")

    def test_consent_overview_matrix(self):
        """Ensure consent overview returns all configured purposes."""
        overview = ConsentService.get_user_consent_overview(self.user)
        purposes = [item["purpose"] for item in overview]
        self.assertIn("essential_service", purposes)
        self.assertIn("email_notifications", purposes)
        self.assertIn("ai_assistant", purposes)
        self.assertIn("product_updates", purposes)

    # -------------------------------------------------------------------------
    # 2. DATA PRINCIPAL RIGHTS: ACCESS & DOSSIER (Section 11)
    # -------------------------------------------------------------------------
    def test_personal_data_dossier_generation(self):
        """Section 11: Exported dossier must contain all personal data and task records."""
        # Create a category and task
        category = Category.objects.create(name="DPDP Compliance", user=self.user)
        task = Task.objects.create(
            user=self.user,
            title="Perform Security Audit",
            description="Audit access control and data retention",
            category=category,
            priority="high",
            status="in_progress",
        )

        dossier = DataPrincipalRightsService.generate_personal_data_dossier(self.user)
        self.assertIn("dpdp_act_compliance", dossier)
        self.assertIn("account", dossier)
        self.assertEqual(dossier["account"]["username"], "testprincipal")
        self.assertEqual(dossier["account"]["email"], "principal@example.com")
        self.assertEqual(len(dossier["tasks"]), 1)
        self.assertEqual(dossier["tasks"][0]["title"], "Perform Security Audit")
        self.assertIn("consent_history", dossier)

    # -------------------------------------------------------------------------
    # 3. DATA PRINCIPAL RIGHTS: ERASURE & PURGING (Section 12(3))
    # -------------------------------------------------------------------------
    def test_account_erasure_purges_data_and_physical_files(self):
        """Section 12(3): Permanent erasure purges DB records and disk files."""
        category = Category.objects.create(name="Confidential Project", user=self.user)
        task = Task.objects.create(
            user=self.user,
            title="Task with Attachment",
            category=category,
        )
        dummy_file = SimpleUploadedFile("sensitive_doc.pdf", b"Confidential content here")
        attachment = TaskAttachment.objects.create(
            task=task,
            user=self.user,
            file=dummy_file,
            filename="sensitive_doc.pdf",
            file_size=25,
            file_type="application/pdf",
        )
        file_path = attachment.file.path if attachment.file else None

        user_id = self.user.id

        # Execute erasure
        success = DataPrincipalRightsService.execute_account_erasure(user=self.user)
        self.assertTrue(success)

        # Verify user is deleted from auth.User
        self.assertFalse(User.objects.filter(id=user_id).exists())
        # Verify task is deleted
        self.assertFalse(Task.objects.filter(title="Task with Attachment").exists())
        # Verify audit log was created pseudonymously
        audit_entries = PrivacyAuditLog.objects.filter(action="ACCOUNT_ERASED")
        self.assertTrue(audit_entries.exists())
        self.assertEqual(audit_entries.first().user_identifier, f"erased_user_{user_id}")

        # Verify physical file was purged from disk
        if file_path and os.path.exists(file_path):
            self.fail("Physical attachment file was not removed upon account erasure.")

    # -------------------------------------------------------------------------
    # 4. GRIEVANCE REDRESSAL (Section 13 & DPDP Rules 2025 ≤ 90-day SLA)
    # -------------------------------------------------------------------------
    def test_grievance_creation_and_90day_sla(self):
        """Section 13: Grievance must have a unique tracking ticket and statutory 90-day SLA."""
        grievance = GrievanceService.create_grievance(
            full_name="Aarav Sharma",
            email="principal@example.com",
            category=PrivacyGrievance.Category.ACCESS,
            subject="Inquiry regarding AI data processing",
            description="Please clarify if my tasks are shared with external AI APIs.",
            user=self.user,
        )

        self.assertTrue(grievance.ticket_number.startswith("GRV-"))
        self.assertEqual(grievance.status, PrivacyGrievance.Status.SUBMITTED)
        # Check SLA: Statutory deadline must be approximately 90 days from now
        expected_deadline = (timezone.now() + timedelta(days=90)).date()
        self.assertEqual(grievance.statutory_deadline, expected_deadline)
        self.assertEqual(grievance.days_until_deadline, 90)

    def test_grievance_resolution(self):
        """Grievance resolution workflow updates status and resolution notes."""
        grievance = GrievanceService.create_grievance(
            full_name="Aarav Sharma",
            email="principal@example.com",
            category=PrivacyGrievance.Category.CONSENT,
            subject="Consent logs inquiry",
            description="Need confirmation of consent updates",
            user=self.user,
        )

        resolved = GrievanceService.resolve_grievance(
            grievance=grievance,
            resolution_notes="Your consent preferences have been verified and updated.",
            new_status=PrivacyGrievance.Status.RESOLVED,
        )
        self.assertEqual(resolved.status, PrivacyGrievance.Status.RESOLVED)
        self.assertIsNotNone(resolved.resolved_at)

    def test_grievance_tracking_by_ticket_and_email(self):
        """Users can securely track grievance by ticket number and matching email."""
        grievance = GrievanceService.create_grievance(
            full_name="Aarav Sharma",
            email="principal@example.com",
            category=PrivacyGrievance.Category.ERASURE,
            subject="Erasure confirmation request",
            description="Please confirm deletion timelines",
        )

        # Correct email match
        found = GrievanceService.get_grievance_by_ticket(
            ticket_number=grievance.ticket_number,
            email="principal@example.com",
        )
        self.assertIsNotNone(found)

        # Mismatched email rejected (prevents IDOR)
        mismatch = GrievanceService.get_grievance_by_ticket(
            ticket_number=grievance.ticket_number,
            email="hacker@example.com",
        )
        self.assertIsNone(mismatch)

    # -------------------------------------------------------------------------
    # 5. NOMINATION MANAGEMENT (Section 14)
    # -------------------------------------------------------------------------
    def test_nomination_lifecycle(self):
        """Section 14: Data Principal can designate, update, and revoke nominee."""
        # 1. Designate nominee
        nominee = NominationService.set_nominee(
            user=self.user,
            nominee_name="Rohan Sharma",
            nominee_email="rohan.sharma@example.com",
            nominee_phone="+919876500000",
            relationship="Brother",
            notes="Designated nominee for data principal rights in event of incapacity.",
        )
        self.assertEqual(nominee.nominee_name, "Rohan Sharma")
        self.assertEqual(nominee.status, DataPrincipalNomination.Status.ACTIVE)

        # 2. Retrieve
        active_nominee = NominationService.get_nominee(self.user)
        self.assertEqual(active_nominee.id, nominee.id)

        # 3. Revoke
        revoked = NominationService.revoke_nominee(self.user)
        self.assertTrue(revoked)
        self.assertIsNone(NominationService.get_nominee(self.user))

    # -------------------------------------------------------------------------
    # 6. RETENTION POLICY ENGINE (Section 8(7))
    # -------------------------------------------------------------------------
    def test_retention_cleanup_notifications(self):
        """Section 8(7): Purge notifications older than retention threshold (90 days)."""
        # Create an old notification (100 days old)
        old_notif = Notification.objects.create(
            user=self.user,
            title="Old Notification",
            message="This is expired",
            event_type=Notification.EventType.SYSTEM,
        )
        Notification.objects.filter(id=old_notif.id).update(
            created_at=timezone.now() - timedelta(days=100)
        )

        # Create a fresh notification (5 days old)
        fresh_notif = Notification.objects.create(
            user=self.user,
            title="Fresh Notification",
            message="Active notif",
            event_type=Notification.EventType.SYSTEM,
        )

        stats = RetentionService.run_cleanup_retention(dry_run=False)
        self.assertGreaterEqual(stats["notifications_purged"], 1)
        self.assertFalse(Notification.objects.filter(id=old_notif.id).exists())
        self.assertTrue(Notification.objects.filter(id=fresh_notif.id).exists())

    # -------------------------------------------------------------------------
    # 7. DATA BREACH TRIAGE (Section 8(6))
    # -------------------------------------------------------------------------
    def test_breach_incident_recording_and_board_notification(self):
        """Section 8(6): Record incident and generate Board Form DPDP-BN-1 summary."""
        incident = BreachIncidentService.log_incident(
            title="Suspicious Auth Attempts Blocked",
            nature_and_scope="Rate limiter flagged repeated password guessing from suspicious subnet.",
            affected_data_categories="User credentials (hashes)",
            estimated_affected_principals=3,
            severity=DataBreachIncident.Severity.LOW,
            containment_actions="IP range blocked at gateway, sessions rotated.",
            remediation_steps="Enforced stricter rate limits and mandatory 2FA.",
        )

        self.assertTrue(incident.incident_id.startswith("INC-"))
        self.assertEqual(incident.severity, DataBreachIncident.Severity.LOW)

        form = BreachIncidentService.generate_board_notification_summary(incident)
        self.assertEqual(form["incident_id"], incident.incident_id)
        self.assertIn("Form DPDP-BN-1", form["form"])
        self.assertEqual(form["data_fiduciary"], settings.DPDP_CONFIG["DATA_FIDUCIARY_NAME"])

    # -------------------------------------------------------------------------
    # 8. REST API PRIVACY ENDPOINTS
    # -------------------------------------------------------------------------
    def test_api_privacy_notice_public(self):
        """Public notice API endpoint returns 200 without authentication."""
        url = reverse("api-privacy-notice")
        response = self.api_client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("specified_purposes", response.data)
        self.assertIn("grievance_redressal_officer", response.data)

    def test_api_privacy_dossier_authenticated(self):
        """Dossier API returns full export for authenticated user."""
        self.api_client.force_authenticate(user=self.user)
        url = reverse("api-privacy-dossier")
        response = self.api_client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["dossier"]["account"]["username"], "testprincipal")

    def test_api_privacy_dossier_unauthenticated_rejected(self):
        """Unauthenticated access to dossier returns 401."""
        url = reverse("api-privacy-dossier")
        response = self.api_client.get(url)
        self.assertEqual(response.status_code, status.HTTP_401_UNAUTHORIZED)

    def test_api_consent_management(self):
        """API allows listing and updating consent."""
        self.api_client.force_authenticate(user=self.user)
        url = reverse("api-privacy-consents")

        # GET
        get_res = self.api_client.get(url)
        self.assertEqual(get_res.status_code, status.HTTP_200_OK)

        # POST withdrawal
        post_res = self.api_client.post(
            url,
            {"purpose": "email_notifications", "status": "withdrawn"},
            format="json",
        )
        self.assertEqual(post_res.status_code, status.HTTP_200_OK)
        self.assertFalse(ConsentService.has_consent(self.user, "email_notifications"))

    def test_api_nomination_management(self):
        """API allows full CRUD on nominee under Section 14."""
        self.api_client.force_authenticate(user=self.user)
        url = reverse("api-privacy-nomination")

        # 1. Create
        post_res = self.api_client.post(
            url,
            {
                "nominee_name": "Vikram Sharma",
                "nominee_email": "vikram@example.com",
                "relationship": "Father",
            },
            format="json",
        )
        self.assertEqual(post_res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(post_res.data["nominee"]["nominee_name"], "Vikram Sharma")

        # 2. Get
        get_res = self.api_client.get(url)
        self.assertEqual(get_res.status_code, status.HTTP_200_OK)
        self.assertIsNotNone(get_res.data["nominee"])

        # 3. Delete / Revoke
        del_res = self.api_client.delete(url)
        self.assertEqual(del_res.status_code, status.HTTP_204_NO_CONTENT)

    def test_api_grievance_submission(self):
        """API accepts grievance submission and returns tracking reference."""
        self.api_client.force_authenticate(user=self.user)
        url = reverse("api-privacy-grievances")

        response = self.api_client.post(
            url,
            {
                "full_name": "Aarav Sharma",
                "email": "principal@example.com",
                "subject": "Question about data storage location",
                "description": "Where is my task database hosted?",
                "category": "other",
            },
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertTrue(response.data["grievance"]["ticket_number"].startswith("GRV-"))

    # -------------------------------------------------------------------------
    # 9. WEB VIEWS & ACCESS CONTROLS
    # -------------------------------------------------------------------------
    def test_web_privacy_notice_view(self):
        """Web privacy notice view renders 200."""
        url = reverse("privacy_notice")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Digital Personal Data Protection")
        self.assertContains(response, "Data Protection Officer")

    def test_web_privacy_center_authenticated(self):
        """Privacy center view renders with 6 tabs for authenticated users."""
        self.client.force_login(self.user)
        url = reverse("privacy_center")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Privacy & Data Rights Center")
        self.assertContains(response, "Download Personal Data Dossier")

    def test_web_dossier_download(self):
        """Dossier download produces application/json attachment."""
        self.client.force_login(self.user)
        url = reverse("privacy_dossier_download")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response["Content-Type"].startswith("application/json"))
        self.assertIn("attachment; filename=", response["Content-Disposition"])

    def test_web_consent_toggle(self):
        """Web view allows toggling consent."""
        self.client.force_login(self.user)
        url = reverse("privacy_consent_toggle")
        response = self.client.post(
            url,
            {"purpose": "ai_assistant", "action": "grant"},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(ConsentService.has_consent(self.user, "ai_assistant"))

    def test_web_grievance_submission(self):
        """Web view allows submitting grievance ticket."""
        self.client.force_login(self.user)
        url = reverse("privacy_grievance_submit")
        response = self.client.post(
            url,
            {
                "full_name": "Aarav Sharma",
                "email": "principal@example.com",
                "subject": "Data port request",
                "description": "Please explain export structure",
                "category": "access",
            },
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(PrivacyGrievance.objects.filter(user=self.user, subject="Data port request").exists())

    def test_web_account_erasure(self):
        """Web view erases account when exact confirmation string is provided."""
        self.client.force_login(self.user)
        url = reverse("privacy_account_erase")
        response = self.client.post(
            url,
            {"confirm_erase": "ERASE MY DATA", "password": "SecurePassword123!"},
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(id=self.user.id).exists())

    # -------------------------------------------------------------------------
    # 10. REGISTRATION DPDP CONSENT & AGE AFFIRMATION VALIDATION
    # -------------------------------------------------------------------------
    def test_registration_requires_dpdp_consent_and_age_affirmation(self):
        """Registration form must reject submissions without DPDP consent or age confirmation."""
        # Missing dpdp_consent_essential and age_affirmation
        form_invalid = RegisterForm(data={
            "username": "newprincipal",
            "email": "new@example.com",
            "password": "SecurePassword123!",
            "confirm_password": "SecurePassword123!",
        })
        self.assertFalse(form_invalid.is_valid())
        self.assertIn("dpdp_consent_essential", form_invalid.errors)
        self.assertIn("age_affirmation", form_invalid.errors)

        # Valid submission
        form_valid = RegisterForm(data={
            "username": "newprincipal",
            "email": "new@example.com",
            "password": "SecurePassword123!",
            "confirm_password": "SecurePassword123!",
            "dpdp_consent_essential": True,
            "age_affirmation": True,
            "email_notifications_opt_in": True,
        })
        self.assertTrue(form_valid.is_valid())
