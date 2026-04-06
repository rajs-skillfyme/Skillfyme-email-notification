"""
core/views/log_views.py
------------------------
Email log API views — 1:1 migration from FastAPI log_routes.py.

GET /api/batch/<batch_code>/email-logs/ — paginated, filterable
GET /api/dashboard/email-logs/sent-today/  — JSON (for modal JS in dashboard)
GET /api/dashboard/email-logs/failed-today/ — JSON
"""

import logging
from datetime import date, datetime, timedelta, timezone

from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from core.models import Batch, EmailLog
from core.serializers import EmailLogOutSerializer

logger = logging.getLogger(__name__)

_IST = timezone(timedelta(hours=5, minutes=30))


def _serialize_logs(logs):
    return [EmailLogOutSerializer({
        'id': l.id,
        'batch_code': l.batch_id,
        'learner_email': l.learner_email,
        'class_date': l.class_date,
        'status': l.status,
        'attempt_count': l.attempt_count,
        'error_message': l.error_message,
        'sent_at': l.sent_at,
        'created_at': l.created_at,
    }).data for l in logs]


@api_view(['GET'])
def get_email_logs(request, batch_code):
    """Return email logs for a batch, with optional date and status filters."""
    if not Batch.objects.filter(batch_code=batch_code).exists():
        return Response({'detail': f"Batch '{batch_code}' not found."}, status=status.HTTP_404_NOT_FOUND)

    qs = EmailLog.objects.filter(batch_id=batch_code)

    date_param = request.GET.get('date')
    if date_param:
        try:
            filter_date = date.fromisoformat(date_param)
            qs = qs.filter(class_date=filter_date)
        except ValueError:
            pass

    log_status = request.GET.get('status')
    if log_status is not None:
        valid_statuses = {'queued', 'sent', 'failed', 'cancelled'}
        if log_status not in valid_statuses:
            return Response(
                {'detail': f"Invalid status '{log_status}'. Must be one of: {', '.join(sorted(valid_statuses))}"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        qs = qs.filter(status=log_status)

    try:
        limit = int(request.GET.get('limit', 500))
        limit = max(1, min(limit, 5000))
    except ValueError:
        limit = 500

    logs = qs.order_by('-created_at')[:limit]
    return Response(_serialize_logs(logs))


@api_view(['GET'])
def api_sent_today(request):
    """JSON endpoint: sent emails for today — used by dashboard JS modal."""
    today = datetime.now(_IST).date()
    qs = EmailLog.objects.filter(class_date=today, status='sent')
    search = request.GET.get('search', '')
    if search:
        from django.db.models import Q
        qs = qs.filter(
            Q(learner_email__icontains=search) | Q(batch_id__icontains=search)
        )
    logs = qs.order_by('-sent_at')
    return Response(_serialize_logs(logs))


@api_view(['GET'])
def api_failed_today(request):
    """JSON endpoint: failed emails for today — used by dashboard JS modal."""
    today = datetime.now(_IST).date()
    qs = EmailLog.objects.filter(class_date=today, status='failed')
    search = request.GET.get('search', '')
    if search:
        from django.db.models import Q
        qs = qs.filter(
            Q(learner_email__icontains=search) | Q(batch_id__icontains=search)
        )
    logs = qs.order_by('-created_at')
    return Response(_serialize_logs(logs))
