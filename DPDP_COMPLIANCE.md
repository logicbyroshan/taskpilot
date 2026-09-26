# TaskFarmm (TaskPilot) — DPDP Act, 2023 & DPDP Rules, 2025 Technical Compliance Manual

**Document Version:** 1.0-2025-DPDP  
**Jurisdiction:** Digital Personal Data Protection Act, 2023 (Act No. 22 of 2023) & Digital Personal Data Protection Rules, 2025  
**Data Fiduciary:** TaskFarmm Technologies  
**Classification:** Internal Technical Architecture, Compliance Operations & Audit Reference

---

## 1. Executive Summary & Legal Classification

TaskFarmm operates a full-stack, real-time task orchestration and project management platform. Under the Digital Personal Data Protection Act, 2023:
- **Data Fiduciary (Section 2(i)):** TaskFarmm acts as the primary Data Fiduciary for registered Workspace Owners and direct account holders, determining the purpose and means of processing personal data.
- **Data Processor (Section 2(k)):** Where workspace owners provision and manage sub-users (members, viewers, admins), TaskFarmm processes sub-user data strictly under the instructions of the parent Workspace Owner.
- **Data Principal (Section 2(j)):** Any natural person (registered user, sub-user, or visitor) whose personal data is processed.
- **Significant Data Fiduciary (SDF) Readiness (Section 10):** While TaskFarmm operates as a standard Data Fiduciary, the architecture implements SDF-ready controls: independent DPO registration, periodic Data Protection Impact Assessment (DPIA) mechanisms, and regulatory audit logging.

---

## 2. Personal Data Mapping & Purpose Registry

| Category | Specific Data Elements | Processing Purpose | Legal Basis / Consent Tier | Retention Threshold | Purging Mechanism |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Identity & Account** | Username, email, hashed password, full name, phone number | Account authentication, workspace isolation, team collaboration | Mandatory Consent (`essential_service`) | Account lifetime | Cascading erasure under Section 12(3) |
| **Workspace Content** | Tasks, Kanban columns, checklist items, project boards, task comments | Task management, deadline tracking, collaborative workflow | Mandatory Consent (`essential_service`) | Account lifetime | Deleted on account/task erasure |
| **Uploaded Attachments** | PDF, image, document files stored on disk (`/media/`) | Task documentation, file sharing | Mandatory Consent (`essential_service`) | Account lifetime | Physical unlinking (`os.remove`) on task/account deletion |
| **Communications & Alerts** | In-app alerts, email notifications (due dates, task assignments) | Notification dispatch and audit tracking | Granular Opt-in Consent (`email_notifications`) | 90 days maximum | Automated cron / `cleanup_retention_data` |
| **AI Assistant Interactions** | User prompt strings, task suggestions | Roadmap generation, task breakdowns | Granular Opt-in Consent (`ai_assistant`) | Ephemeral (in-memory) | Zero persistent user profiling |
| **Regulatory & Security Logs** | IP address, user agent, action timestamps, nominee data | Statutory compliance, breach detection, grievance handling | Legal obligation (Sections 8, 13, 14) | 365 days maximum | Immutable ORM records, automated 365d pruning |

---

## 3. Statutory Notice Architecture (Section 5)

TaskFarmm delivers itemized, clear, and unambiguous notice to Data Principals via two synchronized channels:
1. **Web Public Notice:** Accessible to visitors and users at [`/privacy/`](/privacy/) without requiring authentication.
2. **REST API Notice:** Structured JSON metadata at [`/api/v1/privacy/notice/`](/api/v1/privacy/notice/).
3. **Registration Notice:** Presented prior to account creation at [`/register/`](/register/) containing:
   - Itemized processing purposes.
   - Mechanism to exercise Section 6(4) withdrawal and Section 11–14 rights.
   - Contact details of the Data Protection & Grievance Redressal Officer.
   - Right of escalation to the **Data Protection Board of India**.

---

## 4. Consent Lifecycle & Section 6(4) Withdrawal Engine

### Consent Registry (`ConsentRecord` Model)
Located in [`todo/models.py`](todo/models.py), the `ConsentRecord` model stores:
- `user`: Foreign key to `auth.User`.
- `purpose`: `essential_service`, `email_notifications`, `ai_assistant`, `product_updates`.
- `status`: `granted` or `withdrawn`.
- `notice_version`: E.g., `1.0-2025-DPDP`.
- `ip_address`, `user_agent`, `channel`: Source audit metadata.
- `granted_at`, `withdrawn_at`: Precise timestamps.

### Comparable Ease of Withdrawal (Section 6(4))
- Users can toggle or withdraw consent instantly with a single click in the **Privacy & Data Rights Center** ([`/privacy/center/?tab=consents`](/privacy/center/?tab=consents)) or via `POST /api/v1/privacy/consents/`.
- Withdrawing `email_notifications` immediately synchronizes and disables task reminders and due date email dispatch flags on `UserProfile`.

---

## 5. Data Principal Rights Implementation (Sections 11–14)

### 5.1 Right to Access & Personal Data Dossier (Section 11)
- **Web:** One-click JSON export at [`/privacy/dossier/download/`](/privacy/dossier/download/).
- **API:** Authenticated endpoint `GET /api/v1/privacy/dossier/`.
- **Dossier Payload:** Exports structured profile metadata, managed sub-users, all projects/categories, all tasks and checklists, comments, attachment metadata (file sizes, types, filenames), notification history, active consents, and registered nominees.

### 5.2 Right to Correction & Updating (Section 12)
- Self-service profile updates via [`/settings/`](/settings/) with audited logging in `PrivacyAuditLog`.

### 5.3 Right to Erasure & Cascading Physical Purging (Section 12(3))
- **Trigger:** Accessible via [`/privacy/center/?tab=erasure`](/privacy/center/?tab=erasure) or `POST /api/v1/privacy/erase/`.
- **Security Check:** Requires entering the exact confirmation phrase `ERASE MY DATA` and verifying account password.
- **Cascading Purge:**
  1. Identifies and physically unlinks all uploaded task attachment files from disk (`os.remove(att.file.path)`).
  2. If the user is an owner, cascades deletion to all associated sub-user accounts and their attachments.
  3. Purges all tasks, projects, Kanban boards, comments, notifications, consents, nominations, and grievances from the database.
  4. Records a pseudonymized entry (`user_identifier='erased_user_<id>'`) in `PrivacyAuditLog` with zero residual PII.
  5. Terminates active session and deletes the user record from `auth.User`.

### 5.4 Grievance Redressal with Statutory ≤ 90-Day SLA (Section 13 & DPDP Rules 2025)
- **Ticket Generation:** Unique statutory reference number format `GRV-YYYYMMDD-XXXXXX`.
- **Statutory Deadline:** Automatically calculated as `timezone.now() + timedelta(days=90)`.
- **Interface:** Submit and track grievances at [`/privacy/center/?tab=grievances`](/privacy/center/?tab=grievances) or `POST /api/v1/privacy/grievances/`.
- **DPO Details:**
  - **Officer:** Roshan Damor (Data Protection & Grievance Officer)
  - **Email:** `privacy@taskfarmm.com`
  - **Escalation:** Data Protection Board of India ([https://www.meity.gov.in/dpdp](https://www.meity.gov.in/dpdp)).

### 5.5 Data Principal Nomination (Section 14)
- **Functionality:** Nominate an authorized representative in case of death or incapacity to manage or retrieve data.
- **Interface:** [`/privacy/center/?tab=nomination`](/privacy/center/?tab=nomination) or `/api/v1/privacy/nomination/`.
- **Operations:** Register nominee, update contact/relationship details, or revoke nomination.

---

## 6. Protection of Children & Age Affirmation (Section 9)

- Under Section 9, a child is defined as any individual under 18 years of age.
- TaskFarmm strictly prohibits tracking, behavioral monitoring, or targeted advertising directed at minors.
- The registration flow (`RegisterForm` in [`todo/forms.py`](todo/forms.py)) requires mandatory affirmative confirmation:
  - `age_affirmation`: *"I affirm that I am 18 years of age or older, or authorized by a parent/lawful guardian."*
  - `dpdp_consent_essential`: *"I have read and consent to the TaskFarmm DPDP Privacy Notice."*

---

## 7. Security Safeguards & Data Breach Triage (Section 8(6))

### Security Hardening in `config/settings.py`
- `SECURE_CONTENT_TYPE_NOSNIFF = True`
- `SECURE_REFERRER_POLICY = 'strict-origin-when-cross-origin'`
- `SESSION_COOKIE_HTTPONLY = True`
- `SESSION_COOKIE_AGE = 86400` (24-hour lifetime)
- `SESSION_EXPIRE_AT_BROWSER_CLOSE = False`
- `SESSION_COOKIE_SAMESITE = 'Lax'`

### Incident Management & Form DPDP-BN-1
- `DataBreachIncident` model tracks:
  - Incident ID (`INC-YYYYMMDD-XXXXXX`), severity (`low`, `medium`, `high`, `critical`), status (`detected`, `investigating`, `contained`, `resolved`).
  - Affected data categories, estimated principal count, containment actions, and remediation steps.
- **Statutory Notice Summary:** `BreachIncidentService.generate_board_notification_summary` generates formatted **Form DPDP-BN-1** for submission to the Data Protection Board of India.
- **Management Command:** `python manage.py record_data_breach` CLI triage utility.

---

## 8. Automated Data Retention & Lifecycle Purging (Section 8(7))

### Retention Policy Engine (`RetentionService`)
Configured in `settings.DPDP_CONFIG`:
- `RETENTION_NOTIFICATIONS_DAYS`: 90 days.
- `RETENTION_EXPIRED_SESSIONS_DAYS`: 30 days.
- `RETENTION_AUDIT_LOGS_DAYS`: 365 days.

### Automated Management Command
```bash
# Execute retention cleanup
python manage.py cleanup_retention_data

# Dry-run inspection
python manage.py cleanup_retention_data --dry-run
```

---

## 9. Automated Test Verification

A dedicated compliance test suite ([`todo/tests/test_dpdp_compliance.py`](todo/tests/test_dpdp_compliance.py)) validates all 10 core DPDP modules across 26 distinct test cases:
1. `test_consent_grant_and_retrieval` (Section 6)
2. `test_consent_withdrawal` (Section 6(4))
3. `test_essential_service_cannot_be_withdrawn` (Purpose limitation)
4. `test_invalid_purpose_rejected` (Validation)
5. `test_consent_overview_matrix` (Notice & UI sync)
6. `test_personal_data_dossier_generation` (Section 11)
7. `test_account_erasure_purges_data_and_physical_files` (Section 12(3) & disk cleanup)
8. `test_account_erasure_fails_without_confirmation` (Erasure safety gate)
9. `test_grievance_creation_and_90day_sla` (Section 13 & ≤ 90-day statutory calculation)
10. `test_grievance_resolution` (Grievance lifecycle)
11. `test_grievance_tracking_by_ticket_and_email` (IDOR protection)
12. `test_nomination_lifecycle` (Section 14 CRUD)
13. `test_retention_cleanup_notifications` (Section 8(7) lifecycle)
14. `test_breach_incident_recording_and_board_notification` (Section 8(6) Form DPDP-BN-1)
15. `test_api_privacy_notice_public` (REST API Notice)
16. `test_api_privacy_dossier_authenticated` (REST API Dossier)
17. `test_api_privacy_dossier_unauthenticated_rejected` (REST API 401 gate)
18. `test_api_consent_management` (REST API Consent toggle)
19. `test_api_nomination_management` (REST API Nominee CRUD)
20. `test_api_grievance_submission` (REST API Grievance ticket)
21. `test_web_privacy_notice_view` (Web Notice 200)
22. `test_web_privacy_center_authenticated` (Web 6-Tab Privacy Hub)
23. `test_web_dossier_download` (JSON Dossier attachment header)
24. `test_web_consent_toggle` (Web Form consent toggle)
25. `test_web_grievance_submission` (Web Form grievance submission)
26. `test_web_account_erasure` (Web Form account deletion)
27. `test_registration_requires_dpdp_consent_and_age_affirmation` (Section 9 minor safety)

---

## 10. Operational Checklist for Deployment

- [x] Apply Migration `0013_databreachincident_dataprincipalnomination_and_more.py`.
- [x] Configure production environment variables in `.env` (`DPDP_FIDUCIARY_NAME`, `DPDP_DPO_EMAIL`, `DPDP_DPO_PHONE`).
- [x] Schedule `python manage.py cleanup_retention_data` as a recurring daily cron job at 00:00 UTC.
- [x] Verify DPO contact email (`privacy@taskfarmm.com`) is active and routed to authorized compliance officers.
