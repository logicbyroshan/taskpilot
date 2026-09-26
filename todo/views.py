"""
todo/views.py

Template-rendering views for the TaskFarmm web application.

This file is intentionally thin — all business logic lives in services.py.
Views here are responsible only for:
  1. Authentication/permission checking
  2. Calling the appropriate service method
  3. Rendering the correct template with the returned context

JSON/AJAX endpoints for the HTMX frontend are kept here (not in the DRF API)
because they use Django's session auth and return task-specific HTML fragments.
External consumers should use the REST API (/api/v1/).
"""

import os
import re
import csv
import json
import logging
import base64

# Django core
from django.conf import settings
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse
from django.http import JsonResponse, HttpResponse
from django.db.models import Q
from django.views.decorators.http import require_POST, require_http_methods
from django.contrib import messages
from django.contrib.auth import update_session_auth_hash, authenticate, login as auth_login, logout as auth_logout
from django.contrib.auth.models import User
from django.contrib.auth.decorators import login_required
from django.utils import timezone
from django.utils.timesince import timesince
from django.core.files.base import ContentFile

# Local models, forms, and services
from .models import Task, Category, UserProfile, TaskComment, TaskAttachment, Notification
from .forms import (
    TaskForm, CategoryForm, UserUpdateForm, UserProfileForm, 
    PasswordUpdateForm, LoginForm, RegisterForm
)
from .autocorrect import autocorrect_text
from .notifications import NotificationService
from .services import (
    TaskService, CategoryService, ExportService,
    PreDefinedTaskService, StatsService, SubUserService,
)

logger = logging.getLogger('todo')

MAX_ATTACHMENT_SIZE = 10 * 1024 * 1024  # 10 MB
ALLOWED_ATTACHMENT_EXTENSIONS = {
    'jpg', 'jpeg', 'png', 'gif', 'webp', 'svg',
    'pdf', 'doc', 'docx', 'xls', 'xlsx', 'csv', 'txt', 'ppt', 'pptx', 'zip'
}
BLOCKED_ATTACHMENT_EXTENSIONS = {
    'exe', 'bat', 'cmd', 'sh', 'php', 'py', 'js', 'vbs', 'jar', 'scr', 'msi', 'com', 'pif'
}


# ============================================================
#  HELPERS
# ============================================================

def get_or_create_profile(user):
    """Returns or creates the UserProfile for a user."""
    profile, _ = UserProfile.objects.get_or_create(user=user)
    return profile


def get_accessible_task(user, pk):
    """Returns a task accessible by user (owned, shared project, or assigned)."""
    return get_object_or_404(
        Task.objects.filter(
            Q(user=user) | 
            Q(category__members=user) | 
            Q(category__user=user) | 
            Q(assignees=user)
        ).distinct(),
        pk=pk
    )


# ============================================================
#  PAGE VIEWS
# ============================================================

@login_required
def dashboard(request):
    """Displays the main dashboard for an authenticated user with Recent Projects and Team Overview."""
    profile = get_or_create_profile(request.user)
    stats = StatsService.get_stats(request.user)
    recent_tasks = TaskService.get_recent_tasks(request.user)
    recently_completed = TaskService.get_recently_completed(request.user)
    category_stats = CategoryService.get_with_stats(request.user)
    recent_projects = category_stats[:8]
    all_projects_progress = [
        {
            'id': cat.id,
            'name': cat.name,
            'color': cat.color,
            'task_count': cat.task_count,
            'completed_count': cat.completed_count,
            'progress': cat.progress,
        }
        for cat in sorted(category_stats, key=lambda c: c.name)
    ]

    # Sub-user team management data (owner only)
    is_owner = getattr(profile, 'is_owner', True)
    subusers_data = SubUserService.get_subusers_data(request.user) if is_owner else []
    subuser_count = len(subusers_data)
    max_subusers = SubUserService.MAX_SUBUSERS

    context = {
        'total_count': stats['total_count'],
        'backlog_count': stats['backlog_count'],
        'to_do_count': stats['to_do_count'],
        'in_progress_count': stats['in_progress_count'],
        'done_count': stats['done_count'],
        'on_hold_count': stats['on_hold_count'],
        'canceled_count': stats['canceled_count'],
        'overdue_count': stats['overdue_count'],
        'due_today_count': stats['due_today_count'],
        'completion_rate': stats['completion_rate'],
        'recent_tasks': recent_tasks,
        'recently_completed_tasks': recently_completed,
        'recent_projects': recent_projects,
        'categories': category_stats,
        'all_projects_progress': all_projects_progress,
        'is_owner': is_owner,
        'subusers': subusers_data,
        'subuser_count': subuser_count,
        'max_subusers': max_subusers,
        'remaining_subusers': max(0, max_subusers - subuser_count),
        'active_page': 'dashboard',
    }
    return render(request, 'todo/index.html', context)



@login_required
def manage_tasks(request):
    """Redirects legacy manage-tasks requests to the centralized Kanban Board."""
    project = request.GET.get('project')
    if project:
        return redirect(f"{reverse('manage_kanban')}?project={project}")
    return redirect('manage_kanban')


@login_required
def task_categories(request):
    """Displays all projects/categories for the current user."""
    categories = CategoryService.get_with_stats(request.user)
    form = CategoryForm()
    context = {
        'categories': categories,
        'form': form,
        'active_page': 'task_categories',
    }
    return render(request, 'todo/manage-projects.html', context)


@login_required
def manage_kanban(request):
    """Displays the Kanban board with single-project view and dynamic workflow template."""
    project_id = request.GET.get('project')
    data = TaskService.get_kanban_columns(request.user, project_id)

    board_members = []
    if data['selected_project']:
        proj = data['selected_project']
        if proj.user:
            board_members.append(proj.user)
        for m in proj.members.all():
            if m not in board_members:
                board_members.append(m)
    else:
        board_members.append(request.user)

    context = {
        'all_projects': data['all_projects'],
        'selected_project': data['selected_project'],
        'selected_project_id': data['selected_project_id'],
        'has_projects': data['has_projects'],
        'active_columns': data['active_columns'],
        'board_members': board_members,
        'backlog_tasks': data['columns']['backlog'],
        'to_do_tasks': data['columns']['not-started'],
        'in_progress_tasks': data['columns']['in-progress'],
        'done_tasks': data['columns']['completed'],
        'on_hold_tasks': data['columns']['on-hold'],
        'canceled_tasks': data['columns']['canceled'],
        'active_page': 'manage_kanban',
    }
    return render(request, 'todo/kanban.html', context)


@login_required
def settings_page(request):
    """Handles the user settings page — profile, password, preferences, and danger zone."""
    profile = get_or_create_profile(request.user)

    if request.method == 'POST':
        action = request.POST.get('action', 'profile')

        if action == 'profile':
            u_form = UserUpdateForm(request.POST, instance=request.user)
            if u_form.is_valid():
                u_form.save()
                messages.success(request, 'Profile updated successfully!')
                return redirect('settings')
            p_form = UserProfileForm(instance=profile)
            pw_form = PasswordUpdateForm(request.user)

        elif action == 'preferences':
            p_form = UserProfileForm(request.POST, instance=profile)
            if p_form.is_valid():
                p_form.save()
                messages.success(request, 'Preferences saved successfully!')
                return redirect('settings')
            u_form = UserUpdateForm(instance=request.user)
            pw_form = PasswordUpdateForm(request.user)

        elif action == 'password':
            pw_form = PasswordUpdateForm(request.user, request.POST)
            if pw_form.is_valid():
                user = pw_form.save()
                update_session_auth_hash(request, user)
                messages.success(request, 'Password changed successfully!')
                return redirect('settings')
            u_form = UserUpdateForm(instance=request.user)
            p_form = UserProfileForm(instance=profile)

        elif action == 'clear_tasks':
            if request.POST.get('confirm_clear') == 'yes':
                Task.objects.filter(user=request.user).delete()
                messages.success(request, 'All tasks cleared successfully. Projects remain intact.')
            else:
                messages.error(request, 'Confirmation not provided. No tasks were deleted.')
            return redirect('settings')

        elif action in ('clear_all', 'clear_data'):
            if request.POST.get('confirm_clear') == 'yes':
                Task.objects.filter(user=request.user).delete()
                Category.objects.filter(user=request.user).delete()
                messages.success(request, 'All data (tasks and projects) cleared successfully.')
            else:
                messages.error(request, 'Confirmation not provided. No data was deleted.')
            return redirect('settings')

        else:
            u_form = UserUpdateForm(instance=request.user)
            p_form = UserProfileForm(instance=profile)
            pw_form = PasswordUpdateForm(request.user)
    else:
        u_form = UserUpdateForm(instance=request.user)
        p_form = UserProfileForm(instance=profile)
        pw_form = PasswordUpdateForm(request.user)

    context = {
        'active_page': 'settings',
        'u_form': u_form,
        'p_form': p_form,
        'pw_form': pw_form,
        'profile': profile,
    }
    return render(request, 'todo/settings.html', context)


def login_view(request):
    """
    Dedicated TaskFarmm Authentication Page.
    Handles user login with showcase of platform features and instant demo logins.
    """
    if request.user.is_authenticated:
        return redirect('dashboard')

    form = LoginForm(request.POST or None)
    error_message = None

    if request.method == 'POST' and form.is_valid():
        username_or_email = form.cleaned_data['username'].strip()
        password = form.cleaned_data['password']
        remember_me = form.cleaned_data.get('remember_me', True)

        # Allow logging in via username or email
        user_obj = None
        if '@' in username_or_email:
            user_obj = User.objects.filter(email__iexact=username_or_email).first()
        if not user_obj:
            user_obj = User.objects.filter(username__iexact=username_or_email).first()

        user = None
        if user_obj:
            user = authenticate(request, username=user_obj.username, password=password)

        if user is not None:
            auth_login(request, user)
            if not remember_me:
                request.session.set_expiry(0)  # Session expires on browser close
            else:
                request.session.set_expiry(1209600)  # 2 weeks
            messages.success(request, f'Welcome back, {user.first_name or user.username}!')
            next_url = request.GET.get('next') or request.POST.get('next') or 'dashboard'
            return redirect(next_url)
        else:
            error_message = "Invalid username/email or password. Please check your credentials."

    context = {
        'form': form,
        'error_message': error_message,
        'next': request.GET.get('next', ''),
        'active_auth_tab': 'login',
    }
    return render(request, 'todo/auth/login.html', context)


def register_view(request):
    """
    Dedicated TaskFarmm Registration Page.
    Creates a new user account with default setup, automatic login, and dashboard redirect.
    """
    if request.user.is_authenticated:
        return redirect('dashboard')

    form = RegisterForm(request.POST or None)

    if request.method == 'POST' and form.is_valid():
        user = form.save(commit=False)
        user.set_password(form.cleaned_data['password'])
        user.save()

        # Create default UserProfile
        profile, _ = UserProfile.objects.get_or_create(user=user)
        opt_in_notifications = form.cleaned_data.get('dpdp_consent_notifications', True)
        opt_in_ai = form.cleaned_data.get('dpdp_consent_ai', True)

        if not opt_in_notifications:
            profile.notify_task_reminders = False
            profile.notify_due_date_alerts = False
            profile.save(update_fields=['notify_task_reminders', 'notify_due_date_alerts'])

        # Record DPDP Act 2023 Consent Records
        try:
            from .privacy_services import ConsentService
            ConsentService.record_initial_user_consents(
                user=user,
                opt_in_notifications=opt_in_notifications,
                opt_in_ai=opt_in_ai,
                opt_in_updates=False,
                request=request,
                channel='web_registration'
            )
        except Exception as e:
            logger.warning('Could not record initial DPDP consents on registration: %s', e)

        # Create default starter project
        Category.objects.create(
            user=user,
            name="General Tasks",
            color="#3b82f6",
            description="Default workspace for your tasks & quick ideas.",
            board_template=Category.BoardTemplate.SMART,
        )

        auth_login(request, user, backend='django.contrib.auth.backends.ModelBackend')
        messages.success(request, f'Welcome to TaskFarmm, {user.first_name or user.username}! Your workspace is ready.')
        return redirect('dashboard')

    context = {
        'form': form,
        'active_auth_tab': 'register',
    }
    return render(request, 'todo/auth/register.html', context)


@login_required
def logout_view(request):
    """Logs out the user and redirects to the login page."""
    auth_logout(request)
    messages.info(request, "You have been logged out successfully.")
    return redirect('login')


def switch_user(request):
    """Allows instant switching between collaborator profiles for multi-user live testing (Debug/Demo only)."""
    if not (settings.DEBUG and getattr(settings, 'ENABLE_DEMO_AUTH', False)):
        return JsonResponse({'success': False, 'message': 'Demo user switching is disabled in production.'}, status=403)

    from django.contrib.auth import login as auth_login
    from django.contrib.auth.models import User as AuthUser

    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            username = data.get('username', '').strip()
        except Exception:
            username = request.POST.get('username', '').strip()

        if not username:
            return JsonResponse({'success': False, 'message': 'Username required.'}, status=400)

        allowed_demo_users = {
            'prakash_ahuja': ('Prakash', 'Ahuja'),
            'adarsh_computer': ('Adarsh', 'Computer'),
            'kamal_dewnani': ('Kamal', 'Dewnani'),
            'roshan_damor': ('Roshan', 'Damor'),
            'demo_user': ('Demo', 'User')
        }

        if username not in allowed_demo_users:
            return JsonResponse({'success': False, 'message': 'Only pre-configured demo accounts can be switched to.'}, status=400)

        first_name, last_name = allowed_demo_users[username]

        user, _ = AuthUser.objects.get_or_create(
            username=username,
            defaults={
                'first_name': first_name,
                'last_name': last_name,
                'email': f'{username}@taskfarm.com'
            }
        )

        if user.is_staff or user.is_superuser:
            return JsonResponse({'success': False, 'message': 'Cannot switch to administrative accounts.'}, status=403)

        auth_login(request, user, backend='django.contrib.auth.backends.ModelBackend')
        return JsonResponse({
            'success': True,
            'message': f'Switched to {user.username}',
            'user': {
                'id': user.id,
                'username': user.username,
                'initials': user.username[:2].upper()
            }
        })
    return JsonResponse({'success': False, 'message': 'POST required.'}, status=405)


@login_required
def ai_assistant_page(request):
    """Renders the AI Assistant page."""
    projects = CategoryService.get_with_stats(request.user)
    recent_tasks = TaskService.get_recent_tasks(request.user, limit=10)
    context = {
        'active_page': 'ai_assistant',
        'projects': projects,
        'recent_tasks': recent_tasks,
    }
    return render(request, 'todo/ai_assistant.html', context)


# ============================================================
#  TASK CRUD (HTMX / AJAX — session auth)
# ============================================================

@login_required
def task_detail(request, pk):
    """Returns task detail as JSON for the Trello modal."""
    task = get_accessible_task(request.user, pk)

    is_ajax = (
        request.headers.get('X-Requested-With') == 'XMLHttpRequest'
        or request.GET.get('format') == 'json'
    )
    if is_ajax:
        data = TaskService.get_task_detail_data(task)
        return JsonResponse({'success': True, 'task': data})
    return render(request, 'todo/task_detail.html', {'task': task})


@login_required
def task_create(request):
    """Creates a task via AJAX POST or JSON payload."""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'message': 'POST required.'}, status=405)

    if not SubUserService.can_manage_tasks(request.user):
        return JsonResponse({'success': False, 'message': 'Permission denied. Viewers cannot create tasks.'}, status=403)

    post_data = request.POST
    if request.content_type and 'application/json' in request.content_type:
        try:
            post_data = json.loads(request.body)
        except Exception:
            post_data = request.POST

    form = TaskForm(post_data, user=request.user)
    if form.is_valid():
        task = form.save(commit=False)
        task.user = request.user
        task.save()
        return JsonResponse({
            'success': True,
            'message': 'Task created successfully!',
            'task': {
                'id': task.id,
                'title': task.title,
                'status': task.status,
                'priority': task.priority,
                'category_id': task.category_id,
                'description': task.description or '',
                'user': task.user.username,
                'due_date': task.due_date.isoformat() if task.due_date else None,
                'checklist': task.checklist or [],
            }
        })
    return JsonResponse({'success': False, 'errors': form.errors}, status=400)


@login_required
def task_update(request, pk):
    """
    Updates a task via JSON body (Trello modal) or form POST (kanban drag-drop).
    GET: returns task JSON for Trello modal population.
    POST (JSON body): partial update for live editing.
    POST (form): quick status update or full form update.
    """
    task = get_accessible_task(request.user, pk)

    if request.method == 'GET':
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            data = TaskService.get_task_detail_data(task)
            return JsonResponse({'success': True, 'task': data})
        return JsonResponse({'success': False, 'message': 'Ajax GET only.'}, status=400)

    if request.method == 'POST':
        if not SubUserService.can_manage_tasks(request.user):
            return JsonResponse({'success': False, 'message': 'Permission denied. Viewers cannot update tasks.'}, status=403)

        # JSON body from Trello modal live edits
        if request.content_type and 'application/json' in request.content_type:
            try:
                data = json.loads(request.body)
                task = TaskService.partial_update_task(task, data, request.user)
                return JsonResponse({
                    'success': True,
                    'message': 'Task updated!',
                    'task': TaskService.get_task_detail_data(task)
                })
            except Exception as e:
                logger.warning('Task update failed: pk=%d error=%s', pk, e)
                return JsonResponse({'success': False, 'error': str(e)}, status=400)

        # Quick status update (kanban drag-drop)
        new_status = request.POST.get('status')
        if new_status and new_status in Task.Status.values and 'title' not in request.POST:
            task.status = new_status
            task.save(update_fields=['status', 'updated_at'])
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return JsonResponse({'success': True, 'message': 'Status updated!'})
            return redirect(request.META.get('HTTP_REFERER', 'dashboard'))

        # Full form update
        form = TaskForm(request.POST, instance=task, user=request.user)
        if form.is_valid():
            form.save()
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return JsonResponse({'success': True, 'message': 'Task updated!'})
            return redirect(request.META.get('HTTP_REFERER', 'dashboard'))

        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({'success': False, 'errors': form.errors}, status=400)
        return redirect(request.META.get('HTTP_REFERER', 'dashboard'))

    return JsonResponse({'success': False, 'message': 'Invalid method.'}, status=405)


@login_required
@require_http_methods(['POST'])
def task_add_comment(request, pk):
    """Adds a new activity comment to a task."""
    if not SubUserService.can_manage_tasks(request.user):
        return JsonResponse({'success': False, 'error': 'Permission denied. Viewers cannot post comments.'}, status=403)

    task = get_accessible_task(request.user, pk)
    try:
        data = json.loads(request.body)
        content = data.get('content', '').strip()
    except (json.JSONDecodeError, AttributeError):
        content = request.POST.get('content', '').strip()

    try:
        comment = TaskService.add_comment(task, request.user, content)
    except ValueError as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=400)

    return JsonResponse({
        'success': True,
        'comment': {
            'id': comment.id,
            'user': comment.user.username,
            'content': comment.content,
            'created_at': comment.created_at.strftime('%d %b %Y, %H:%M'),
            'time_ago': 'Just now',
        }
    })


@login_required
@require_http_methods(['POST', 'PUT'])
def task_comment_edit(request, pk):
    """Edits a task comment (comment author only)."""
    comment = get_object_or_404(
        TaskComment.objects.filter(user=request.user),
        pk=pk
    )
    try:
        data = json.loads(request.body)
        content = data.get('content', '').strip()
    except (json.JSONDecodeError, AttributeError):
        content = request.POST.get('content', '').strip()

    if not content:
        return JsonResponse({'success': False, 'error': 'Comment content cannot be empty.'}, status=400)

    comment.content = content
    comment.save(update_fields=['content', 'updated_at'])
    return JsonResponse({
        'success': True,
        'message': 'Comment updated.',
        'comment': {
            'id': comment.id,
            'user': comment.user.username,
            'content': comment.content,
            'is_edited': True,
        }
    })


@login_required
@require_http_methods(['POST', 'DELETE'])
def task_comment_delete(request, pk):
    """Deletes a task comment (comment author or task owner)."""
    comment = get_object_or_404(
        TaskComment.objects.filter(
            Q(user=request.user) | 
            Q(task__user=request.user) | 
            Q(task__category__user=request.user)
        ).distinct(),
        pk=pk
    )
    comment.delete()
    return JsonResponse({'success': True, 'message': 'Comment deleted.'})


@login_required
@require_http_methods(['POST'])
def task_update_checklist(request, pk):
    """Replaces task checklist."""
    if not SubUserService.can_manage_tasks(request.user):
        return JsonResponse({'success': False, 'message': 'Permission denied. Viewers cannot modify checklists.'}, status=403)

    task = get_accessible_task(request.user, pk)
    try:
        data = json.loads(request.body)
        checklist = data.get('checklist', [])
    except (json.JSONDecodeError, AttributeError):
        checklist = []
    TaskService.update_checklist(task, checklist)
    return JsonResponse({'success': True, 'message': 'Checklist updated!'})


@login_required
@require_http_methods(['POST'])
def task_upload_attachment(request, pk):
    """
    Uploads one or multiple files or pasted image (base64) to a task with strict validation.
    """
    if not SubUserService.can_manage_tasks(request.user):
        return JsonResponse({'success': False, 'error': 'Permission denied. Viewers cannot upload attachments.'}, status=403)

    task = get_accessible_task(request.user, pk)
    attachments_created = []

    def validate_and_sanitize(uploaded_file, filename):
        # Sanitize filename
        safe_filename = os.path.basename(filename).strip()
        safe_filename = re.sub(r'[\r\n\t\x00]', '', safe_filename)
        if not safe_filename:
            safe_filename = f'attachment_{timezone.now().strftime("%Y%m%d_%H%M%S")}.dat'

        # Check size
        file_size = getattr(uploaded_file, 'size', None)
        if file_size is None:
            try:
                if hasattr(uploaded_file, 'file'):
                    cur = uploaded_file.file.tell()
                    uploaded_file.file.seek(0, os.SEEK_END)
                    file_size = uploaded_file.file.tell()
                    uploaded_file.file.seek(cur)
                else:
                    file_size = len(uploaded_file)
            except Exception:
                file_size = 0
        if file_size > MAX_ATTACHMENT_SIZE:
            raise ValueError(f'File "{safe_filename}" exceeds the maximum allowed size of 10 MB.')

        # Check extension
        ext = safe_filename.rsplit('.', 1)[-1].lower() if '.' in safe_filename else ''
        if ext in BLOCKED_ATTACHMENT_EXTENSIONS or (ext and ext not in ALLOWED_ATTACHMENT_EXTENSIONS):
            raise ValueError(f'File type ".{ext}" is not permitted for upload.')

        return safe_filename

    # Handle standard multipart file uploads
    if request.FILES:
        files = request.FILES.getlist('files') or ([request.FILES['file']] if 'file' in request.FILES else [])
        for f in files:
            try:
                safe_name = validate_and_sanitize(f, f.name)
                f.name = safe_name
                att = TaskService.add_attachment(task, request.user, f)
                attachments_created.append(att)
            except ValueError as e:
                return JsonResponse({'success': False, 'error': str(e)}, status=400)

    # Handle JSON base64 pasted images
    elif request.body:
        try:
            data = json.loads(request.body)
            image_data = data.get('image_data', '')
            raw_filename = data.get('filename', f'pasted_image_{timezone.now().strftime("%Y%m%d_%H%M%S")}.png')
            if image_data and ';base64,' in image_data:
                fmt, imgstr = image_data.split(';base64,')
                ext = fmt.split('/')[-1].lower() if '/' in fmt else 'png'
                if ext not in ('png', 'jpg', 'jpeg', 'gif', 'webp'):
                    ext = 'png'
                if not raw_filename.lower().endswith(f'.{ext}'):
                    raw_filename = f'{raw_filename}.{ext}'

                decoded_data = base64.b64decode(imgstr)
                if len(decoded_data) > MAX_ATTACHMENT_SIZE:
                    return JsonResponse({'success': False, 'error': 'Pasted image exceeds 10 MB limit.'}, status=400)

                content = ContentFile(decoded_data, name=raw_filename)
                safe_name = validate_and_sanitize(content, raw_filename)
                content.name = safe_name
                att = TaskService.add_attachment(task, request.user, content)
                attachments_created.append(att)
        except Exception as e:
            logger.warning('Failed to process pasted image: %s', e)
            return JsonResponse({'success': False, 'error': str(e)}, status=400)

    if not attachments_created:
        return JsonResponse({'success': False, 'error': 'No file provided.'}, status=400)

    task_data = TaskService.get_task_detail_data(task)
    return JsonResponse({
        'success': True,
        'message': f'{len(attachments_created)} attachment(s) uploaded.',
        'attachments': task_data['attachments'],
    })


@login_required
@require_http_methods(['POST', 'DELETE'])
def task_delete_attachment(request, pk):
    """Deletes a task attachment (attachment author or task owner)."""
    attachment = get_object_or_404(
        TaskAttachment.objects.filter(
            Q(user=request.user) | 
            Q(task__user=request.user) | 
            Q(task__category__user=request.user)
        ).distinct(),
        pk=pk
    )
    task = attachment.task
    attachment.file.delete(save=False)
    attachment.delete()
    task_data = TaskService.get_task_detail_data(task)
    return JsonResponse({
        'success': True,
        'message': 'Attachment deleted.',
        'attachments': task_data['attachments']
    })


@login_required
@require_http_methods(['POST'])
def task_toggle_status(request, pk):
    """Toggles task status between DONE and TO_DO."""
    if not SubUserService.can_manage_tasks(request.user):
        return JsonResponse({'success': False, 'message': 'Permission denied. Viewers cannot modify task status.'}, status=403)

    task = get_accessible_task(request.user, pk)
    TaskService.toggle_status(task)
    if request.headers.get('HX-Request'):
        return render(request, 'todo/components/task_card.html', {'task': task})

    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return JsonResponse({
            'success': True,
            'task': {
                'id': task.id,
                'status': task.status,
                'is_completed': task.status == Task.Status.DONE,
            }
        })
    return redirect(request.META.get('HTTP_REFERER', 'dashboard'))


@login_required
def task_delete(request, pk):
    """Deletes a task."""
    if not SubUserService.can_manage_tasks(request.user):
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({'success': False, 'message': 'Permission denied. Viewers cannot delete tasks.'}, status=403)
        messages.error(request, 'Permission denied. Viewers cannot delete tasks.')
        return redirect('manage_kanban')

    task = get_object_or_404(
        Task.objects.filter(Q(user=request.user) | Q(category__user=request.user)).distinct(),
        pk=pk
    )
    if request.method == 'POST':
        title = task.title
        task.delete()
        if request.headers.get('HX-Request'):
            return HttpResponse('', status=200)
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({'success': True, 'message': f'Task "{title}" deleted.'})
        messages.success(request, f'Task "{title}" deleted.')
        return redirect('manage_kanban')
    return render(request, 'todo/confirm_delete.html', {'object': task, 'type': 'task'})


# ============================================================
#  CATEGORY / PROJECT CRUD & SHARING
# ============================================================

@login_required
def category_create(request):
    """Creates a new project/category."""
    if not SubUserService.can_create_projects(request.user):
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({'success': False, 'message': 'Permission denied. You do not have permission to create projects.'}, status=403)
        messages.error(request, 'Permission denied. You do not have permission to create projects.')
        return redirect('task_categories')

    if request.method == 'POST':
        post_data = request.POST
        if request.content_type and 'application/json' in request.content_type:
            try:
                post_data = json.loads(request.body)
            except Exception:
                post_data = request.POST

        form = CategoryForm(post_data)
        if form.is_valid():
            category = form.save(commit=False)
            category.user = request.user
            category.save()
            category.ensure_share_token()
            is_ajax_or_json = (
                request.headers.get('X-Requested-With') == 'XMLHttpRequest'
                or (request.content_type and 'application/json' in request.content_type)
            )
            if is_ajax_or_json:
                return JsonResponse({
                    'success': True,
                    'message': f'Project "{category.name}" created!',
                    'category': {
                        'id': category.id,
                        'name': category.name,
                        'color': category.color,
                        'share_token': category.share_token,
                    }
                })
            messages.success(request, f'Project "{category.name}" created.')
        else:
            is_ajax_or_json = (
                request.headers.get('X-Requested-With') == 'XMLHttpRequest'
                or (request.content_type and 'application/json' in request.content_type)
            )
            if is_ajax_or_json:
                return JsonResponse({'success': False, 'errors': form.errors}, status=400)
    return redirect('task_categories')


@login_required
def category_update(request, pk):
    """Updates an existing category."""
    category = get_object_or_404(Category, pk=pk, user=request.user)
    if request.method == 'POST':
        form = CategoryForm(request.POST, instance=category)
        if form.is_valid():
            form.save()
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                return JsonResponse({'success': True, 'message': f'Project "{category.name}" updated.'})
            messages.success(request, f'Project "{category.name}" updated.')
    return redirect('task_categories')


@login_required
def category_delete(request, pk):
    """Deletes a category."""
    category = get_object_or_404(Category, pk=pk, user=request.user)
    if request.method == 'POST':
        name = category.name
        category.delete()
        if request.headers.get('HX-Request'):
            return HttpResponse('', status=200)
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({'success': True, 'message': f'Project "{name}" deleted.'})
        messages.success(request, f'Project "{name}" deleted.')
        return redirect('task_categories')
    return render(request, 'todo/confirm_delete.html', {'object': category, 'type': 'project'})


@login_required
@require_http_methods(['POST'])
def category_rename_column(request, pk):
    """Renames a column for a specific project."""
    category = get_object_or_404(
        Category.objects.filter(Q(user=request.user) | Q(members=request.user)).distinct(),
        pk=pk
    )
    try:
        data = json.loads(request.body)
        col_key = data.get('column_key')
        new_title = data.get('title', '').strip()
    except Exception:
        col_key = request.POST.get('column_key')
        new_title = request.POST.get('title', '').strip()

    if not col_key or not new_title:
        return JsonResponse({'success': False, 'error': 'Invalid column key or title.'}, status=400)

    TaskService.update_project_column_name(category, col_key, new_title)
    return JsonResponse({
        'success': True,
        'message': f'Column renamed to "{new_title}".',
        'column_key': col_key,
        'title': new_title
    })


@login_required
def project_share(request, pk):
    """
    GET: Returns project sharing info (share URL, members, is_owner).
    POST: Adds or removes project members by username/email.
    """
    from django.contrib.auth.models import User as AuthUser
    category = get_object_or_404(
        Category.objects.filter(Q(user=request.user) | Q(members=request.user)).distinct(),
        pk=pk
    )
    token = category.ensure_share_token()
    share_url = request.build_absolute_uri(f"/project/join/{token}/")
    is_owner = (category.user_id == request.user.id)

    if request.method == 'POST':
        if not is_owner:
            return JsonResponse({'success': False, 'message': 'Only project owner can manage members.'}, status=403)
        try:
            data = json.loads(request.body)
        except Exception:
            data = request.POST

        action = data.get('action')
        if action == 'add_member':
            user_id = data.get('user_id')
            query = data.get('username', '').strip()
            if user_id:
                user_to_add = AuthUser.objects.filter(pk=user_id).first()
            else:
                user_to_add = AuthUser.objects.filter(Q(username__iexact=query) | Q(email__iexact=query)).first()

            if not user_to_add:
                return JsonResponse({'success': False, 'message': f'User not found.'}, status=404)
            if user_to_add.id == category.user_id:
                return JsonResponse({'success': False, 'message': 'User is already the project owner.'}, status=400)
            category.members.add(user_to_add)
            try:
                NotificationService.queue_notification(
                    user=user_to_add,
                    event_type=Notification.EventType.PROJECT_SHARED,
                    title=f"Added to project: {category.name}",
                    message=f"{request.user.get_full_name() or request.user.username} added you to project '{category.name}'",
                    action_url=f"/kanban/?project={category.id}",
                    context={
                        'project_name': category.name,
                        'inviter_name': request.user.get_full_name() or request.user.username,
                    }
                )
            except Exception as e:
                logger.warning('Failed to queue project shared notification: %s', e)

            return JsonResponse({
                'success': True,
                'message': f'{user_to_add.username} added to project!',
                'member': {
                    'id': user_to_add.id,
                    'username': user_to_add.username,
                    'initials': user_to_add.username[:2].upper()
                }
            })
        elif action == 'remove_member':
            member_id = data.get('member_id')
            user_to_remove = AuthUser.objects.filter(pk=member_id).first()
            if user_to_remove:
                category.members.remove(user_to_remove)
                return JsonResponse({'success': True, 'message': f'{user_to_remove.username} removed from project.'})
            return JsonResponse({'success': False, 'message': 'Member not found.'}, status=404)

    members = [
        {
            'id': m.id,
            'username': m.username,
            'name': m.get_full_name() or m.username,
            'initials': m.username[:2].upper(),
            'is_owner': False
        }
        for m in category.members.all()
    ]
    owner_info = {
        'id': category.user.id,
        'username': category.user.username,
        'name': category.user.get_full_name() or category.user.username,
        'initials': category.user.username[:2].upper(),
        'is_owner': True
    }

    available_subusers = []
    if is_owner:
        subs = SubUserService.get_subusers(request.user).exclude(id__in=category.members.values_list('id', flat=True))
        available_subusers = [
            {'id': u.id, 'username': u.username, 'name': u.get_full_name() or u.username}
            for u in subs
        ]

    return JsonResponse({
        'success': True,
        'project_id': category.id,
        'project_name': category.name,
        'share_url': share_url,
        'is_owner': is_owner,
        'owner': owner_info,
        'members': members,
        'available_subusers': available_subusers
    })


@login_required
def project_join(request, token):
    """
    Allows a user with an invite/share link to join a shared project.
    """
    category = get_object_or_404(Category, share_token=token)
    if category.user != request.user and not category.members.filter(pk=request.user.pk).exists():
        category.members.add(request.user)
        messages.success(request, f'🎉 You have joined project "{category.name}"!')
    else:
        messages.info(request, f'You are currently viewing project "{category.name}".')

    return redirect(f"/kanban/?project={category.id}")


# ============================================================
#  PREDEFINED TASK TEMPLATES
# ============================================================

@login_required
def predefined_tasks_api(request):
    """Returns predefined task templates filtered by category."""
    from .models import PreDefinedTask
    category = request.GET.get('category', 'all')
    tasks_qs = PreDefinedTaskService.get_templates(category)

    tasks_data = [
        {
            'id': t.id,
            'title': t.title,
            'description': t.description or '',
            'category': t.category,
            'category_display': t.get_category_display(),
            'suggested_priority': t.suggested_priority,
            'icon': t.icon,
        }
        for t in tasks_qs
    ]
    categories = [
        {'value': c[0], 'label': c[1]}
        for c in PreDefinedTask.Category.choices
    ]
    return JsonResponse({'success': True, 'tasks': tasks_data, 'categories': categories})


@login_required
def add_predefined_task(request):
    """Adds a predefined task template to the user's task pool."""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required'}, status=405)
    try:
        data = json.loads(request.body)
        task = PreDefinedTaskService.add_to_user_tasks(
            user=request.user,
            predefined_id=data.get('predefined_id'),
            category_id=data.get('category_id'),
        )
        return JsonResponse({
            'success': True,
            'message': f'Task "{task.title}" added!',
            'task_id': task.id,
        })
    except Exception as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=400)


# ============================================================
#  STATS & EXPORT (legacy session-auth endpoints, kept for HTMX widgets)
# ============================================================

@login_required
def stats_api(request):
    """Returns aggregated stats. Also available at /api/v1/stats/ with JWT auth."""
    stats = StatsService.get_stats(request.user)
    return JsonResponse({'success': True, 'stats': stats})


@login_required
def tasks_export_api(request):
    """Exports all tasks as JSON or CSV."""
    fmt = request.GET.get('format', 'json').lower()
    if fmt == 'csv':
        header, rows = ExportService.tasks_to_csv_rows(request.user)
        response = HttpResponse(content_type='text/csv; charset=utf-8')
        response['Content-Disposition'] = 'attachment; filename="taskfarmm_export.csv"'
        writer = csv.writer(response)
        writer.writerow(header)
        writer.writerows(rows)
        return response

    data = ExportService.tasks_to_dict_list(request.user)
    return JsonResponse({'success': True, 'tasks': data, 'total': len(data)})


# ============================================================
#  AI ASSISTANT ENDPOINTS
# ============================================================

@login_required
def api_ai_suggest(request):
    """
    AI Task & Project Assistant endpoint.
    Generates a suggested action plan from a user prompt.
    """
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST method required'}, status=405)

    try:
        data = json.loads(request.body)
        prompt = data.get('prompt', '').strip()

        if not prompt:
            return JsonResponse({'success': False, 'error': 'Empty prompt'}, status=400)

        prompt_lower = prompt.lower()

        if any(kw in prompt_lower for kw in ['website', 'launch', 'app']):
            title = "Launch Strategy & Production Readiness"
            suggestion = (
                "1. Finalize DNS records & SSL certificate configuration.\n"
                "2. Run cross-browser compatibility and lighthouse performance audit.\n"
                "3. Execute production database migrations.\n"
                "4. Verify exception logging & error reporting setup.\n"
                "5. Set up monitoring & uptime alerts."
            )
        elif any(kw in prompt_lower for kw in ['subtask', 'breakdown', 'feature']):
            title = f"Task Breakdown: {prompt[:30]}..."
            suggestion = (
                "Recommended Subtasks:\n"
                "- API Endpoint setup with request validation\n"
                "- Database schema migrations & indexing\n"
                "- Frontend UI component integration\n"
                "- Write unit and integration tests\n"
                "- Code review and deployment"
            )
        elif any(kw in prompt_lower for kw in ['marketing', 'campaign']):
            title = "Marketing Campaign Execution Plan"
            suggestion = (
                "1. Define target audience and campaign objectives.\n"
                "2. Create content calendar and asset list.\n"
                "3. Set up ad creatives and A/B tests.\n"
                "4. Launch campaign and monitor metrics.\n"
                "5. Analyze results and optimize."
            )
        else:
            title = f"AI Workflow Task: {prompt[:35]}"
            suggestion = (
                f"AI Action Plan for '{prompt}':\n"
                "- Priority: High\n"
                "- Recommended Timeline: Complete within 48 hours\n"
                "- Suggested Action: Create task, assign project category, and review progress.\n"
                "- Next Step: Break into subtasks if the scope is large."
            )

        return JsonResponse({
            'success': True,
            'title': title,
            'suggestion': suggestion,
            'description': suggestion,
            'prompt': prompt,
        })
    except json.JSONDecodeError:
        return JsonResponse({'success': False, 'error': 'Invalid JSON body'}, status=400)
    except Exception as e:
        logger.error('AI suggest error: %s', e)
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@login_required
def ai_create_task(request):
    """Creates a task from AI suggestion data."""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required'}, status=405)
    try:
        data = json.loads(request.body)
        title = data.get('title', '').strip()
        if not title:
            return JsonResponse({'success': False, 'error': 'Title is required'}, status=400)

        category = None
        if data.get('category_id'):
            category = Category.objects.filter(pk=data['category_id'], user=request.user).first()

        task = Task.objects.create(
            user=request.user,
            title=title,
            description=data.get('description', '').strip(),
            priority=data.get('priority', 'moderate') if data.get('priority') in Task.Priority.values else 'moderate',
            status=data.get('status', 'not-started') if data.get('status') in Task.Status.values else 'not-started',
            category=category,
        )
        return JsonResponse({
            'success': True,
            'message': f'Task "{task.title}" created!',
            'task_id': task.id,
            'task_title': task.title,
        })
    except Exception as e:
        logger.error('AI create task error: %s', e)
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@login_required
def ai_create_project(request):
    """Creates a project with optional tasks from AI suggestion data."""
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required'}, status=405)
    try:
        data = json.loads(request.body)
        name = data.get('name', '').strip()
        if not name:
            return JsonResponse({'success': False, 'error': 'Project name is required'}, status=400)

        project = CategoryService.create_category(
            user=request.user,
            name=name,
            color=data.get('color', '#3b82f6'),
            description=data.get('description', ''),
        )

        created_tasks = []
        for task_data in data.get('tasks', [])[:10]:  # Max 10 tasks
            task_title = task_data if isinstance(task_data, str) else task_data.get('title', '')
            task_priority = 'moderate' if isinstance(task_data, str) else task_data.get('priority', 'moderate')
            if task_title:
                t = Task.objects.create(
                    user=request.user,
                    title=task_title,
                    priority=task_priority,
                    status='not-started',
                    category=project,
                )
                created_tasks.append({'id': t.id, 'title': t.title})

        return JsonResponse({
            'success': True,
            'message': f'Project "{project.name}" created with {len(created_tasks)} tasks!',
            'project_id': project.id,
            'project_name': project.name,
            'tasks_created': created_tasks,
        })
    except Exception as e:
        logger.error('AI create project error: %s', e)
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@login_required
def api_autocorrect(request):
    """
    Intelligent multi-lingual spell correction and text normalization
    endpoint supporting English, Hindi, and Hinglish.
    """
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required'}, status=405)
    try:
        data = json.loads(request.body)
        text = data.get('text', '')
        result = autocorrect_text(text)
        return JsonResponse({
            'success': True,
            'original': result['original'],
            'corrected': result['corrected'],
            'changed': result['changed'],
        })
    except Exception as e:
        logger.error('Auto-correct error: %s', e)
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


# ============================================================
#  NOTIFICATION AJAX ENDPOINTS
# ============================================================

@login_required
def api_notifications_list(request):
    """Returns recent in-app notifications and unread counter for current user."""
    notifications = Notification.objects.filter(user=request.user).order_by('-created_at')[:20]
    unread_count = Notification.objects.filter(user=request.user, is_read=False).count()
    data = [
        {
            'id': n.id,
            'event_type': n.event_type,
            'title': n.title,
            'message': n.message,
            'action_url': n.action_url,
            'is_read': n.is_read,
            'created_at': n.created_at.strftime('%d %b %Y, %H:%M'),
            'time_ago': timesince(n.created_at) + ' ago',
        }
        for n in notifications
    ]
    return JsonResponse({
        'success': True,
        'unread_count': unread_count,
        'notifications': data,
    })


@login_required
@require_POST
def api_notification_mark_read(request, pk):
    """Marks a single notification as read."""
    NotificationService.mark_as_read(pk, request.user)
    unread_count = NotificationService.get_unread_count(request.user)
    return JsonResponse({'success': True, 'unread_count': unread_count})


@login_required
@require_POST
def api_notification_mark_all_read(request):
    """Marks all notifications for current user as read."""
    NotificationService.mark_all_as_read(request.user)
    return JsonResponse({'success': True, 'unread_count': 0})


@login_required
def api_notifications_unread_count(request):
    """Returns unread notification count for current user."""
    count = NotificationService.get_unread_count(request.user)
    return JsonResponse({'unread_count': count})


# ============================================================
#  GLOBAL SEARCH API
# ============================================================

@login_required
def api_global_search(request):
    """
    Global search API: searches tasks and projects across all user and team data.
    Returns structured results grouped by type with location metadata and direct open URLs.
    """
    q = request.GET.get('q', '').strip()
    if not q or len(q) < 2:
        return JsonResponse({'results': [], 'query': q})

    from django.db.models import Q
    from django.contrib.auth.models import User as AuthUser

    profile = getattr(request.user, 'profile', None)
    if profile and not profile.is_subuser:
        sub_ids = list(AuthUser.objects.filter(profile__parent_user=request.user).values_list('id', flat=True))
        all_user_ids = [request.user.id] + sub_ids
        task_scope = (
            Q(user_id__in=all_user_ids) |
            Q(category__user=request.user) |
            Q(category__members__in=all_user_ids) |
            Q(assignees__in=all_user_ids)
        )
        proj_scope = (
            Q(user=request.user) |
            Q(members__in=all_user_ids)
        )
    else:
        task_scope = (
            Q(user=request.user) |
            Q(category__members=request.user) |
            Q(assignees=request.user)
        )
        proj_scope = (
            Q(user=request.user) |
            Q(members=request.user)
        )

    results = []

    # Search tasks (title, description)
    task_qs = Task.objects.filter(task_scope).filter(
        Q(title__icontains=q) | Q(description__icontains=q)
    ).select_related('category').distinct()[:15]

    for task in task_qs:
        results.append({
            'type': 'task',
            'id': task.id,
            'title': task.title,
            'subtitle': task.category.name if task.category else 'General Tasks',
            'status': task.status,
            'status_display': task.get_status_display(),
            'priority': task.priority,
            'priority_display': task.get_priority_display(),
            'url': f'/kanban/?project={task.category_id}&task={task.id}' if task.category_id else f'/kanban/?task={task.id}',
            'icon': 'fa-tasks',
            'color': task.category.color if task.category else '#3b82f6',
        })

    # Search projects
    proj_qs = Category.objects.filter(proj_scope).filter(
        Q(name__icontains=q) | Q(description__icontains=q)
    ).annotate(task_count=Count('tasks', distinct=True)).distinct()[:8]

    for proj in proj_qs:
        t_count = getattr(proj, 'task_count', 0)
        results.append({
            'type': 'project',
            'id': proj.id,
            'title': proj.name,
            'subtitle': f'{t_count} task{"s" if t_count != 1 else ""}',
            'status': '',
            'url': f'/kanban/?project={proj.id}',
            'icon': 'fa-folder',
            'color': proj.color or '#3b82f6',
        })

    return JsonResponse({'results': results, 'query': q, 'total': len(results)})



@login_required
def manage_subusers(request):
    """Displays the Team & Sub-Users management hub."""
    profile = get_or_create_profile(request.user)
    if profile.is_subuser:
        messages.warning(request, "Only account owners can access the Team Management hub.")
        return redirect('dashboard')

    subusers = SubUserService.get_subusers_data(request.user)
    projects = CategoryService.get_categories(request.user)
    subuser_count = len(subusers)

    context = {
        'active_page': 'manage_subusers',
        'subusers': subusers,
        'projects': projects,
        'categories': projects,
        'subuser_count': subuser_count,
        'max_subusers': SubUserService.MAX_SUBUSERS,
        'remaining_subusers': max(0, SubUserService.MAX_SUBUSERS - subuser_count),
    }
    return render(request, 'todo/manage_team.html', context)


@login_required
def api_subusers_list(request):
    """Returns subusers list and quota as JSON."""
    profile = get_or_create_profile(request.user)
    if profile.is_subuser:
        return JsonResponse({'success': False, 'message': 'Only account owners can view subusers.'}, status=403)

    data = SubUserService.get_subusers_data(request.user)
    return JsonResponse({
        'success': True,
        'subusers': data,
        'count': len(data),
        'max_subusers': SubUserService.MAX_SUBUSERS,
    })


@login_required
@require_POST
def api_subuser_create(request):
    """Creates a new subuser (max 99 limit, editable username & password, no unique email needed)."""
    profile = get_or_create_profile(request.user)
    if profile.is_subuser:
        return JsonResponse({'success': False, 'error': 'Sub-users cannot create other sub-users.'}, status=403)

    try:
        data = json.loads(request.body) if request.content_type and 'application/json' in request.content_type else request.POST
        username = data.get('username', '').strip()
        password = data.get('password', '').strip()
        display_name = data.get('display_name', '').strip() or data.get('first_name', '').strip() or data.get('name', '').strip()
        role = data.get('role', 'member')
        assigned_project_ids = data.get('assigned_projects') or data.get('assigned_project_ids') or []
        if isinstance(assigned_project_ids, str):
            assigned_project_ids = [int(pid) for pid in assigned_project_ids.split(',') if pid.strip().isdigit()]

        subuser = SubUserService.create_subuser(
            owner=request.user,
            username=username,
            password=password,
            display_name=display_name,
            role=role,
            assigned_project_ids=assigned_project_ids,
        )

        return JsonResponse({
            'success': True,
            'message': f'Sub-user "{subuser.username}" created successfully!',
            'subuser': {
                'id': subuser.id,
                'username': subuser.username,
                'name': subuser.get_full_name() or subuser.username,
                'role': subuser.profile.role,
                'role_display': subuser.profile.get_role_display(),
            }
        })
    except ValueError as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=400)
    except Exception as e:
        logger.error('Subuser create failed: %s', e)
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@login_required
@require_POST
def api_subuser_update(request, pk):
    """Updates an existing sub-user's username, password, display name, role, or project assignments."""
    profile = get_or_create_profile(request.user)
    if profile.is_subuser:
        return JsonResponse({'success': False, 'error': 'Sub-users cannot edit other sub-users.'}, status=403)

    try:
        data = json.loads(request.body) if request.content_type and 'application/json' in request.content_type else request.POST
        username = data.get('username')
        password = data.get('password')
        display_name = data.get('display_name') or data.get('first_name') or data.get('name')
        role = data.get('role')
        is_active = data.get('is_active')
        assigned_project_ids = data.get('assigned_projects') or data.get('assigned_project_ids')
        if isinstance(assigned_project_ids, str):
            assigned_project_ids = [int(pid) for pid in assigned_project_ids.split(',') if pid.strip().isdigit()]

        subuser = SubUserService.update_subuser(
            owner=request.user,
            subuser_id=pk,
            username=username,
            password=password if password else None,
            display_name=display_name,
            role=role,
            is_active=is_active,
            assigned_project_ids=assigned_project_ids,
        )

        return JsonResponse({
            'success': True,
            'message': f'Sub-user "{subuser.username}" updated successfully!',
            'subuser': {
                'id': subuser.id,
                'username': subuser.username,
                'name': subuser.get_full_name() or subuser.username,
                'role': subuser.profile.role,
                'role_display': subuser.profile.get_role_display(),
                'is_active': subuser.is_active,
            }
        })
    except ValueError as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=400)
    except Exception as e:
        logger.error('Subuser update failed: %s', e)
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@login_required
@require_POST
def api_subuser_delete(request, pk):
    """Deletes a sub-user account under current owner."""
    profile = get_or_create_profile(request.user)
    if profile.is_subuser:
        return JsonResponse({'success': False, 'error': 'Sub-users cannot delete other sub-users.'}, status=403)

    try:
        SubUserService.delete_subuser(owner=request.user, subuser_id=pk)
        return JsonResponse({'success': True, 'message': 'Sub-user deleted successfully.'})
    except ValueError as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=400)
    except Exception as e:
        logger.error('Subuser delete failed: %s', e)
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


# ============================================================
#  DPDP ACT, 2023 & DPDP RULES, 2025 PRIVACY VIEWS
# ============================================================

from .privacy_services import (
    ConsentService, DataPrincipalRightsService, GrievanceService,
    NominationService, RetentionService
)
from .forms import PrivacyGrievanceForm, NominationForm
from .models import PrivacyGrievance, ConsentRecord, DataPrincipalNomination


def privacy_notice_view(request):
    """
    DPDP Section 5 & DPDP Rules 2025 Notice Page.
    Itemizes personal data categories, specific purposes of processing, lawful bases,
    Data Principal rights, 90-day grievance redressal, and official DPO / Grievance Officer details.
    """
    dpdp_cfg = getattr(settings, 'DPDP_CONFIG', {})
    context = {
        'active_page': 'privacy_notice',
        'dpdp_config': dpdp_cfg,
    }
    return render(request, 'todo/privacy_notice.html', context)


@login_required
def privacy_center_view(request):
    """
    DPDP Act 2023 & DPDP Rules 2025 Privacy & Data Rights Center.
    Interactive hub for:
      - Personal data dossier & export (Section 11)
      - Consent preferences & withdrawal (Section 6)
      - Data Principal nomination (Section 14)
      - Privacy grievances with 90-day statutory SLA tracking (Section 13)
      - Account & personal data erasure (Section 12(3))
    """
    dpdp_cfg = getattr(settings, 'DPDP_CONFIG', {})
    consents = ConsentService.get_user_consent_overview(request.user)
    nominee = NominationService.get_nominee(request.user)
    grievances = PrivacyGrievance.objects.filter(user=request.user).order_by('-created_at')

    grievance_form = PrivacyGrievanceForm(initial={
        'full_name': request.user.get_full_name() or request.user.username,
        'email': request.user.email,
    })
    nomination_form = NominationForm(instance=nominee) if nominee else NominationForm()

    # User data statistics for overview
    tasks_count = Task.objects.filter(Q(user=request.user) | Q(assignees=request.user)).distinct().count()
    projects_count = Category.objects.filter(Q(user=request.user) | Q(members=request.user)).distinct().count()
    comments_count = TaskComment.objects.filter(user=request.user).count()
    attachments_count = TaskAttachment.objects.filter(user=request.user).count()

    active_tab = request.GET.get('tab', 'dossier')

    context = {
        'active_page': 'privacy_center',
        'dpdp_config': dpdp_cfg,
        'consents': consents,
        'nominee': nominee,
        'grievances': grievances,
        'grievance_form': grievance_form,
        'nomination_form': nomination_form,
        'tasks_count': tasks_count,
        'projects_count': projects_count,
        'comments_count': comments_count,
        'attachments_count': attachments_count,
        'active_tab': active_tab,
    }
    return render(request, 'todo/privacy_center.html', context)


@login_required
def privacy_dossier_download(request):
    """
    DPDP Section 11 Right to Access:
    Generates and returns the user's complete Personal Data Dossier as a downloadable JSON file.
    """
    dossier = DataPrincipalRightsService.generate_personal_data_dossier(request.user, request=request)
    
    fmt = request.GET.get('format', 'json').lower()
    if fmt == 'view':
        return JsonResponse(dossier, json_dumps_params={'indent': 2})

    response = HttpResponse(
        json.dumps(dossier, indent=2),
        content_type='application/json; charset=utf-8'
    )
    filename = f"taskfarmm_dpdp_personal_data_dossier_{request.user.username}.json"
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response


@login_required
@require_POST
def privacy_consent_toggle(request):
    """
    DPDP Section 6(4) Consent Withdrawal / Grant AJAX endpoint.
    Allows toggling optional consent purposes with immediate backend synchronization.
    """
    try:
        data = json.loads(request.body) if request.content_type and 'application/json' in request.content_type else request.POST
        purpose = data.get('purpose', '').strip()
        new_status = data.get('status', '').strip().lower()

        if not purpose:
            return JsonResponse({'success': False, 'error': 'Purpose is required.'}, status=400)

        if new_status in ('withdrawn', 'false', '0'):
            record = ConsentService.withdraw_consent(request.user, purpose, request=request)
            is_granted = False
            msg = f"Consent for '{record.get_purpose_display()}' has been withdrawn."
        else:
            record = ConsentService.record_consent(
                request.user, purpose,
                status=ConsentRecord.Status.GRANTED,
                request=request,
                channel='privacy_center'
            )
            is_granted = True
            msg = f"Consent for '{record.get_purpose_display()}' has been granted."

        return JsonResponse({
            'success': True,
            'message': msg,
            'purpose': purpose,
            'is_granted': is_granted,
            'status': record.status,
            'updated_at': timezone.now().strftime('%Y-%m-%d %H:%M'),
        })
    except ValueError as e:
        return JsonResponse({'success': False, 'error': str(e)}, status=400)
    except Exception as e:
        logger.error('Consent toggle error: %s', e)
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@login_required
@require_POST
def privacy_nomination_save(request):
    """
    DPDP Section 14 Data Principal Nomination save endpoint.
    """
    try:
        data = json.loads(request.body) if request.content_type and 'application/json' in request.content_type else request.POST
        nominee_name = data.get('nominee_name', '').strip()
        nominee_email = data.get('nominee_email', '').strip()
        nominee_phone = data.get('nominee_phone', '').strip()
        relationship = data.get('relationship', '').strip()
        notes = data.get('notes', '').strip()

        nominee = NominationService.set_nominee(
            user=request.user,
            nominee_name=nominee_name,
            nominee_email=nominee_email,
            relationship=relationship,
            nominee_phone=nominee_phone,
            notes=notes,
            request=request
        )

        is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest' or (request.content_type and 'application/json' in request.content_type)
        if is_ajax:
            return JsonResponse({
                'success': True,
                'message': f'Nominee "{nominee.nominee_name}" registered successfully under DPDP Act Section 14.',
                'nominee': {
                    'name': nominee.nominee_name,
                    'email': nominee.nominee_email,
                    'relationship': nominee.relationship,
                    'phone': nominee.nominee_phone,
                }
            })
        messages.success(request, f'Nominee "{nominee.nominee_name}" registered successfully.')
        return redirect(f"{reverse('privacy_center')}?tab=nomination")
    except ValueError as e:
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({'success': False, 'error': str(e)}, status=400)
        messages.error(request, str(e))
        return redirect(f"{reverse('privacy_center')}?tab=nomination")
    except Exception as e:
        logger.error('Nomination save error: %s', e)
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


@login_required
@require_POST
def privacy_nomination_revoke(request):
    """
    Revokes the current Data Principal nomination.
    """
    NominationService.revoke_nominee(request.user, request=request)
    is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest'
    if is_ajax:
        return JsonResponse({'success': True, 'message': 'Nomination revoked successfully.'})
    messages.info(request, 'Nomination has been revoked.')
    return redirect(f"{reverse('privacy_center')}?tab=nomination")


def privacy_grievance_submit(request):
    """
    DPDP Section 13 Privacy Grievance Submission endpoint.
    Accessible to authenticated users as well as non-authenticated visitors.
    """
    if request.method != 'POST':
        return JsonResponse({'success': False, 'error': 'POST required.'}, status=405)

    try:
        data = json.loads(request.body) if request.content_type and 'application/json' in request.content_type else request.POST
        full_name = data.get('full_name', '').strip()
        email = data.get('email', '').strip()
        category = data.get('category', 'other')
        subject = data.get('subject', '').strip()
        description = data.get('description', '').strip()

        grievance = GrievanceService.create_grievance(
            full_name=full_name,
            email=email,
            category=category,
            subject=subject,
            description=description,
            user=request.user if request.user.is_authenticated else None,
            request=request
        )

        is_ajax = request.headers.get('X-Requested-With') == 'XMLHttpRequest' or (request.content_type and 'application/json' in request.content_type)
        if is_ajax:
            return JsonResponse({
                'success': True,
                'message': f'Grievance registered. Ticket number: {grievance.ticket_number}. Under DPDP Rules 2025, our Grievance Officer will review this within statutory timelines (≤90 days).',
                'ticket_number': grievance.ticket_number,
                'statutory_deadline': grievance.statutory_deadline.strftime('%d %b %Y'),
            })

        messages.success(request, f'Grievance registered! Ticket: {grievance.ticket_number}. We will resolve this within statutory timelines.')
        if request.user.is_authenticated:
            return redirect(f"{reverse('privacy_center')}?tab=grievances")
        return redirect('privacy_notice')
    except ValueError as e:
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({'success': False, 'error': str(e)}, status=400)
        messages.error(request, str(e))
        return redirect(f"{reverse('privacy_center')}?tab=grievances") if request.user.is_authenticated else redirect('privacy_notice')
    except Exception as e:
        logger.error('Grievance submission error: %s', e)
        return JsonResponse({'success': False, 'error': str(e)}, status=500)


def privacy_grievance_track(request):
    """
    Allows tracking a privacy grievance by ticket number and email.
    """
    ticket_number = request.GET.get('ticket') or request.POST.get('ticket', '')
    email = request.GET.get('email') or request.POST.get('email', '')

    if not ticket_number:
        return JsonResponse({'success': False, 'error': 'Ticket number required.'}, status=400)

    grievance = GrievanceService.get_grievance_by_ticket(ticket_number, email=email)
    if not grievance:
        return JsonResponse({'success': False, 'error': 'Grievance ticket not found or email mismatch.'}, status=404)

    return JsonResponse({
        'success': True,
        'grievance': {
            'ticket_number': grievance.ticket_number,
            'category': grievance.get_category_display(),
            'subject': grievance.subject,
            'status': grievance.status,
            'status_display': grievance.get_status_display(),
            'resolution_notes': grievance.resolution_notes or '',
            'statutory_deadline': grievance.statutory_deadline.strftime('%d %b %Y'),
            'created_at': grievance.created_at.strftime('%d %b %Y, %H:%M'),
            'resolved_at': grievance.resolved_at.strftime('%d %b %Y, %H:%M') if grievance.resolved_at else None,
        }
    })


@login_required
@require_POST
def privacy_account_erase(request):
    """
    DPDP Section 12(3) Right to Erasure Execution Endpoint.
    Requires password verification and confirmation phrase 'ERASE MY DATA'.
    Permanently erases user account, attachments, sub-users, projects, tasks, and sessions.
    """
    from django.contrib.auth import logout as auth_logout
    confirm_text = request.POST.get('confirm_erase', '').strip()
    password = request.POST.get('password', '').strip()

    if confirm_text != 'ERASE MY DATA':
        messages.error(request, 'Confirmation text mismatch. You must type "ERASE MY DATA" exactly to confirm permanent erasure.')
        return redirect(f"{reverse('privacy_center')}?tab=erasure")

    if not request.user.check_password(password):
        messages.error(request, 'Incorrect password. Account erasure request aborted for security.')
        return redirect(f"{reverse('privacy_center')}?tab=erasure")

    user_to_erase = request.user
    auth_logout(request)
    DataPrincipalRightsService.execute_account_erasure(user_to_erase, request=request)

    messages.info(request, 'Your TaskFarmm account and all associated personal data have been permanently erased under DPDP Act 2023 Section 12(3).')
    return redirect('login')
