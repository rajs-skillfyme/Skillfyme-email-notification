"""
edtech/settings.py
------------------
Django settings for the EdTech Email Reminder System (Django migration).
All configuration is loaded from .env via django-environ.
"""

import os
from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent

# Load .env
env = environ.Env()
environ.Env.read_env(BASE_DIR / '.env')

# ---------------------------------------------------------------------------
# Core Django settings
# ---------------------------------------------------------------------------

SECRET_KEY = env('SECRET_KEY', default='change-me-in-production')
DEBUG = env.bool('DEBUG', default=False)
ALLOWED_HOSTS = ['*']

# ---------------------------------------------------------------------------
# Installed apps
# ---------------------------------------------------------------------------

# AFTER
INSTALLED_APPS = [
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.auth',
    'django.contrib.staticfiles',
    'rest_framework',
    'django_apscheduler',   # ← add this line
    'core.apps.CoreConfig',
]

# ---------------------------------------------------------------------------
# Middleware
# ---------------------------------------------------------------------------

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'core.middleware.LoginRequiredMiddleware',
    'django.middleware.common.CommonMiddleware',
]

ROOT_URLCONF = 'edtech.urls'

# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'core' / 'templates'],
        'APP_DIRS': False,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
            ],
        },
    },
]

WSGI_APPLICATION = 'edtech.wsgi.application'

# ---------------------------------------------------------------------------
# Database (SQLite with WAL mode + FK enforcement)
# ---------------------------------------------------------------------------

import dj_database_url

DATABASE_URL = env('DATABASE_URL', default='sqlite:///data/edtech.db')

if DATABASE_URL.startswith('postgresql'):
    DATABASES = {
        'default': dj_database_url.parse(DATABASE_URL, conn_max_age=600)
    }
else:
    _db_path = DATABASE_URL.replace('sqlite:///', '')
    if not os.path.isabs(_db_path):
        _db_path = str(BASE_DIR / _db_path)
    os.makedirs(os.path.dirname(_db_path), exist_ok=True)
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': _db_path,
            'OPTIONS': {'timeout': 20},
        }
    
}
# ---------------------------------------------------------------------------
# Sessions
# ---------------------------------------------------------------------------

SESSION_ENGINE = 'django.contrib.sessions.backends.db'
SESSION_COOKIE_HTTPONLY = True

# ---------------------------------------------------------------------------
# Internationalization
# ---------------------------------------------------------------------------

LANGUAGE_CODE = 'en-us'
# AFTER
TIME_ZONE = 'Asia/Kolkata'
USE_I18N = True
USE_TZ = True

# ---------------------------------------------------------------------------
# Default primary key field type
# ---------------------------------------------------------------------------

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# ---------------------------------------------------------------------------
# DRF — custom exception handler to return 422 for validation errors
# ---------------------------------------------------------------------------

# AFTER
REST_FRAMEWORK = {
    'EXCEPTION_HANDLER': 'core.views.custom_exception_handler',
    'UNAUTHENTICATED_USER': None,  # ← add this
}

# ---------------------------------------------------------------------------
# Application configuration (all from .env)
# ---------------------------------------------------------------------------

ADMIN_USERNAME = env('ADMIN_USERNAME', default='admin')
# AFTER
ADMIN_PASSWORD_HASH = env('ADMIN_PASSWORD_HASH', default='') or '$2b$12$DShXeGCG3ReP5AGxvuAcN.seMknLLshFGpHbx2A18bf5qj1dzruJO'

SMTP_HOST = env('SMTP_HOST', default='smtp.gmail.com')
SMTP_PORT = env.int('SMTP_PORT', default=587)
SMTP_USERNAME = env('SMTP_USERNAME', default='')
SMTP_PASSWORD = env('SMTP_PASSWORD', default='')
SENDER_EMAIL = env('SENDER_EMAIL', default='')
SENDER_NAME = env('SENDER_NAME', default='EdTech Platform')

EMAIL_SEND_DELAY_SECONDS = env.float('EMAIL_SEND_DELAY_SECONDS', default=1.0)
MAX_RETRY_ATTEMPTS = env.int('MAX_RETRY_ATTEMPTS', default=3)
MINUTES_BEFORE_CLASS = env.int('MINUTES_BEFORE_CLASS', default=60)

APP_TIMEZONE = env('APP_TIMEZONE', default='Asia/Kolkata')
LOG_LEVEL = env('LOG_LEVEL', default='INFO')

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

_logs_dir = BASE_DIR / 'logs'
_logs_dir.mkdir(exist_ok=True)

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'standard': {
            'format': '%(asctime)s | %(levelname)-8s | %(name)s | %(message)s',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'standard',
        },
        'file': {
            'class': 'logging.FileHandler',
            'filename': str(BASE_DIR / 'logs' / 'app.log'),
            'encoding': 'utf-8',
            'formatter': 'standard',
        },
    },
    'root': {
        'handlers': ['console', 'file'],
        'level': LOG_LEVEL,
    },
}
SESSION_COOKIE_AGE = int(env('SESSION_COOKIE_AGE', default='86400'))
SESSION_SAVE_EVERY_REQUEST = True
