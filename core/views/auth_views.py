"""
core/views/auth_views.py
-------------------------
Login / logout views — 1:1 migration from FastAPI auth_routes.py.

Session key: the string "authenticated" (literal, not a variable)
Session value on success: True (boolean)
Password: bcrypt via passlib.context.CryptContext — NOT Django check_password()
Login failure: render login.html with HTTP 401
Logout: session.clear() → redirect /login/ (302)
"""

import logging

from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods
from passlib.context import CryptContext

from django.conf import settings

logger = logging.getLogger(__name__)

# Session key — literal string, matches FastAPI original
SESSION_KEY = 'authenticated'

# bcrypt context — matches FastAPI original
pwd_context = CryptContext(schemes=['bcrypt'], deprecated='auto')


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def is_authenticated(request) -> bool:
    return request.session.get(SESSION_KEY) is True


@require_http_methods(['GET', 'POST'])
def login_view(request):
    if request.method == 'GET':
        if is_authenticated(request):
            return redirect('/dashboard/')
        return render(request, 'login.html')

    # POST
    username = request.POST.get('username', '')
    password = request.POST.get('password', '')

    expected_username = settings.ADMIN_USERNAME
    expected_hash = settings.ADMIN_PASSWORD_HASH

    if username == expected_username and verify_password(password, expected_hash):
        request.session[SESSION_KEY] = True
        logger.info('Admin login successful for user: %s', username)
        return redirect('/dashboard/')

    logger.warning('Failed login attempt for username: %s', username)
    return render(
        request,
        'login.html',
        {'error': 'Invalid username or password.'},
        status=401,
    )


def logout_view(request):
    request.session.clear()
    return redirect('/login/')
