import os
from pathlib import Path
from datetime import timedelta

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent

# Load environment variables from .env file if available
try:
    from dotenv import load_dotenv
    load_dotenv(BASE_DIR / '.env')
    load_dotenv(BASE_DIR / '.github' / '.env')
except ImportError:
    pass

# MySQL / MariaDB support via PyMySQL
try:
    import pymysql
    pymysql.install_as_MySQLdb()
except ImportError:
    pass

# ============================================================
#  SECURITY
# ============================================================

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = os.environ.get(
    'SECRET_KEY',
    'django-insecure-wzjz3xhp8s(so$mav5us0skf(0q*$ze85(hpa@&9&@!8x3#hm@'
)

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = os.environ.get('DEBUG', 'True').strip().lower() in ['true', '1', 'yes', 't']

allowed_hosts_env = os.environ.get('ALLOWED_HOSTS', '').strip()
if allowed_hosts_env:
    ALLOWED_HOSTS = [h.strip() for h in allowed_hosts_env.split(',') if h.strip()]
else:
    ALLOWED_HOSTS = ['*'] if DEBUG else ['127.0.0.1', 'localhost']

# CSRF Trusted Origins (comma-separated origins e.g. https://yourdomain.com)
csrf_origins_env = os.environ.get('CSRF_TRUSTED_ORIGINS', '').strip()
if csrf_origins_env:
    CSRF_TRUSTED_ORIGINS = [o.strip() for o in csrf_origins_env.split(',') if o.strip()]

# Production security headers
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = 'strict-origin-when-cross-origin'
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_AGE = 1209600  # 2 weeks
SESSION_EXPIRE_AT_BROWSER_CLOSE = False

if not DEBUG:
    SECURE_BROWSER_XSS_FILTER = True
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_SSL_REDIRECT = os.environ.get('SECURE_SSL_REDIRECT', 'True').lower() == 'true'
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    X_FRAME_OPTIONS = 'DENY'
else:
    X_FRAME_OPTIONS = 'SAMEORIGIN'

# ============================================================
#  INSTALLED APPS
# ============================================================

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    # Third-party
    'rest_framework',
    'rest_framework_simplejwt',
    'django_filters',
    # Local
    'todo',
]

# ============================================================
#  MIDDLEWARE
# ============================================================

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'todo.middleware.DemoAuthMiddleware',
]

ROOT_URLCONF = 'config.urls'

LOGIN_URL = 'login'
LOGIN_REDIRECT_URL = 'dashboard'
ENABLE_DEMO_AUTH = os.environ.get('ENABLE_DEMO_AUTH', 'False').strip().lower() in ['true', '1', 'yes', 't']

# ============================================================
#  TEMPLATES
# ============================================================

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'todo.context_processors.add_task_form_to_context',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'

# ============================================================
#  DATABASE
# ============================================================

DATABASE_URL = os.environ.get('DATABASE_URL')
DB_ENGINE = os.environ.get('DB_ENGINE', '').lower()

if DATABASE_URL:
    try:
        import dj_database_url
        DATABASES = {
            'default': dj_database_url.config(default=DATABASE_URL, conn_max_age=600)
        }
    except ImportError:
        DATABASES = {
            'default': {
                'ENGINE': 'django.db.backends.sqlite3',
                'NAME': BASE_DIR / 'db.sqlite3',
            }
        }
elif DB_ENGINE == 'mysql':
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.mysql',
            'NAME': os.environ.get('DB_NAME', 'taskfarmm_db'),
            'USER': os.environ.get('DB_USER', 'root'),
            'PASSWORD': os.environ.get('DB_PASSWORD', ''),
            'HOST': os.environ.get('DB_HOST', 'localhost'),
            'PORT': os.environ.get('DB_PORT', '3306'),
            'CONN_MAX_AGE': 600,
            'OPTIONS': {
                'charset': 'utf8mb4',
                'init_command': "SET sql_mode='STRICT_TRANS_TABLES'",
            },
        }
    }
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db.sqlite3',
            'CONN_MAX_AGE': 300,
        }
    }

# ============================================================
#  CACHING
# ============================================================

CACHES = {
    'default': {
        'BACKEND': os.environ.get(
            'CACHE_BACKEND',
            'django.core.cache.backends.filebased.FileBasedCache'
        ),
        'LOCATION': os.environ.get(
            'CACHE_LOCATION',
            str(BASE_DIR / '.cache')
        ),
        'TIMEOUT': 300,
        'OPTIONS': {
            'MAX_ENTRIES': 1000,
        },
    }
}

# ============================================================
#  DJANGO REST FRAMEWORK
# ============================================================

REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework_simplejwt.authentication.JWTAuthentication',
        'rest_framework.authentication.SessionAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
    'DEFAULT_FILTER_BACKENDS': [
        'django_filters.rest_framework.DjangoFilterBackend',
        'rest_framework.filters.SearchFilter',
        'rest_framework.filters.OrderingFilter',
    ],
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.PageNumberPagination',
    'PAGE_SIZE': 20,
    'DEFAULT_THROTTLE_CLASSES': [
        'rest_framework.throttling.AnonRateThrottle',
        'rest_framework.throttling.UserRateThrottle',
    ],
    'DEFAULT_THROTTLE_RATES': {
        'anon': '100/day',
        'user': '1000/day',
    },
    'DEFAULT_RENDERER_CLASSES': [
        'rest_framework.renderers.JSONRenderer',
        'rest_framework.renderers.BrowsableAPIRenderer',
    ],
}

# ============================================================
#  JWT CONFIGURATION
# ============================================================

SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(hours=1),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=7),
    'ROTATE_REFRESH_TOKENS': True,
    'BLACKLIST_AFTER_ROTATION': False,
    'AUTH_HEADER_TYPES': ('Bearer',),
    'AUTH_HEADER_NAME': 'HTTP_AUTHORIZATION',
}

# ============================================================
#  PASSWORD VALIDATION
# ============================================================

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

# ============================================================
#  INTERNATIONALISATION
# ============================================================

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

# ============================================================
#  STATIC & MEDIA FILES
# ============================================================

STATIC_URL = '/static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'
STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedStaticFilesStorage",
    },
}

MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# ============================================================
#  EMAIL
# ============================================================

EMAIL_BACKEND = os.environ.get(
    'EMAIL_BACKEND',
    'django.core.mail.backends.console.EmailBackend' if DEBUG else 'django.core.mail.backends.smtp.EmailBackend'
)
EMAIL_HOST = os.environ.get('EMAIL_HOST', 'mail.roshandamor.me')
EMAIL_PORT = int(os.environ.get('EMAIL_PORT', 465))
EMAIL_USE_SSL = os.environ.get('EMAIL_USE_SSL', 'True').lower() in ['true', '1']
EMAIL_USE_TLS = os.environ.get('EMAIL_USE_TLS', 'False').lower() in ['true', '1']
EMAIL_HOST_USER = os.environ.get('EMAIL_HOST_USER', 'noreply@roshandamor.me')
EMAIL_HOST_PASSWORD = os.environ.get('EMAIL_HOST_PASSWORD', '')
DEFAULT_FROM_EMAIL = os.environ.get('DEFAULT_FROM_EMAIL', 'TaskFarmm <noreply@roshandamor.me>')

# ============================================================
#  LOGGING
# ============================================================

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '{levelname} {asctime} {module} {process:d} {thread:d} {message}',
            'style': '{',
        },
        'simple': {
            'format': '{levelname} {asctime} {module} — {message}',
            'style': '{',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'simple',
        },
    },
    'loggers': {
        'django': {
            'handlers': ['console'],
            'level': os.environ.get('DJANGO_LOG_LEVEL', 'WARNING'),
            'propagate': False,
        },
        'todo': {
            'handlers': ['console'],
            'level': 'DEBUG' if DEBUG else 'INFO',
            'propagate': False,
        },
    },
}

# ============================================================
#  AUTHENTICATION URL CONFIGURATION
# ============================================================

LOGIN_URL = 'login'
LOGIN_REDIRECT_URL = 'dashboard'
LOGOUT_REDIRECT_URL = 'login'

# ============================================================
#  GOOGLE SSO / OAUTH2 CONFIGURATION
# ============================================================

GOOGLE_CLIENT_ID = os.environ.get('GOOGLE_CLIENT_ID', '').strip()
GOOGLE_CLIENT_SECRET = os.environ.get('GOOGLE_CLIENT_SECRET', '').strip()

# ============================================================
#  DPDP ACT, 2023 & DPDP RULES, 2025 CONFIGURATION LAYER
# ============================================================

DPDP_CONFIG = {
    'DATA_FIDUCIARY_NAME': os.environ.get('DPDP_DATA_FIDUCIARY_NAME', 'TaskFarmm Technologies'),
    'DATA_FIDUCIARY_EMAIL': os.environ.get('DPDP_DATA_FIDUCIARY_EMAIL', 'contact@taskfarmm.com'),
    'DATA_FIDUCIARY_ADDRESS': os.environ.get('DPDP_DATA_FIDUCIARY_ADDRESS', 'TaskFarmm Tech Park, Sector 62, Noida, Uttar Pradesh 201309, India'),
    'NOTICE_VERSION': os.environ.get('DPDP_NOTICE_VERSION', '1.0-2025-DPDP'),
    'NOTICE_EFFECTIVE_DATE': os.environ.get('DPDP_NOTICE_EFFECTIVE_DATE', '2025-01-01'),
    'GRIEVANCE_OFFICER_NAME': os.environ.get('DPDP_GRIEVANCE_OFFICER_NAME', 'Roshan Damor (Data Protection & Grievance Officer)'),
    'GRIEVANCE_OFFICER_EMAIL': os.environ.get('DPDP_GRIEVANCE_OFFICER_EMAIL', 'privacy@taskfarmm.com'),
    'GRIEVANCE_OFFICER_PHONE': os.environ.get('DPDP_GRIEVANCE_OFFICER_PHONE', '+91-11-4000-8000'),
    'GRIEVANCE_OFFICER_ADDRESS': os.environ.get('DPDP_GRIEVANCE_OFFICER_ADDRESS', 'TaskFarmm Grievance Redressal Cell, Sector 62, Noida, UP 201309, India'),
    'STATUTORY_GRIEVANCE_DAYS': int(os.environ.get('DPDP_STATUTORY_GRIEVANCE_DAYS', 90)),
    'RETENTION_NOTIFICATIONS_DAYS': int(os.environ.get('DPDP_RETENTION_NOTIFICATIONS_DAYS', 90)),
    'RETENTION_EXPIRED_SESSIONS_DAYS': int(os.environ.get('DPDP_RETENTION_EXPIRED_SESSIONS_DAYS', 30)),
    'RETENTION_AUDIT_LOGS_DAYS': int(os.environ.get('DPDP_RETENTION_AUDIT_LOGS_DAYS', 365)),
    'CHILD_DATA_COLLECTION_ENABLED': False,
    'BOARD_ESCALATION_URL': 'https://www.meity.gov.in/dpdp',
}