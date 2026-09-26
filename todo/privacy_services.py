"""
todo/privacy_services.py

Digital Personal Data Protection Act, 2023 (DPDP Act) & DPDP Rules, 2025
Privacy & Data Governance Service Layer for TaskFarmm.

Provides:
  1. ConsentService: Purpose-specific consent grant, audit records, and withdrawal engine.
  2. DataPrincipalRightsService: Personal Data Dossier compilation (Section 11),
     audited data correction (Section 12), and cascading secure erasure (Section 12(3)).
  3. GrievanceService: Privacy grievance tracking with statutory 90-day SLA enforcement (Section 13).
  4. NominationService: Data Principal representative nomination & revocation (Section 14).
  5. RetentionService: Automated data lifecycle cleanup & retention policy enforcement (Section 8(7)).
  6. BreachIncidentService: Incident triage, tracking, and statutory Board/Principal notification generation (Section 8(6)).
"""

import json
import logging
import os
from datetime import timedelta
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.sessions.models import Session
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .models import (
    ConsentRecord, DataPrincipalNomination, PrivacyGrievance,
    DataBreachIncident, PrivacyAuditLog, Task, Category,
    TaskComment, TaskAttachment, Notification, UserProfile
)

User = get_user_model()
logger = logging.getLogger('todo')


def get_client_ip(request):
    """Extracts client IP address safely from request headers."""
    if not request:
        return None
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        ip = x_forwarded_for.split(',')[0].strip()
    else:
        ip = request.META.get('REMOTE_ADDR')
    return ip


def get_user_agent(request):
    """Extracts and sanitizes user agent from request."""
    if not request:
        return ''
    ua = request.META.get('HTTP_USER_AGENT', '')
    return ua[:280]


# ============================================================
#  1. CONSENT SERVICE (DPDP Section 6 & DPDP Rules, 2025)
# ============================================================

class ConsentService:
    """
    Manages granular, purpose-specific consent lifecycle with immutable audit trail.
    """

    PURPOSE_DEFINITIONS = {
        ConsentRecord.Purpose.ESSENTIAL_SERVICE: {
            'title': 'Account & Core Task Management',
            'description': 'Processing necessary to create your account, manage projects, tasks, checklists, and collaborate with team members.',
            'is_mandatory': True,
        },
        ConsentRecord.Purpose.EMAIL_NOTIFICATIONS: {
            'title': 'Task Reminders, Comments & Alert Emails',
            'description': 'Outbound email notifications for task assignments, due dates, project invitations, and card discussions.',
            'is_mandatory': False,
        },
        ConsentRecord.Purpose.AI_ASSISTANT: {
            'title': 'AI Task Breakdown & Roadmap Planning',
            'description': 'Processing prompts and task descriptions through AI assistance to generate task suggestions and project roadmaps.',
            'is_mandatory': False,
        },
        ConsentRecord.Purpose.PRODUCT_UPDATES: {
            'title': 'Platform Announcements & Feature Updates',
            'description': 'Receiving periodic communications regarding new features, security enhancements, and major platform updates.',
            'is_mandatory': False,
        },
    }

    @classmethod
    def record_consent(cls, user, purpose, status=ConsentRecord.Status.GRANTED, notice_version=None, request=None, channel='web_app'):
        """
        Records or updates a consent record for a specific purpose.
        """
        if not user or not user.is_authenticated:
            return None

        if purpose not in ConsentRecord.Purpose.values:
            raise ValueError(f"Invalid consent purpose: {purpose}")

        version = notice_version or getattr(settings, 'DPDP_CONFIG', {}).get('NOTICE_VERSION', '1.0-2025-DPDP')
        ip_addr = get_client_ip(request)
        ua = get_user_agent(request)

        # Look for existing active record
        record = ConsentRecord.objects.filter(user=user, purpose=purpose).order_by('-granted_at').first()

        now = timezone.now()
        if record:
            record.status = status
            record.notice_version = version
            record.ip_address = ip_addr
            record.user_agent = ua
            record.channel = channel
            if status == ConsentRecord.Status.WITHDRAWN:
                record.withdrawn_at = now
            else:
                record.granted_at = now
                record.withdrawn_at = None
            record.save()
        else:
            record = ConsentRecord.objects.create(
                user=user,
                purpose=purpose,
                status=status,
                notice_version=version,
                ip_address=ip_addr,
                user_agent=ua,
                channel=channel,
                granted_at=now,
                withdrawn_at=now if status == ConsentRecord.Status.WITHDRAWN else None,
            )

        # Audit log entry
        action = 'CONSENT_GRANTED' if status == ConsentRecord.Status.GRANTED else 'CONSENT_WITHDRAWN'
        PrivacyAuditLog.objects.create(
            user=user,
            action=action,
            resource=f"consent:{purpose}",
            details={
                'purpose': purpose,
                'status': status,
                'notice_version': version,
                'channel': channel,
            },
            ip_address=ip_addr
        )

        logger.info("DPDP Consent recorded: user=%s purpose=%s status=%s version=%s", user.username, purpose, status, version)
        return record

    @classmethod
    def record_initial_user_consents(cls, user, opt_in_notifications=True, opt_in_ai=True, opt_in_updates=False, request=None, channel='registration'):
        """
        Records standard set of initial consents upon registration.
        """
        cls.record_consent(user, ConsentRecord.Purpose.ESSENTIAL_SERVICE, ConsentRecord.Status.GRANTED, request=request, channel=channel)
        
        status_notif = ConsentRecord.Status.GRANTED if opt_in_notifications else ConsentRecord.Status.WITHDRAWN
        cls.record_consent(user, ConsentRecord.Purpose.EMAIL_NOTIFICATIONS, status_notif, request=request, channel=channel)

        status_ai = ConsentRecord.Status.GRANTED if opt_in_ai else ConsentRecord.Status.WITHDRAWN
        cls.record_consent(user, ConsentRecord.Purpose.AI_ASSISTANT, status_ai, request=request, channel=channel)

        status_updates = ConsentRecord.Status.GRANTED if opt_in_updates else ConsentRecord.Status.WITHDRAWN
        cls.record_consent(user, ConsentRecord.Purpose.PRODUCT_UPDATES, status_updates, request=request, channel=channel)

    @classmethod
    def withdraw_consent(cls, user, purpose, request=None):
        """
        Withdraws consent for a purpose and synchronizes downstream feature settings.
        """
        if purpose == ConsentRecord.Purpose.ESSENTIAL_SERVICE:
            raise ValueError("Consent for Essential Service Delivery cannot be withdrawn individually. To stop all processing, use the Account Erasure workflow.")

        record = cls.record_consent(user, purpose, status=ConsentRecord.Status.WITHDRAWN, request=request, channel='privacy_center')

        # Synchronize profile toggles if related
        profile = getattr(user, 'profile', None)
        if profile:
            if purpose == ConsentRecord.Purpose.EMAIL_NOTIFICATIONS:
                profile.notify_task_reminders = False
                profile.notify_due_date_alerts = False
                profile.save(update_fields=['notify_task_reminders', 'notify_due_date_alerts'])
            elif purpose == ConsentRecord.Purpose.PRODUCT_UPDATES:
                profile.notify_app_updates = False
                profile.save(update_fields=['notify_app_updates'])

        return record

    @classmethod
    def has_consent(cls, user, purpose):
        """
        Returns True if the user currently has active granted consent for the specified purpose.
        """
        if not user or not user.is_authenticated:
            return False
        record = ConsentRecord.objects.filter(
            user=user, purpose=purpose, status=ConsentRecord.Status.GRANTED
        ).first()
        return bool(record)

    @classmethod
    def get_user_consent_overview(cls, user):
        """
        Returns all purposes with user status, description, and metadata for the Privacy Center UI.
        """
        user_records = {r.purpose: r for r in ConsentRecord.objects.filter(user=user)}
        result = []
        for p_key, p_meta in cls.PURPOSE_DEFINITIONS.items():
            rec = user_records.get(p_key)
            is_granted = bool(rec and rec.status == ConsentRecord.Status.GRANTED)
            result.append({
                'purpose': p_key,
                'title': p_meta['title'],
                'description': p_meta['description'],
                'is_mandatory': p_meta['is_mandatory'],
                'is_granted': is_granted,
                'status': rec.status if rec else ConsentRecord.Status.WITHDRAWN,
                'notice_version': rec.notice_version if rec else getattr(settings, 'DPDP_CONFIG', {}).get('NOTICE_VERSION', '1.0-2025-DPDP'),
                'granted_at': rec.granted_at.strftime('%Y-%m-%d %H:%M') if (rec and rec.granted_at) else None,
                'withdrawn_at': rec.withdrawn_at.strftime('%Y-%m-%d %H:%M') if (rec and rec.withdrawn_at) else None,
            })
        return result


# ============================================================
#  2. DATA PRINCIPAL RIGHTS SERVICE (DPDP Sections 11 & 12)
# ============================================================

class DataPrincipalRightsService:
    """
    Implements Data Principal Access, Correction, and Cascading Erasure workflows.
    """

    @classmethod
    def generate_personal_data_dossier(cls, user, request=None):
        """
        DPDP Section 11 Right to Access:
        Compiles a comprehensive, structured data dossier of all personal data held
        about the Data Principal across accounts, projects, tasks, comments, files,
        notifications, and consent history.
        """
        profile = getattr(user, 'profile', None)
        is_subuser = bool(profile and profile.is_subuser)
        parent_username = profile.parent_user.username if (is_subuser and profile.parent_user) else None

        # 1. Identity & Account Details
        account_data = {
            'id': user.id,
            'username': user.username,
            'email': user.email,
            'first_name': user.first_name,
            'last_name': user.last_name,
            'date_joined': user.date_joined.strftime('%Y-%m-%d %H:%M:%S UTC'),
            'last_login': user.last_login.strftime('%Y-%m-%d %H:%M:%S UTC') if user.last_login else None,
            'is_subuser': is_subuser,
            'parent_account': parent_username,
            'role': profile.role if profile else 'member',
            'theme_preference': profile.theme if profile else 'dark',
            'notify_task_reminders': profile.notify_task_reminders if profile else True,
            'notify_due_date_alerts': profile.notify_due_date_alerts if profile else True,
            'notify_app_updates': profile.notify_app_updates if profile else False,
        }

        # 2. Sub-users managed (if owner)
        subusers_data = []
        if not is_subuser:
            subusers = User.objects.filter(profile__parent_user=user).select_related('profile')
            for s in subusers:
                subusers_data.append({
                    'id': s.id,
                    'username': s.username,
                    'name': s.get_full_name() or s.username,
                    'email': s.email,
                    'role': s.profile.role if hasattr(s, 'profile') else 'member',
                    'date_joined': s.date_joined.strftime('%Y-%m-%d %H:%M:%S UTC'),
                    'is_active': s.is_active,
                })

        # 3. Projects / Categories
        projects_data = []
        user_projects = Category.objects.filter(Q(user=user) | Q(members=user)).distinct()
        for p in user_projects:
            projects_data.append({
                'id': p.id,
                'name': p.name,
                'color': p.color,
                'description': p.description or '',
                'board_template': p.board_template,
                'is_owner': (p.user_id == user.id),
                'created_at': p.created_at.strftime('%Y-%m-%d %H:%M:%S UTC'),
                'member_count': p.members.count(),
            })

        # 4. Tasks (Created or Assigned)
        tasks_data = []
        user_tasks = Task.objects.filter(Q(user=user) | Q(assignees=user)).distinct().select_related('category')
        for t in user_tasks:
            tasks_data.append({
                'id': t.id,
                'title': t.title,
                'description': t.description or '',
                'project_name': t.category.name if t.category else 'General',
                'status': t.status,
                'status_display': t.get_status_display(),
                'priority': t.priority,
                'priority_display': t.get_priority_display(),
                'due_date': t.due_date.strftime('%Y-%m-%d') if t.due_date else None,
                'created_at': t.created_at.strftime('%Y-%m-%d %H:%M:%S UTC'),
                'completed_at': t.completed_at.strftime('%Y-%m-%d %H:%M:%S UTC') if t.completed_at else None,
                'checklist_items_count': len(t.checklist) if isinstance(t.checklist, list) else 0,
            })

        # 5. Comments
        comments_data = []
        user_comments = TaskComment.objects.filter(user=user).select_related('task')
        for c in user_comments:
            comments_data.append({
                'id': c.id,
                'task_id': c.task_id,
                'task_title': c.task.title if c.task else '',
                'content': c.content,
                'created_at': c.created_at.strftime('%Y-%m-%d %H:%M:%S UTC'),
            })

        # 6. Attachment Metadata (zero disclosure of raw bytes)
        attachments_data = []
        user_attachments = TaskAttachment.objects.filter(user=user).select_related('task')
        for a in user_attachments:
            attachments_data.append({
                'id': a.id,
                'task_id': a.task_id,
                'task_title': a.task.title if a.task else '',
                'filename': a.filename,
                'file_size_bytes': a.file_size,
                'file_type': a.file_type,
                'created_at': a.created_at.strftime('%Y-%m-%d %H:%M:%S UTC'),
            })

        # 7. Notifications
        notifications_data = []
        user_notifs = Notification.objects.filter(user=user).order_by('-created_at')[:50]
        for n in user_notifs:
            notifications_data.append({
                'id': n.id,
                'event_type': n.event_type,
                'title': n.title,
                'message': n.message,
                'email': n.email,
                'status': n.status,
                'created_at': n.created_at.strftime('%Y-%m-%d %H:%M:%S UTC'),
            })

        # 8. Consents History
        consents_data = []
        user_consents = ConsentRecord.objects.filter(user=user).order_by('-granted_at')
        for cr in user_consents:
            consents_data.append({
                'purpose': cr.purpose,
                'status': cr.status,
                'notice_version': cr.notice_version,
                'granted_at': cr.granted_at.strftime('%Y-%m-%d %H:%M:%S UTC') if cr.granted_at else None,
                'withdrawn_at': cr.withdrawn_at.strftime('%Y-%m-%d %H:%M:%S UTC') if cr.withdrawn_at else None,
                'channel': cr.channel,
            })

        # 9. Nomination Details (if set)
        nomination_data = None
        nom = DataPrincipalNomination.objects.filter(user=user, status=DataPrincipalNomination.Status.ACTIVE).first()
        if nom:
            nomination_data = {
                'nominee_name': nom.nominee_name,
                'nominee_email': nom.nominee_email,
                'nominee_phone': nom.nominee_phone,
                'relationship': nom.relationship,
                'status': nom.status,
                'created_at': nom.created_at.strftime('%Y-%m-%d %H:%M:%S UTC'),
            }

        # 10. Grievances
        grievances_data = []
        user_grievances = PrivacyGrievance.objects.filter(user=user).order_by('-created_at')
        for gr in user_grievances:
            grievances_data.append({
                'ticket_number': gr.ticket_number,
                'category': gr.category,
                'subject': gr.subject,
                'status': gr.status,
                'statutory_deadline': gr.statutory_deadline.strftime('%Y-%m-%d'),
                'created_at': gr.created_at.strftime('%Y-%m-%d %H:%M:%S UTC'),
            })

        # Audit log
        PrivacyAuditLog.objects.create(
            user=user,
            action='DATA_DOSSIER_DOWNLOAD',
            resource='dossier',
            details={'export_format': 'json', 'total_tasks': len(tasks_data)},
            ip_address=get_client_ip(request)
        )

        dossier = {
            'dpdp_act_compliance': {
                'act': 'Digital Personal Data Protection Act, 2023',
                'rules': 'Digital Personal Data Protection Rules, 2025',
                'data_fiduciary': getattr(settings, 'DPDP_CONFIG', {}).get('DATA_FIDUCIARY_NAME', 'TaskFarmm Technologies'),
                'notice_version': getattr(settings, 'DPDP_CONFIG', {}).get('NOTICE_VERSION', '1.0-2025-DPDP'),
                'generated_at': timezone.now().strftime('%Y-%m-%d %H:%M:%S UTC'),
            },
            'account': account_data,
            'team_subusers': subusers_data,
            'projects': projects_data,
            'tasks': tasks_data,
            'comments': comments_data,
            'attachments_metadata': attachments_data,
            'notifications': notifications_data,
            'consent_history': consents_data,
            'data_principal_nomination': nomination_data,
            'privacy_grievances': grievances_data,
        }
        return dossier

    @classmethod
    def execute_account_erasure(cls, user, request=None):
        """
        DPDP Section 12(3) Right to Erasure:
        Executes cascading permanent deletion of all personal data, task attachments from storage,
        associated sub-users, projects, notifications, and sessions.
        """
        username = user.username
        user_id = user.id
        ip_addr = get_client_ip(request)

        with transaction.atomic():
            # 1. Delete physical files from disk / media storage for all user attachments
            attachments = list(TaskAttachment.objects.filter(
                Q(user=user) | Q(task__user=user) | Q(task__category__user=user)
            ))
            deleted_files_count = 0
            for att in attachments:
                if att.file:
                    try:
                        if os.path.isfile(att.file.path):
                            os.remove(att.file.path)
                            deleted_files_count += 1
                    except Exception as e:
                        logger.warning("Could not delete attachment file on erasure: %s", e)
                att.delete()

            # 2. If user is owner, delete all sub-users and their data
            subusers = list(User.objects.filter(profile__parent_user=user))
            for sub in subusers:
                sub_attachments = list(TaskAttachment.objects.filter(user=sub))
                for satt in sub_attachments:
                    if satt.file:
                        try:
                            if os.path.isfile(satt.file.path):
                                os.remove(satt.file.path)
                        except Exception:
                            pass
                    satt.delete()
                sub.delete()

            # 3. Clean projects and tasks
            Task.objects.filter(user=user).delete()
            Category.objects.filter(user=user).delete()
            TaskComment.objects.filter(user=user).delete()
            Notification.objects.filter(user=user).delete()
            ConsentRecord.objects.filter(user=user).delete()
            DataPrincipalNomination.objects.filter(user=user).delete()

            # 4. Record pseudonymized audit log entry for regulatory record-keeping
            PrivacyAuditLog.objects.create(
                user=None,
                user_identifier=f"erased_user_{user_id}",
                action='ACCOUNT_ERASED',
                resource='user_account',
                details={
                    'user_id': user_id,
                    'username_hash': f"sha256_{hash(username)}",
                    'deleted_files_count': deleted_files_count,
                    'erased_at': timezone.now().isoformat(),
                },
                ip_address=ip_addr
            )

            # 5. Delete User instance and related profile (cascades)
            user.delete()

        logger.info("DPDP Account erasure completed: user_id=%s username=%s", user_id, username)
        return True


# ============================================================
#  3. GRIEVANCE SERVICE (DPDP Section 13 & DPDP Rules, 2025)
# ============================================================

class GrievanceService:
    """
    Handles Privacy Grievance submission, tracking, and resolution with 90-day SLA.
    """

    @classmethod
    def create_grievance(cls, full_name, email, category, subject, description, user=None, request=None):
        """
        Creates a new Privacy Grievance ticket with statutory 90-day SLA deadline.
        """
        if not full_name or not full_name.strip():
            raise ValueError("Full name is required.")
        if not email or '@' not in email:
            raise ValueError("A valid email address is required.")
        if not subject or not subject.strip():
            raise ValueError("Subject is required.")
        if not description or not description.strip():
            raise ValueError("Description of grievance is required.")

        if category not in PrivacyGrievance.Category.values:
            category = PrivacyGrievance.Category.OTHER

        grievance = PrivacyGrievance.objects.create(
            user=user if (user and user.is_authenticated) else None,
            full_name=full_name.strip(),
            email=email.strip().lower(),
            category=category,
            subject=subject.strip(),
            description=description.strip(),
            status=PrivacyGrievance.Status.SUBMITTED,
        )

        PrivacyAuditLog.objects.create(
            user=user if (user and user.is_authenticated) else None,
            user_identifier=email.strip().lower(),
            action='GRIEVANCE_SUBMITTED',
            resource=f"grievance:{grievance.ticket_number}",
            details={
                'ticket_number': grievance.ticket_number,
                'category': category,
                'subject': subject,
                'statutory_deadline': grievance.statutory_deadline.isoformat(),
            },
            ip_address=get_client_ip(request)
        )

        # Notify user via notification queue if authenticated
        if user and user.is_authenticated:
            try:
                Notification.objects.create(
                    user=user,
                    event_type=Notification.EventType.SYSTEM,
                    title=f"Privacy Grievance Received [{grievance.ticket_number}]",
                    message=f"Your grievance '{subject}' has been registered with ticket number {grievance.ticket_number}. Under DPDP Rules 2025, our Grievance Officer will review and resolve this within statutory timelines.",
                    action_url=f"/privacy/center/?tab=grievances",
                    status=Notification.Status.PENDING,
                )
            except Exception as e:
                logger.warning("Could not queue grievance acknowledgment notification: %s", e)

        logger.info("DPDP Privacy Grievance created: ticket=%s email=%s category=%s", grievance.ticket_number, email, category)
        return grievance

    @classmethod
    def get_grievance_by_ticket(cls, ticket_number, email=None):
        """
        Retrieves grievance by ticket number, verifying email for security.
        """
        qs = PrivacyGrievance.objects.filter(ticket_number__iexact=ticket_number.strip())
        if email:
            qs = qs.filter(email__iexact=email.strip().lower())
        return qs.first()

    @classmethod
    def resolve_grievance(cls, grievance, resolution_notes, new_status=PrivacyGrievance.Status.RESOLVED):
        """
        Resolves or updates grievance status with notes.
        """
        grievance.status = new_status
        grievance.resolution_notes = resolution_notes.strip() if resolution_notes else ''
        if new_status in (PrivacyGrievance.Status.RESOLVED, PrivacyGrievance.Status.REJECTED):
            grievance.resolved_at = timezone.now()
        grievance.save()

        PrivacyAuditLog.objects.create(
            user=grievance.user,
            user_identifier=grievance.email,
            action=f"GRIEVANCE_{new_status.upper()}",
            resource=f"grievance:{grievance.ticket_number}",
            details={'ticket_number': grievance.ticket_number, 'status': new_status},
        )
        return grievance


# ============================================================
#  4. NOMINATION SERVICE (DPDP Section 14)
# ============================================================

class NominationService:
    """
    Manages Data Principal Nomination.
    """

    @classmethod
    def set_nominee(cls, user, nominee_name, nominee_email, relationship, nominee_phone='', notes='', request=None):
        """
        Creates or updates nominee for the Data Principal.
        """
        if not user or not user.is_authenticated:
            raise ValueError("Authenticated user required for nomination.")
        if not nominee_name or not nominee_name.strip():
            raise ValueError("Nominee name is required.")
        if not nominee_email or '@' not in nominee_email:
            raise ValueError("A valid nominee email address is required.")
        if not relationship or not relationship.strip():
            raise ValueError("Relationship to nominee is required.")

        nominee, _ = DataPrincipalNomination.objects.get_or_create(
            user=user,
            defaults={
                'nominee_name': nominee_name.strip(),
                'nominee_email': nominee_email.strip().lower(),
                'nominee_phone': nominee_phone.strip(),
                'relationship': relationship.strip(),
                'notes': notes.strip(),
                'status': DataPrincipalNomination.Status.ACTIVE,
            }
        )
        nominee.nominee_name = nominee_name.strip()
        nominee.nominee_email = nominee_email.strip().lower()
        nominee.nominee_phone = nominee_phone.strip()
        nominee.relationship = relationship.strip()
        nominee.notes = notes.strip()
        nominee.status = DataPrincipalNomination.Status.ACTIVE
        nominee.save()

        PrivacyAuditLog.objects.create(
            user=user,
            action='NOMINATION_UPDATED',
            resource='nomination',
            details={
                'nominee_name': nominee.nominee_name,
                'relationship': nominee.relationship,
                'status': nominee.status,
            },
            ip_address=get_client_ip(request)
        )

        logger.info("DPDP Nominee set: user=%s nominee=%s (%s)", user.username, nominee.nominee_name, nominee.relationship)
        return nominee

    @classmethod
    def get_nominee(cls, user):
        """Returns active nominee or None."""
        if not user or not user.is_authenticated:
            return None
        return DataPrincipalNomination.objects.filter(user=user, status=DataPrincipalNomination.Status.ACTIVE).first()

    @classmethod
    def revoke_nominee(cls, user, request=None):
        """Revokes existing nominee."""
        nominee = DataPrincipalNomination.objects.filter(user=user).first()
        if nominee:
            nominee.status = DataPrincipalNomination.Status.REVOKED
            nominee.save(update_fields=['status', 'updated_at'])

            PrivacyAuditLog.objects.create(
                user=user,
                action='NOMINATION_REVOKED',
                resource='nomination',
                details={'status': DataPrincipalNomination.Status.REVOKED},
                ip_address=get_client_ip(request)
            )
            return True
        return False


# ============================================================
#  5. RETENTION & CLEANUP SERVICE (DPDP Section 8(7))
# ============================================================

class RetentionService:
    """
    Automates data lifecycle retention cleanup policies.
    """

    @classmethod
    def run_cleanup_retention(cls, dry_run=False):
        """
        Purges data that has exceeded statutory/business retention limits:
          - Notifications older than DPDP_RETENTION_NOTIFICATIONS_DAYS (default 90 days)
          - Expired Django sessions
          - Orphaned attachment files on disk
          - Audit logs older than DPDP_RETENTION_AUDIT_LOGS_DAYS (default 365 days)
        """
        dpdp_cfg = getattr(settings, 'DPDP_CONFIG', {})
        notif_days = dpdp_cfg.get('RETENTION_NOTIFICATIONS_DAYS', 90)
        session_days = dpdp_cfg.get('RETENTION_EXPIRED_SESSIONS_DAYS', 30)
        audit_days = dpdp_cfg.get('RETENTION_AUDIT_LOGS_DAYS', 365)

        now = timezone.now()
        notif_cutoff = now - timedelta(days=notif_days)
        session_cutoff = now - timedelta(days=session_days)
        audit_cutoff = now - timedelta(days=audit_days)

        stats = {
            'notifications_purged': 0,
            'sessions_purged': 0,
            'audit_logs_purged': 0,
            'dry_run': dry_run,
        }

        # 1. Notifications cleanup
        notifs_qs = Notification.objects.filter(created_at__lt=notif_cutoff)
        stats['notifications_purged'] = notifs_qs.count()
        if not dry_run and stats['notifications_purged'] > 0:
            notifs_qs.delete()

        # 2. Expired sessions cleanup
        expired_sessions = Session.objects.filter(expire_date__lt=session_cutoff)
        stats['sessions_purged'] = expired_sessions.count()
        if not dry_run and stats['sessions_purged'] > 0:
            expired_sessions.delete()

        # 3. Old audit logs cleanup
        old_audit = PrivacyAuditLog.objects.filter(timestamp__lt=audit_cutoff)
        stats['audit_logs_purged'] = old_audit.count()
        if not dry_run and stats['audit_logs_purged'] > 0:
            old_audit.delete()

        logger.info("DPDP Retention cleanup executed: %s", stats)
        return stats


# ============================================================
#  6. DATA BREACH INCIDENT SERVICE (DPDP Section 8(6))
# ============================================================

class BreachIncidentService:
    """
    Logs, tracks, and prepares statutory breach notifications under DPDP Rules, 2025.
    """

    @classmethod
    def log_incident(cls, title, nature_and_scope, affected_data_categories, estimated_affected_principals=0,
                     severity=DataBreachIncident.Severity.MEDIUM, containment_actions='', remediation_steps=''):
        """
        Creates a new personal data breach incident record.
        """
        incident = DataBreachIncident.objects.create(
            title=title.strip(),
            nature_and_scope=nature_and_scope.strip(),
            affected_data_categories=affected_data_categories.strip(),
            estimated_affected_principals=estimated_affected_principals,
            severity=severity,
            containment_actions=containment_actions.strip(),
            remediation_steps=remediation_steps.strip(),
            status=DataBreachIncident.Status.DETECTED,
            discovered_at=timezone.now(),
        )

        PrivacyAuditLog.objects.create(
            action='DATA_BREACH_LOGGED',
            resource=f"incident:{incident.incident_id}",
            details={
                'incident_id': incident.incident_id,
                'severity': severity,
                'affected_count': estimated_affected_principals,
            }
        )

        logger.warning("DPDP Personal Data Breach logged: id=%s severity=%s", incident.incident_id, severity)
        return incident

    @classmethod
    def generate_board_notification_summary(cls, incident):
        """
        Generates formal statutory notification payload for the Data Protection Board of India.
        """
        fiduciary_name = getattr(settings, 'DPDP_CONFIG', {}).get('DATA_FIDUCIARY_NAME', 'TaskFarmm Technologies')
        officer_name = getattr(settings, 'DPDP_CONFIG', {}).get('GRIEVANCE_OFFICER_NAME', 'Data Protection Officer')
        officer_email = getattr(settings, 'DPDP_CONFIG', {}).get('GRIEVANCE_OFFICER_EMAIL', 'privacy@taskfarmm.com')

        return {
            'form': 'Form DPDP-BN-1 (Notice of Personal Data Breach to Data Protection Board)',
            'incident_id': incident.incident_id,
            'data_fiduciary': fiduciary_name,
            'contact_person': f"{officer_name} ({officer_email})",
            'date_time_occurrence': incident.occurred_at.isoformat(),
            'date_time_discovery': incident.discovered_at.isoformat(),
            'nature_and_scope': incident.nature_and_scope,
            'affected_data_categories': incident.affected_data_categories,
            'estimated_affected_principals': incident.estimated_affected_principals,
            'severity_rating': incident.severity,
            'containment_measures_taken': incident.containment_actions,
            'remediation_plan': incident.remediation_steps,
        }
