from django.conf import settings
from .forms import TaskForm
from .models import Notification


def add_task_form_to_context(request):
    """
    Makes the TaskForm, unread notifications, and user categories (projects)
    available in the context of every template. This ensures modals in base.html
    that loop over `categories` always work regardless of which view is active.
    """
    if request.user.is_authenticated:
        from django.db.models import Q
        from .models import Category
        form = TaskForm(user=request.user)
        unread_count = Notification.objects.filter(user=request.user, is_read=False).count()
        latest_notifications = Notification.objects.filter(user=request.user).order_by('-created_at')[:10]
        # Provide both owned and shared categories globally for all modals
        categories = list(
            Category.objects.filter(
                Q(user=request.user) | Q(members=request.user)
            ).distinct().order_by('name')
        )
        return {
            'task_create_form': form,
            'unread_notifications_count': unread_count,
            'latest_notifications': latest_notifications,
            'categories': categories,
            'dpdp_config': getattr(settings, 'DPDP_CONFIG', {}),
        }
    return {'dpdp_config': getattr(settings, 'DPDP_CONFIG', {})}