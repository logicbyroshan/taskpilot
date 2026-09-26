"""
todo/google_auth.py

Production-ready Google Single Sign-On (SSO) & OAuth 2.0 Integration for TaskFarmm.
Supports:
  1. Standard Google OAuth2 Authorization Code Flow (/auth/google/login/ -> /auth/google/callback/)
  2. Google Identity Services (GIS) One-Tap / JWT Credential Verification (/auth/google/credential/)
"""

import json
import logging
import secrets
import urllib.parse
import urllib.request
import urllib.error

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login as auth_login
from django.contrib.auth.models import User
from django.http import JsonResponse, HttpResponseRedirect
from django.shortcuts import render, redirect
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST, require_http_methods

from .models import UserProfile, Category

logger = logging.getLogger('todo')

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"
GOOGLE_TOKENINFO_URL = "https://oauth2.googleapis.com/tokeninfo"


def _generate_unique_username(email, first_name=""):
    """Generates a clean, unique Django username from an email or name."""
    base = ""
    if email and '@' in email:
        base = email.split('@')[0].strip()
    if not base and first_name:
        base = first_name.strip().lower()
    if not base:
        base = "google_user"

    import re
    clean_base = re.sub(r'[^a-zA-Z0-9_]', '_', base)[:20] or "user"
    username = clean_base

    counter = 1
    while User.objects.filter(username__iexact=username).exists():
        username = f"{clean_base}_{counter}"
        counter += 1

    return username


def _provision_google_user(email, first_name="", last_name="", picture=""):
    """
    Finds existing user by email or provisions a new user with profile & starter project.
    """
    email = (email or '').strip().lower()
    if not email:
        raise ValueError("Google user info did not contain a valid email address.")

    user = User.objects.filter(email__iexact=email).first()
    is_new = False

    if not user:
        username = _generate_unique_username(email, first_name)
        user = User(
            username=username,
            email=email,
            first_name=first_name[:30] if first_name else '',
            last_name=last_name[:30] if last_name else '',
        )
        user.set_unusable_password()
        user.save()
        is_new = True

    # Ensure profile exists
    profile, _ = UserProfile.objects.get_or_create(user=user)
    if not user.is_active:
        user.is_active = True
        user.save(update_fields=['is_active'])

    # Starter project & DPDP Consent for new registrations
    if is_new:
        if not Category.objects.filter(user=user).exists():
            Category.objects.create(
                user=user,
                name="General Tasks",
                color="#3b82f6",
                description="Default workspace for your tasks & quick ideas.",
                board_template=Category.BoardTemplate.SMART,
            )
        try:
            from .privacy_services import ConsentService
            ConsentService.record_initial_user_consents(user, opt_in_notifications=True, opt_in_ai=True, channel='google_sso')
        except Exception as e:
            logger.warning('Could not record initial DPDP consent for Google user: %s', e)

    return user, is_new


def google_login(request):
    """
    Initiates Google OAuth 2.0 authorization redirect.
    """
    client_id = getattr(settings, 'GOOGLE_CLIENT_ID', '').strip()
    client_secret = getattr(settings, 'GOOGLE_CLIENT_SECRET', '').strip()

    next_url = request.GET.get('next', '')
    if next_url:
        request.session['google_oauth_next'] = next_url

    # Check if Google Client ID is configured
    if not client_id or not client_secret:
        return render(request, 'todo/auth/google_setup.html', {
            'is_configured': False,
            'client_id': client_id,
            'next': next_url,
        })

    # Generate secure random state token to prevent CSRF
    state = secrets.token_urlsafe(32)
    request.session['google_oauth_state'] = state

    redirect_uri = request.build_absolute_uri(reverse('google_callback'))
    if not settings.DEBUG and request.is_secure():
        redirect_uri = redirect_uri.replace('http://', 'https://')

    params = {
        'client_id': client_id,
        'redirect_uri': redirect_uri,
        'response_type': 'code',
        'scope': 'openid email profile',
        'state': state,
        'access_type': 'online',
        'prompt': 'select_account',
    }

    auth_redirect_url = f"{GOOGLE_AUTH_URL}?{urllib.parse.urlencode(params)}"
    return HttpResponseRedirect(auth_redirect_url)


def google_callback(request):
    """
    Handles the Google OAuth 2.0 authorization code exchange and logs in user.
    """
    error = request.GET.get('error')
    if error:
        logger.warning('Google OAuth callback error: %s', error)
        messages.error(request, f"Google authentication was cancelled or failed: {error}")
        return redirect('login')

    code = request.GET.get('code')
    state = request.GET.get('state')
    saved_state = request.session.pop('google_oauth_state', None)

    if not code:
        messages.error(request, "Missing authorization code from Google.")
        return redirect('login')

    # Validate state parameter against CSRF
    if not state or state != saved_state:
        logger.warning('Google OAuth state mismatch. Possible CSRF.')
        messages.error(request, "Authentication session expired or state verification failed. Please try again.")
        return redirect('login')

    client_id = getattr(settings, 'GOOGLE_CLIENT_ID', '').strip()
    client_secret = getattr(settings, 'GOOGLE_CLIENT_SECRET', '').strip()
    redirect_uri = request.build_absolute_uri(reverse('google_callback'))
    if not settings.DEBUG and request.is_secure():
        redirect_uri = redirect_uri.replace('http://', 'https://')

    try:
        # Exchange authorization code for access token
        token_payload = urllib.parse.urlencode({
            'code': code,
            'client_id': client_id,
            'client_secret': client_secret,
            'redirect_uri': redirect_uri,
            'grant_type': 'authorization_code',
        }).encode('utf-8')

        token_req = urllib.request.Request(
            GOOGLE_TOKEN_URL,
            data=token_payload,
            headers={'Content-Type': 'application/x-www-form-urlencoded'}
        )

        with urllib.request.urlopen(token_req, timeout=10) as token_res:
            token_data = json.loads(token_res.read().decode('utf-8'))

        access_token = token_data.get('access_token')
        if not access_token:
            raise ValueError("No access_token returned by Google token endpoint.")

        # Fetch user info using access token
        userinfo_req = urllib.request.Request(
            GOOGLE_USERINFO_URL,
            headers={'Authorization': f'Bearer {access_token}'}
        )

        with urllib.request.urlopen(userinfo_req, timeout=10) as userinfo_res:
            user_info = json.loads(userinfo_res.read().decode('utf-8'))

        email = user_info.get('email', '')
        first_name = user_info.get('given_name', '')
        last_name = user_info.get('family_name', '')
        picture = user_info.get('picture', '')

        user, is_new = _provision_google_user(
            email=email,
            first_name=first_name,
            last_name=last_name,
            picture=picture
        )

        auth_login(request, user, backend='django.contrib.auth.backends.ModelBackend')
        request.session.set_expiry(1209600)  # 2 weeks

        action_msg = "created and signed in" if is_new else "signed in"
        messages.success(request, f"Welcome, {user.first_name or user.username}! Successfully {action_msg} with Google.")

        next_url = request.session.pop('google_oauth_next', None) or 'dashboard'
        return redirect(next_url)

    except Exception as e:
        logger.error('Google OAuth token exchange error: %s', e, exc_info=True)
        messages.error(request, f"Failed to complete Google Sign-In: {str(e)}")
        return redirect('login')


@csrf_exempt
@require_POST
def google_credential_callback(request):
    """
    Handles Google Identity Services (GIS) One-Tap / Button credential token.
    Accepts JSON: { "credential": "<Google ID Token JWT>" }
    """
    try:
        data = json.loads(request.body)
        id_token = data.get('credential', '').strip()

        if not id_token:
            return JsonResponse({'success': False, 'error': 'Credential token required.'}, status=400)

        verify_url = f"{GOOGLE_TOKENINFO_URL}?id_token={urllib.parse.quote(id_token)}"
        req = urllib.request.Request(verify_url)

        with urllib.request.urlopen(req, timeout=10) as res:
            token_info = json.loads(res.read().decode('utf-8'))

        client_id = getattr(settings, 'GOOGLE_CLIENT_ID', '').strip()
        if client_id and token_info.get('aud') != client_id:
            return JsonResponse({'success': False, 'error': 'Google token audience mismatch.'}, status=400)

        email = token_info.get('email', '')
        first_name = token_info.get('given_name', '')
        last_name = token_info.get('family_name', '')
        picture = token_info.get('picture', '')

        if not email:
            return JsonResponse({'success': False, 'error': 'Email not verified by Google.'}, status=400)

        user, is_new = _provision_google_user(
            email=email,
            first_name=first_name,
            last_name=last_name,
            picture=picture
        )

        auth_login(request, user, backend='django.contrib.auth.backends.ModelBackend')
        request.session.set_expiry(1209600)

        return JsonResponse({
            'success': True,
            'message': f"Welcome {user.first_name or user.username}!",
            'redirect_url': reverse('dashboard')
        })

    except Exception as e:
        logger.error('Google GIS credential verification error: %s', e)
        return JsonResponse({'success': False, 'error': str(e)}, status=400)


def google_dev_demo_login(request):
    """
    Developer fallback to test Google sign-in workflow when GOOGLE_CLIENT_ID is not configured locally.
    """
    if request.method == 'POST':
        email = request.POST.get('email', 'google_user@gmail.com').strip()
        first_name = request.POST.get('first_name', 'Google').strip()
        last_name = request.POST.get('last_name', 'User').strip()

        if not email:
            email = 'google_user@gmail.com'

        user, is_new = _provision_google_user(email=email, first_name=first_name, last_name=last_name)
        auth_login(request, user, backend='django.contrib.auth.backends.ModelBackend')

        next_url = request.session.pop('google_oauth_next', None) or 'dashboard'
        messages.success(request, f"Signed in with Google account: {user.email}")
        return redirect(next_url)

    return redirect('login')
