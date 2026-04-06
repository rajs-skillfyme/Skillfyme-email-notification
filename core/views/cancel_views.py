"""
core/views/cancel_views.py
---------------------------
Cancellation and postponement views — 1:1 migration from FastAPI cancel_routes.py.

CRITICAL logic preserved:
  24-hour cancellation timing branch:
    IF hours_until_class > 24:
      Schedule delayed thread (sleeps until 24h before class)
    ELSE:
      Fire immediately
  Both branches use daemon=True threads.
  Postponement emails always fire immediately (no 24h branch).

All thread functions open their own DB connections (Django ORM thread safety).
"""

import logging
import threading
import time as _time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from core.models import Batch, Cancellation, Learner, Postponement
from core.serializers import (
    CancelClassRequestSerializer,
    CancellationOutSerializer,
    PostponeClassRequestSerializer,
    PostponementOutSerializer,
    UndoCancelRequestSerializer,
)
from core.services.email_service import (
    send_cancellation_email,
    send_instructor_cancellation_email,
    send_instructor_postponement_email,
    send_postponement_email,
)

logger = logging.getLogger(__name__)

_IST = ZoneInfo(settings.APP_TIMEZONE)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _require_batch(batch_code: str):
    try:
        return Batch.objects.get(batch_code=batch_code)
    except Batch.DoesNotExist:
        return None


def _send_cancellation_emails_now(batch_code: str, class_date_iso: str) -> None:
    """
    Runs in a background thread.
    Opens its own DB connection, fetches all learners, sends cancellation emails.
    """
    import django.db
    django.db.close_old_connections()

    class_date = date.fromisoformat(class_date_iso)
    try:
        batch = Batch.objects.filter(batch_code=batch_code).first()
        if not batch:
            logger.warning('Cancellation email: batch %r not found.', batch_code)
            return
        learners = list(Learner.objects.filter(batch_id=batch_code))
        delay = settings.EMAIL_SEND_DELAY_SECONDS
        for idx, learner in enumerate(learners):
            send_cancellation_email(
                batch_code=batch_code,
                learner_email=learner.email,
                learner_name=learner.learner_name,
                product_title=batch.product_title,
                class_date=class_date,
                class_time=batch.class_time,
                instructor_name=batch.instructor_name,
            )
            if idx < len(learners) - 1:
                _time.sleep(delay)
        # Notify instructor
        if batch.instructor_email:
            send_instructor_cancellation_email(
                batch_code=batch_code,
                instructor_email=batch.instructor_email,
                instructor_name=batch.instructor_name,
                product_title=batch.product_title,
                class_date=class_date,
                class_time=batch.class_time,
            )
        else:
            logger.info('No instructor_email for batch %s — skipping instructor cancellation email.', batch_code)
    except Exception:
        logger.exception('Error sending cancellation emails for %s.', batch_code)
    finally:
        django.db.connection.close()


def _send_postponement_emails_now(
    batch_code: str,
    original_date_iso: str,
    new_date_iso: str,
    new_time: str,
) -> None:
    """
    Runs in a background thread.
    Sends postponement emails to all learners immediately.
    """
    import django.db
    django.db.close_old_connections()

    original_date = date.fromisoformat(original_date_iso)
    new_date = date.fromisoformat(new_date_iso)
    try:
        batch = Batch.objects.filter(batch_code=batch_code).first()
        if not batch:
            logger.warning('Postponement email: batch %r not found.', batch_code)
            return
        learners = list(Learner.objects.filter(batch_id=batch_code))
        delay = settings.EMAIL_SEND_DELAY_SECONDS
        for idx, learner in enumerate(learners):
            send_postponement_email(
                batch_code=batch_code,
                learner_email=learner.email,
                learner_name=learner.learner_name,
                product_title=batch.product_title,
                original_date=original_date,
                new_date=new_date,
                new_time=new_time,
                instructor_name=batch.instructor_name,
            )
            if idx < len(learners) - 1:
                _time.sleep(delay)
        # Notify instructor
        if batch.instructor_email:
            send_instructor_postponement_email(
                batch_code=batch_code,
                instructor_email=batch.instructor_email,
                instructor_name=batch.instructor_name,
                product_title=batch.product_title,
                original_date=original_date,
                new_date=new_date,
                new_time=new_time,
            )
        else:
            logger.info('No instructor_email for batch %s — skipping instructor postponement email.', batch_code)
    except Exception:
        logger.exception('Error sending postponement emails for %s.', batch_code)
    finally:
        django.db.connection.close()


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------

@api_view(['POST', 'DELETE'])
def cancel_class_view(request, batch_code):
    if request.method == 'POST':
        return _cancel_class(request, batch_code)
    elif request.method == 'DELETE':
        return _undo_cancel_class(request, batch_code)


def _cancel_class(request, batch_code):
    """
    Cancel a specific class date.

    Email timing logic (24-hour branch) — MUST be preserved exactly:
      IF hours_until_class > 24:
        Schedule to fire 24h before class
      ELSE:
        Fire immediately
    """
    batch = _require_batch(batch_code)
    if batch is None:
        return Response({'detail': f"Batch '{batch_code}' not found."}, status=status.HTTP_404_NOT_FOUND)

    ser = CancelClassRequestSerializer(data=request.data)
    ser.is_valid(raise_exception=True)
    payload = ser.validated_data

    # Idempotency: return existing record if already cancelled
    existing = Cancellation.objects.filter(
        batch_id=batch_code, cancelled_date=payload['date']
    ).first()
    if existing:
        logger.info('Class %s / %s already cancelled — returning existing record.', batch_code, payload['date'])
        return Response(CancellationOutSerializer(existing).data, status=status.HTTP_201_CREATED)

    cancellation = Cancellation.objects.create(
        batch_id=batch_code,
        cancelled_date=payload['date'],
        reason=payload.get('reason'),
    )
    logger.info('Cancelled class for batch %s on %s.', batch_code, payload['date'])

    # ---- Email timing decision — 24h branch ----
    h, m = map(int, batch.class_time.split(':'))
    class_dt_ist = datetime(
        payload['date'].year, payload['date'].month, payload['date'].day,
        h, m, 0, tzinfo=_IST,
    )
    now_ist = datetime.now(tz=_IST)
    hours_until_class = (class_dt_ist - now_ist).total_seconds() / 3600

    if hours_until_class > 24:
        # Schedule to send exactly 24 hours before class
        send_at = class_dt_ist - timedelta(hours=24)
        delay_seconds = max(0, (send_at - now_ist).total_seconds())
        logger.info(
            'Cancellation email for %s/%s scheduled in %.0f seconds (24h before class).',
            batch_code, payload['date'], delay_seconds,
        )

        def _delayed_send():
            _time.sleep(delay_seconds)
            _send_cancellation_emails_now(batch_code, payload['date'].isoformat())

        threading.Thread(target=_delayed_send, daemon=True).start()
    else:
        # Class is within 24 hours — send immediately
        logger.info(
            'Cancellation email for %s/%s firing immediately (class within 24h).',
            batch_code, payload['date'],
        )
        threading.Thread(
            target=_send_cancellation_emails_now,
            args=(batch_code, payload['date'].isoformat()),
            daemon=True,
        ).start()

    return Response(CancellationOutSerializer(cancellation).data, status=status.HTTP_201_CREATED)


def _undo_cancel_class(request, batch_code):
    batch = _require_batch(batch_code)
    if batch is None:
        return Response({'detail': f"Batch '{batch_code}' not found."}, status=status.HTTP_404_NOT_FOUND)

    ser = UndoCancelRequestSerializer(data=request.data)
    ser.is_valid(raise_exception=True)
    payload = ser.validated_data

    cancellation = Cancellation.objects.filter(
        batch_id=batch_code, cancelled_date=payload['date']
    ).first()
    if cancellation is None:
        return Response(
            {'detail': f"No cancellation found for batch '{batch_code}' on {payload['date']}."},
            status=status.HTTP_404_NOT_FOUND,
        )

    cancellation.delete()
    logger.info('Undid cancellation for batch %s on %s.', batch_code, payload['date'])
    return Response({'detail': f"Cancellation for {batch_code} on {payload['date']} has been undone."})


@api_view(['POST'])
def postpone_class_view(request, batch_code):
    """
    Postpone a specific class to a new date and time.
    Fires postponement emails immediately (no 24h branch).
    """
    batch = _require_batch(batch_code)
    if batch is None:
        return Response({'detail': f"Batch '{batch_code}' not found."}, status=status.HTTP_404_NOT_FOUND)

    ser = PostponeClassRequestSerializer(data=request.data)
    ser.is_valid(raise_exception=True)
    payload = ser.validated_data

    # Idempotency: if already postponed for this date, return existing
    existing = Postponement.objects.filter(
        batch_id=batch_code, original_date=payload['original_date']
    ).first()
    if existing:
        logger.info('Class %s / %s already postponed — returning existing record.', batch_code, payload['original_date'])
        return Response(PostponementOutSerializer(existing).data, status=status.HTTP_201_CREATED)

    postponement = Postponement.objects.create(
        batch_id=batch_code,
        original_date=payload['original_date'],
        new_date=payload['new_date'],
        new_time=payload['new_time'],
        reason=payload.get('reason'),
    )
    logger.info(
        'Postponed class for batch %s from %s to %s %s.',
        batch_code, payload['original_date'], payload['new_date'], payload['new_time'],
    )

    # Fire postponement emails immediately in background
    threading.Thread(
        target=_send_postponement_emails_now,
        args=(
            batch_code,
            payload['original_date'].isoformat(),
            payload['new_date'].isoformat(),
            payload['new_time'],
        ),
        daemon=True,
    ).start()

    return Response(PostponementOutSerializer(postponement).data, status=status.HTTP_201_CREATED)