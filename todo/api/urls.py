"""
todo/api/urls.py

URL routing for the TaskFarmm REST API v1.

All routes are under the /api/v1/ prefix (set in config/urls.py).

Authentication:
  POST /api/v1/auth/token/         — obtain JWT access + refresh tokens
  POST /api/v1/auth/token/refresh/ — refresh an access token

Resources:
  /api/v1/tasks/           — Task CRUD + export, toggle, comment, checklist actions
  /api/v1/projects/        — Project (Category) CRUD
  /api/v1/stats/           — Aggregated user stats
  /api/v1/templates/       — Predefined task template library
  /api/v1/profile/         — User profile settings
  /api/v1/comments/{id}/   — Delete a specific comment
"""

from django.urls import path, include
from rest_framework.routers import DefaultRouter
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from todo.api_auth import api_health_check
from .views import (
    TaskViewSet,
    CategoryViewSet,
    StatsAPIView,
    PreDefinedTaskViewSet,
    UserProfileAPIView,
    TaskCommentDeleteView,
    DPDPNoticeAPIView,
    DPDPDossierAPIView,
    DPDPConsentsAPIView,
    DPDPNominationAPIView,
    DPDPGrievanceAPIView,
    DPDPErasureAPIView,
)

# DRF router — auto-generates standard CRUD routes for ViewSets
router = DefaultRouter()
router.register(r'tasks', TaskViewSet, basename='api-task')
router.register(r'projects', CategoryViewSet, basename='api-project')
router.register(r'templates', PreDefinedTaskViewSet, basename='api-template')

urlpatterns = [
    # Health check
    path('health/', api_health_check, name='api-v1-health'),

    # JWT authentication
    path('auth/token/', TokenObtainPairView.as_view(), name='api-token-obtain'),
    path('auth/token/refresh/', TokenRefreshView.as_view(), name='api-token-refresh'),

    # Aggregated stats
    path('stats/', StatsAPIView.as_view(), name='api-stats'),

    # User profile
    path('profile/', UserProfileAPIView.as_view(), name='api-profile'),

    # Comment management
    path('comments/<int:pk>/', TaskCommentDeleteView.as_view(), name='api-comment-delete'),

    # DPDP Act 2023 & DPDP Rules 2025 Privacy Endpoints
    path('privacy/notice/', DPDPNoticeAPIView.as_view(), name='api-privacy-notice'),
    path('privacy/dossier/', DPDPDossierAPIView.as_view(), name='api-privacy-dossier'),
    path('privacy/consents/', DPDPConsentsAPIView.as_view(), name='api-privacy-consents'),
    path('privacy/nomination/', DPDPNominationAPIView.as_view(), name='api-privacy-nomination'),
    path('privacy/grievances/', DPDPGrievanceAPIView.as_view(), name='api-privacy-grievances'),
    path('privacy/erase/', DPDPErasureAPIView.as_view(), name='api-privacy-erase'),

    # Router-generated routes (tasks, projects, templates)
    path('', include(router.urls)),
]

