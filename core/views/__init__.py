"""
core/views/__init__.py
-----------------------
Custom DRF exception handler: maps ValidationError → HTTP 422
(matching FastAPI's default behaviour for Pydantic validation errors).
"""

from rest_framework.views import exception_handler
from rest_framework.exceptions import ValidationError


def custom_exception_handler(exc, context):
    response = exception_handler(exc, context)
    if isinstance(exc, ValidationError) and response is not None:
        response.status_code = 422
    return response
