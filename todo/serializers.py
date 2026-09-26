"""
todo/serializers.py

DRF serializers for all TaskFarmm models.

Kept thin — no business logic here. Validation and object creation are
delegated to the services layer where appropriate.
"""

from django.contrib.auth.models import User
from rest_framework import serializers

from .models import Task, Category, TaskComment, PreDefinedTask, UserProfile


# ============================================================
#  CATEGORY / PROJECT
# ============================================================

class CategorySerializer(serializers.ModelSerializer):
    """Full project/category serializer for CRUD."""

    task_count = serializers.IntegerField(read_only=True)
    completed_count = serializers.IntegerField(read_only=True)
    progress = serializers.IntegerField(read_only=True)

    class Meta:
        model = Category
        fields = [
            'id', 'name', 'color', 'description',
            'task_count', 'completed_count', 'progress',
            'created_at',
        ]
        read_only_fields = ['id', 'created_at']


class CategoryMinimalSerializer(serializers.ModelSerializer):
    """Lightweight serializer used for nested embedding inside Task."""

    class Meta:
        model = Category
        fields = ['id', 'name', 'color']


# ============================================================
#  TASK COMMENT
# ============================================================

class TaskCommentSerializer(serializers.ModelSerializer):
    """Serializer for task activity/discussion comments."""

    user = serializers.CharField(source='user.username', read_only=True)
    time_ago = serializers.SerializerMethodField()
    created_at_display = serializers.SerializerMethodField()

    class Meta:
        model = TaskComment
        fields = ['id', 'user', 'content', 'time_ago', 'created_at_display', 'created_at']
        read_only_fields = ['id', 'user', 'time_ago', 'created_at_display', 'created_at']

    def get_time_ago(self, obj):
        from django.utils.timesince import timesince
        return timesince(obj.created_at) + ' ago'

    def get_created_at_display(self, obj):
        return obj.created_at.strftime('%d %b %Y, %H:%M')


# ============================================================
#  TASK (LIST VIEW — lightweight)
# ============================================================

class TaskListSerializer(serializers.ModelSerializer):
    """
    Lightweight task serializer for list endpoints.
    Omits description, checklist, and comments to reduce payload size.
    """

    category = CategoryMinimalSerializer(read_only=True)
    category_id = serializers.PrimaryKeyRelatedField(
        queryset=Category.objects.none(),  # Overridden in __init__
        write_only=True,
        required=False,
        allow_null=True,
        source='category',
    )
    priority_display = serializers.CharField(source='get_priority_display', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    due_date = serializers.DateField(required=False, allow_null=True)

    class Meta:
        model = Task
        fields = [
            'id', 'title', 'priority', 'priority_display',
            'status', 'status_display', 'due_date',
            'category', 'category_id',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        request = self.context.get('request')
        if request and request.user.is_authenticated:
            self.fields['category_id'].queryset = Category.objects.filter(
                user=request.user
            )


# ============================================================
#  TASK (DETAIL VIEW — full)
# ============================================================

class TaskSerializer(serializers.ModelSerializer):
    """
    Full task serializer with nested category, checklist, and comments.
    Used for detail, create, and update endpoints.
    """

    category = CategoryMinimalSerializer(read_only=True)
    category_id = serializers.PrimaryKeyRelatedField(
        queryset=Category.objects.none(),
        write_only=True,
        required=False,
        allow_null=True,
        source='category',
    )
    priority_display = serializers.CharField(source='get_priority_display', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    comments = TaskCommentSerializer(many=True, read_only=True)
    comment_count = serializers.IntegerField(source='comments.count', read_only=True)
    checklist_progress = serializers.SerializerMethodField()
    due_date = serializers.DateField(required=False, allow_null=True)

    class Meta:
        model = Task
        fields = [
            'id', 'title', 'description', 'checklist', 'checklist_progress',
            'priority', 'priority_display',
            'status', 'status_display',
            'due_date', 'is_predefined',
            'category', 'category_id',
            'comments', 'comment_count',
            'created_at', 'updated_at', 'completed_at',
        ]
        read_only_fields = [
            'id', 'created_at', 'updated_at', 'completed_at', 'is_predefined',
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        request = self.context.get('request')
        if request and request.user.is_authenticated:
            self.fields['category_id'].queryset = Category.objects.filter(
                user=request.user
            )

    def get_checklist_progress(self, obj):
        checklist = obj.checklist or []
        total = len(checklist)
        if total == 0:
            return {'total': 0, 'completed': 0, 'percentage': 0}
        completed = sum(1 for item in checklist if item.get('completed'))
        return {
            'total': total,
            'completed': completed,
            'percentage': int((completed / total) * 100),
        }

    def validate_title(self, value):
        if not value or not value.strip():
            raise serializers.ValidationError('Title cannot be empty.')
        return value.strip()

    def validate_checklist(self, value):
        if not isinstance(value, list):
            raise serializers.ValidationError('Checklist must be a list.')
        for item in value:
            if not isinstance(item, dict):
                raise serializers.ValidationError('Each checklist item must be an object.')
            if 'text' not in item:
                raise serializers.ValidationError('Each checklist item must have a "text" field.')
        return value


# ============================================================
#  TASK WRITE (CREATE / UPDATE — accepts category_id directly)
# ============================================================

class TaskWriteSerializer(serializers.ModelSerializer):
    """
    Simplified write serializer for task creation/update via the API.
    Accepts category_id directly and handles validation.
    """

    category_id = serializers.IntegerField(required=False, allow_null=True)
    due_date = serializers.DateField(required=False, allow_null=True)

    class Meta:
        model = Task
        fields = [
            'id', 'title', 'description', 'priority', 'status',
            'due_date', 'category_id', 'checklist',
        ]
        read_only_fields = ['id']

    def validate_title(self, value):
        if not value or not value.strip():
            raise serializers.ValidationError('Title cannot be empty.')
        return value.strip()

    def validate_priority(self, value):
        if value not in Task.Priority.values:
            raise serializers.ValidationError(f'Invalid priority. Choose from: {Task.Priority.values}')
        return value

    def validate_status(self, value):
        if value not in Task.Status.values:
            raise serializers.ValidationError(f'Invalid status. Choose from: {Task.Status.values}')
        return value


# ============================================================
#  USER STATS
# ============================================================

class UserStatsSerializer(serializers.Serializer):
    """Serializes aggregated user stats for the /api/v1/stats/ endpoint."""

    total_count = serializers.IntegerField()
    done_count = serializers.IntegerField()
    in_progress_count = serializers.IntegerField()
    backlog_count = serializers.IntegerField()
    to_do_count = serializers.IntegerField()
    on_hold_count = serializers.IntegerField()
    canceled_count = serializers.IntegerField()
    overdue_count = serializers.IntegerField()
    due_today_count = serializers.IntegerField()
    completion_rate = serializers.IntegerField()


# ============================================================
#  PREDEFINED TASK TEMPLATE
# ============================================================

class PreDefinedTaskSerializer(serializers.ModelSerializer):
    """Serializer for the task template library."""

    category_display = serializers.CharField(source='get_category_display', read_only=True)

    class Meta:
        model = PreDefinedTask
        fields = ['id', 'title', 'description', 'category', 'category_display',
                  'suggested_priority', 'icon']


# ============================================================
#  USER PROFILE
# ============================================================

class UserProfileSerializer(serializers.ModelSerializer):
    """Serializer for user settings/preferences."""

    class Meta:
        model = UserProfile
        fields = [
            'theme', 'notify_task_reminders', 'notify_due_date_alerts',
            'notify_app_updates', 'default_task_priority', 'default_task_status',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['created_at', 'updated_at']


# ============================================================
#  DPDP PRIVACY & DATA GOVERNANCE SERIALIZERS
# ============================================================

from .models import ConsentRecord, DataPrincipalNomination, PrivacyGrievance, DataBreachIncident

class ConsentRecordSerializer(serializers.ModelSerializer):
    purpose_display = serializers.CharField(source='get_purpose_display', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)

    class Meta:
        model = ConsentRecord
        fields = [
            'id', 'purpose', 'purpose_display', 'status', 'status_display',
            'notice_version', 'channel', 'granted_at', 'withdrawn_at'
        ]
        read_only_fields = ['id', 'notice_version', 'granted_at', 'withdrawn_at']


class DataPrincipalNominationSerializer(serializers.ModelSerializer):
    class Meta:
        model = DataPrincipalNomination
        fields = [
            'id', 'nominee_name', 'nominee_email', 'nominee_phone',
            'relationship', 'status', 'notes', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class PrivacyGrievanceSerializer(serializers.ModelSerializer):
    category_display = serializers.CharField(source='get_category_display', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)

    class Meta:
        model = PrivacyGrievance
        fields = [
            'ticket_number', 'full_name', 'email', 'category', 'category_display',
            'subject', 'description', 'status', 'status_display',
            'resolution_notes', 'statutory_deadline', 'resolved_at', 'created_at'
        ]
        read_only_fields = ['ticket_number', 'status', 'resolution_notes', 'statutory_deadline', 'resolved_at', 'created_at']

