"""
todo/management/commands/record_data_breach.py

Administrative CLI tool for DPDP Section 8(6) & DPDP Rules 2025 Data Breach Incident response.
"""

import json
from django.core.management.base import BaseCommand
from todo.privacy_services import BreachIncidentService
from todo.models import DataBreachIncident


class Command(BaseCommand):
    help = 'Records a personal data breach incident and generates DPDP Board notification payload.'

    def add_arguments(self, parser):
        parser.add_argument('--title', type=str, required=True, help='Short title of the incident')
        parser.add_argument('--nature', type=str, required=True, help='Nature and scope of the breach')
        parser.add_argument('--categories', type=str, required=True, help='Affected personal data categories')
        parser.add_argument('--affected-count', type=int, default=0, help='Estimated number of affected Data Principals')
        parser.add_argument('--severity', type=str, default='medium', choices=['low', 'medium', 'high', 'critical'])
        parser.add_argument('--containment', type=str, default='', help='Containment actions taken')
        parser.add_argument('--remediation', type=str, default='', help='Remediation plan')

    def handle(self, *args, **options):
        title = options['title']
        nature = options['nature']
        categories = options['categories']
        affected_count = options['affected_count']
        severity = options['severity']
        containment = options['containment']
        remediation = options['remediation']

        self.stdout.write(self.style.WARNING("=== Recording Personal Data Breach Incident ==="))
        incident = BreachIncidentService.log_incident(
            title=title,
            nature_and_scope=nature,
            affected_data_categories=categories,
            estimated_affected_principals=affected_count,
            severity=severity,
            containment_actions=containment,
            remediation_steps=remediation,
        )

        self.stdout.write(self.style.SUCCESS(f"Incident recorded: {incident.incident_id} [{incident.severity}]"))
        
        summary = BreachIncidentService.generate_board_notification_summary(incident)
        self.stdout.write("\n=== DPDP Board Notification Payload ===")
        self.stdout.write(json.dumps(summary, indent=2))
