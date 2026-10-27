import os
from pathlib import Path
from urllib.parse import urlsplit, unquote, parse_qs

BASE_DIR = Path(__file__).resolve().parent.parent
LOCAL_DIR = BASE_DIR / '.local'
LOCAL_DIR.mkdir(exist_ok=True)
DEBUG = os.getenv('DJANGO_DEBUG', '1') == '1'
SECRET_KEY = os.getenv('DJANGO_SECRET_KEY', 'local-demo-only-fsp-2026-do-not-use-in-production')
if not DEBUG and SECRET_KEY.startswith('local-demo-only'):
    raise RuntimeError('Set DJANGO_SECRET_KEY before production startup.')
ALLOWED_HOSTS = os.getenv('DJANGO_ALLOWED_HOSTS', 'localhost,127.0.0.1,[::1],testserver').split(',')
CSRF_TRUSTED_ORIGINS = [s for s in os.getenv('DJANGO_CSRF_TRUSTED_ORIGINS', '').split(',') if s]
INSTALLED_APPS = [
    'django.contrib.admin', 'django.contrib.auth', 'django.contrib.contenttypes',
    'django.contrib.sessions', 'django.contrib.messages', 'django.contrib.staticfiles', 'federation',
    'oauth2_provider', 'identity', 'rest_framework', 'rest_framework_simplejwt.token_blacklist',
    'integrations', 'training', 'governance', 'development', 'reporting',
]
MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware', 'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware', 'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware', 'django.contrib.messages.middleware.MessageMiddleware',
    'federation.middleware.MinistryReadOnlyMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]
ROOT_URLCONF = 'config.urls'
TEMPLATES = [{'BACKEND': 'django.template.backends.django.DjangoTemplates', 'DIRS': [], 'APP_DIRS': True,
              'OPTIONS': {'context_processors': [
                  'django.template.context_processors.request', 'django.contrib.auth.context_processors.auth',
                  'django.contrib.messages.context_processors.messages', 'federation.context.navigation',
              ]}}]
WSGI_APPLICATION = 'config.wsgi.application'
DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': os.getenv('DATABASE_PATH', str(LOCAL_DIR / 'db.sqlite3')),
                         'OPTIONS': {'timeout': 20}}}
if os.getenv('DATABASE_URL'):
    database = urlsplit(os.environ['DATABASE_URL'])
    if database.scheme not in ('postgres', 'postgresql'):
        raise RuntimeError('DATABASE_URL must be a PostgreSQL URL.')
    DATABASES = {'default': {'ENGINE': 'django.db.backends.postgresql', 'NAME': unquote(database.path.lstrip('/')),
        'USER': unquote(database.username or ''), 'PASSWORD': unquote(database.password or ''),
        'HOST': database.hostname or 'localhost', 'PORT': database.port or 5432, 'CONN_MAX_AGE': 60,
        'OPTIONS': {k: v[-1] for k, v in parse_qs(database.query).items() if k in ('sslmode', 'connect_timeout')},
    }}
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]
LANGUAGE_CODE = 'ru-ru'
TIME_ZONE = 'Asia/Yekaterinburg'
USE_I18N = True
USE_TZ = True
STATIC_URL = '/static/'
STATIC_ROOT = LOCAL_DIR / 'static'
MEDIA_ROOT = LOCAL_DIR / 'uploads'
# Uploads are deliberately served only by the authenticated regulation_download view.
MEDIA_URL = '/private-media/'
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
LOGIN_URL = 'login'
LOGIN_REDIRECT_URL = 'dashboard'
LOGOUT_REDIRECT_URL = 'login'
EMAIL_BACKEND = os.getenv('EMAIL_BACKEND', 'django.core.mail.backends.filebased.EmailBackend')
EMAIL_FILE_PATH = LOCAL_DIR / 'mail'
EMAIL_HOST = os.getenv('EMAIL_HOST', '')
EMAIL_PORT = int(os.getenv('EMAIL_PORT', '587'))
EMAIL_HOST_USER = os.getenv('EMAIL_HOST_USER', '')
EMAIL_HOST_PASSWORD = os.getenv('EMAIL_HOST_PASSWORD', '')
EMAIL_USE_TLS = os.getenv('EMAIL_USE_TLS', '1') == '1'
EMAIL_TIMEOUT = 5
DEFAULT_FROM_EMAIL = os.getenv('DEFAULT_FROM_EMAIL', 'ОренКод <noreply@orenkod.example>')
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = 'DENY'
FILE_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024
DATA_UPLOAD_MAX_MEMORY_SIZE = 6 * 1024 * 1024
STORAGES = {'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
            'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage' if DEBUG else 'whitenoise.storage.CompressedManifestStaticFilesStorage'}}
PUBLIC_BASE_URL = os.getenv('PUBLIC_BASE_URL', '').rstrip('/')
INTEGRATION_WEBHOOK_ALLOWED_HOSTS = [x.strip() for x in os.getenv('INTEGRATION_WEBHOOK_ALLOWED_HOSTS', '').split(',') if x.strip()]
SECURE_SSL_REDIRECT = os.getenv('DJANGO_SECURE_SSL_REDIRECT', '0' if DEBUG else '1') == '1'
SECURE_REDIRECT_EXEMPT = [r'^health/$']
SECURE_HSTS_SECONDS = 0 if DEBUG else 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = not DEBUG
SECURE_HSTS_PRELOAD = not DEBUG
# Set this only when a trusted reverse proxy strips client-supplied forwarded headers.
if os.getenv('TRUST_PROXY_HEADERS', '0') == '1':
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
from identity.config import oauth_settings
from integrations.settings import REST_FRAMEWORK, SIMPLE_JWT
OAUTH2_PROVIDER = oauth_settings(LOCAL_DIR, DEBUG)
