"""
core/middleware.py
------------------
LoginRequiredMiddleware — mirrors the FastAPI BaseHTTPMiddleware exactly.

OPEN_PATHS includes both /login and /login/ variants (with and without
trailing slash) to handle any redirect differences.

Must be added AFTER SessionMiddleware in MIDDLEWARE list.
"""

from django.shortcuts import redirect

OPEN_PATHS = {
    '/login', '/login/',
    '/logout', '/logout/',
    '/health', '/health/',
    '/docs', '/openapi.json', '/redoc',
}


class LoginRequiredMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path not in OPEN_PATHS:
            if not request.session.get('authenticated'):
                return redirect('/login/')
        return self.get_response(request)
