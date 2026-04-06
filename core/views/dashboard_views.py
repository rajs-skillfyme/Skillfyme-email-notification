"""
core/views/dashboard_views.py
------------------------------
Admin dashboard HTML views using Django TemplateResponse.
All Jinja2 expressions converted to Django template syntax.
Context variables computed in view (msg/err from request.GET, active_batch_count, etc.)
"""

import logging
from datetime import date

from django.shortcuts import render
from django.template.response import TemplateResponse

from core.models import Batch, Cancellation, EmailLog, Learner, Postponement
from core.services.scheduler_service import get_scheduler_status
from core.utils.validators import get_upcoming_class_dates

logger = logging.getLogger(__name__)


def dashboard(request):
    """Main admin dashboard: batch list, CSV upload, email log summary."""
    today = date.today()
    batches = Batch.objects.order_by('batch_code')

    batch_rows = []
    active_count = 0
    for b in batches:
        count = Learner.objects.filter(batch_id=b.batch_code).count()
        is_active = b.batch_end_date >= today
        if is_active:
            active_count += 1
        batch_rows.append({
            'batch_code': b.batch_code,
            'product_title': b.product_title,
            'class_days': b.class_days,
            'class_time': b.class_time,
            'learner_count': count,
            'batch_end_date': b.batch_end_date,
            'is_active': is_active,
        })

    sent_today = EmailLog.objects.filter(class_date=today, status='sent').count()
    failed_today = EmailLog.objects.filter(class_date=today, status='failed').count()
    last_log = EmailLog.objects.filter(status='sent').order_by('-sent_at').first()

    context = {
        'batch_rows': batch_rows,
        'sent_today': sent_today,
        'failed_today': failed_today,
        'last_sent_at': last_log.sent_at if last_log else None,
        'scheduler_status': get_scheduler_status(),
        'today': today,
        'active_batch_count': active_count,
        'total_batch_count': len(batch_rows),
        # Flash messages from query params (replaces Jinja2 request.query_params)
        'msg': request.GET.get('msg', ''),
        'err': request.GET.get('err', ''),
    }
    return render(request, 'dashboard.html', context)


def sent_today_page(request):
    today = date.today()
    search = request.GET.get('search', '')
    qs = EmailLog.objects.filter(class_date=today, status='sent')
    if search:
        from django.db.models import Q
        qs = qs.filter(
            Q(learner_email__icontains=search) | Q(batch_id__icontains=search)
        )
    logs = list(qs.order_by('-sent_at'))
    return render(request, 'email_logs_page.html', {
        'logs': logs,
        'page_title': 'Emails Sent Today',
        'mode': 'sent',
        'search': search,
        'today': today,
        'log_count': len(logs),
        'log_plural': 's' if len(logs) != 1 else '',
    })


def failed_today_page(request):
    today = date.today()
    search = request.GET.get('search', '')
    qs = EmailLog.objects.filter(class_date=today, status='failed')
    if search:
        from django.db.models import Q
        qs = qs.filter(
            Q(learner_email__icontains=search) | Q(batch_id__icontains=search)
        )
    logs = list(qs.order_by('-created_at'))
    return render(request, 'email_logs_page.html', {
        'logs': logs,
        'page_title': 'Failed Emails Today',
        'mode': 'failed',
        'search': search,
        'today': today,
        'log_count': len(logs),
        'log_plural': 's' if len(logs) != 1 else '',
    })


def last_sent_page(request):
    search = request.GET.get('search', '')
    qs = EmailLog.objects.filter(status='sent')
    if search:
        from django.db.models import Q
        qs = qs.filter(
            Q(learner_email__icontains=search) | Q(batch_id__icontains=search)
        )
    logs = list(qs.order_by('-sent_at')[:100])
    return render(request, 'email_logs_page.html', {
        'logs': logs,
        'page_title': 'Last Email Sent (Recent 100)',
        'mode': 'sent',
        'search': search,
        'today': date.today(),
        'log_count': len(logs),
        'log_plural': 's' if len(logs) != 1 else '',
    })


def batches_page(request):
    today = date.today()
    batches = Batch.objects.order_by('batch_code')
    batch_rows = []
    active_count = 0
    for b in batches:
        count = Learner.objects.filter(batch_id=b.batch_code).count()
        is_active = b.batch_end_date >= today
        if is_active:
            active_count += 1
        batch_rows.append({
            'batch_code': b.batch_code,
            'product_title': b.product_title,
            'class_days': b.class_days,
            'class_time': b.class_time,
            'learner_count': count,
            'batch_end_date': b.batch_end_date,
            'is_active': is_active,
        })

    sent_today = EmailLog.objects.filter(class_date=today, status='sent').count()
    failed_today = EmailLog.objects.filter(class_date=today, status='failed').count()
    last_log = EmailLog.objects.filter(status='sent').order_by('-sent_at').first()

    return render(request, 'dashboard.html', {
        'batch_rows': batch_rows,
        'sent_today': sent_today,
        'failed_today': failed_today,
        'last_sent_at': last_log.sent_at if last_log else None,
        'scheduler_status': get_scheduler_status(),
        'today': today,
        'active_batch_count': active_count,
        'total_batch_count': len(batch_rows),
        'msg': request.GET.get('msg', ''),
        'err': request.GET.get('err', ''),
    })


def batch_detail_dashboard(request, batch_code):
    """Batch-detail page: upcoming class dates + cancel/undo/postpone controls."""
    try:
        batch = Batch.objects.get(batch_code=batch_code)
    except Batch.DoesNotExist:
        from django.http import HttpResponse
        return HttpResponse('<h1>Batch not found</h1>', status=404)

    upcoming = get_upcoming_class_dates(
        class_days_str=batch.class_days,
        batch_start_date=batch.batch_start_date,
        batch_end_date=batch.batch_end_date,
        from_date=date.today(),
    )[:14]

    cancelled_dates = {
        c.cancelled_date
        for c in Cancellation.objects.filter(batch_id=batch_code)
    }

    postponement_map = {
        p.original_date: p
        for p in Postponement.objects.filter(batch_id=batch_code)
    }

    class_rows = []
    for d in upcoming:
        postponement = postponement_map.get(d)
        class_rows.append({
            'date': d,
            'date_str': d.strftime('%A, %d %B %Y'),
            'is_cancelled': d in cancelled_dates,
            'is_postponed': postponement is not None,
            'new_date_str': postponement.new_date.strftime('%d %b %Y') if postponement else '',
            'new_time': postponement.new_time if postponement else '',
        })

    learner_count = Learner.objects.filter(batch_id=batch_code).count()

    return render(request, 'batch_detail.html', {
        'batch': batch,
        'class_rows': class_rows,
        'learner_count': learner_count,
    })
