"""
todo/management/commands/cleanup_retention_data.py

DPDP Act 2023 Section 8(7) & DPDP Rules 2025 Automated Retention Cleanup Command.
Purges expired notifications, expired session keys, orphaned files, and aged audit records.
"""

from django.core.management.base import BaseCommand
from todo.privacy_services import RetentionService


class Command(BaseCommand):
    help = 'Executes DPDP statutory data retention cleanup policies (purges expired notifications, sessions, and audit logs).'

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Simulates cleanup and displays counts of records that would be purged without deleting.',
        )

    def handle(self, *args, **options):
        dry_run = options.get('dry_run', False)
        mode_str = "DRY RUN (Simulated)" if dry_run else "LIVE EXECUTION"

        self.stdout.write(self.style.NOTICE(f"--- Starting DPDP Data Retention Cleanup Engine [{mode_str}] ---"))
        stats = RetentionService.run_cleanup_retention(dry_run=dry_run)

        self.stdout.write(f"  • Notifications purged: {stats['notifications_purged']}")
        self.stdout.write(f"  • Expired sessions purged: {stats['sessions_purged']}")
        self.stdout.write(f"  • Aged audit logs purged: {stats['audit_logs_purged']}")
        self.stdout.write(self.style.SUCCESS("--- Retention Cleanup Finished Successfully ---"))
