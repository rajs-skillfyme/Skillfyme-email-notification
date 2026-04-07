"""
core/services/email_service.py
--------------------------------
Handles all outbound email sending via SMTP — 1:1 migration from FastAPI version.

Preserved exactly:
  - Jinja2 Environment for email templates (NOT Django template engine)
  - All 6 idempotency prefix patterns (cancel__, postpone__, instructor_remind__,
    instructor_cancel__, instructor_postpone__, and plain email for reminders)
  - Retry backoff: sleep(2^attempt) after each failure (2s, 4s, 8s)
  - sent_at stored as naive IST datetime (UTC+5:30, no tzinfo)
  - Idempotency guard: return immediately if log.status == 'sent'
  - mark_cancelled: only transitions if status != 'sent'
  - All 6 subject line strings match exactly
  - SMTP sequence: ehlo → starttls → ehlo → login → sendmail, timeout=30

FIX: _send_via_smtp now uses SMTP (port 587) + STARTTLS instead of SMTP_SSL (port 465).
     The env var SMTP_PORT=587 is now correctly respected.
"""

from __future__ import annotations

import logging
import smtplib
import time
from datetime import date, datetime, timezone, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from django.conf import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Jinja2 template environment — email templates stay as Jinja2, NOT Django templates
# ---------------------------------------------------------------------------
_TEMPLATE_DIR = Path(__file__).resolve().parents[1] / 'templates'
_jinja_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATE_DIR)),
    autoescape=select_autoescape(['html']),
)

# IST timezone for sent_at timestamps
_IST_TZ = timezone(timedelta(hours=5, minutes=30))


# ---------------------------------------------------------------------------
# Internal: EmailLog helpers
# ---------------------------------------------------------------------------

def _create_or_get_log(
    batch_code: str,
    learner_email: str,
    class_date: date,
):
    """
    Fetch or create an EmailLog for the (batch_code, class_date, learner_email) triplet.
    This is the idempotency gate — avoids duplicate sends.
    """
    from core.models import EmailLog
    log = EmailLog.objects.filter(
        batch_id=batch_code,
        learner_email=learner_email,
        class_date=class_date,
    ).first()
    if log is None:
        log = EmailLog(
            batch_id=batch_code,
            learner_email=learner_email,
            class_date=class_date,
            status='queued',
            attempt_count=0,
        )
        log.save()
    return log


# ---------------------------------------------------------------------------
# Internal: SMTP send
# FIX: Use SMTP + STARTTLS on port 587 (was wrongly using SMTP_SSL on port 465)
# ---------------------------------------------------------------------------

def _send_via_smtp(
    to_email: str,
    to_name: str,
    subject: str,
    html_body: str,
) -> None:
    """Low-level SMTP send. Raises on any failure (caller handles retries)."""
    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From'] = f'{settings.SENDER_NAME} <{settings.SENDER_EMAIL}>'
    msg['To'] = f'{to_name} <{to_email}>'
    msg.attach(MIMEText(html_body, 'html', 'utf-8'))

    # FIXED: Use SMTP with STARTTLS on port 587 (matches SMTP_PORT=587 in .env)
    # The old code used SMTP_SSL on hardcoded port 465 which ignored SMTP_PORT entirely.
    with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=30) as server:
        server.ehlo()
        server.starttls()
        server.ehlo()
        server.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
        server.sendmail(settings.SENDER_EMAIL, [to_email], msg.as_string())

# ---------------------------------------------------------------------------
# Public: send_email_with_retry (learner reminder)
# ---------------------------------------------------------------------------

def send_email_with_retry(
    *,
    batch_code: str,
    learner_email: str,
    learner_name: str,
    product_title: str,
    class_date: date,
    class_time: str,
    instructor_name: str,
    hours_before: int = 1,
) -> None:
    """
    Render and send a reminder email to *learner_email*, retrying on failure.
    Log key: learner_email as-is (no prefix) — reminder emails.
    """
    log = _create_or_get_log(batch_code, learner_email, class_date)

    if log.status == 'sent':
        logger.info(
            'Skipping duplicate send: %s / %s / %s already sent.',
            batch_code, learner_email, class_date,
        )
        return

    context = {
        'learner_name': learner_name,
        'learner_email': learner_email,
        'batch_code': batch_code,
        'product_title': product_title,
        'class_time': class_time,
        'class_date': class_date.strftime('%A, %d %B %Y'),
        'instructor_name': instructor_name,
    }
    html_body = _jinja_env.get_template('email_template.html').render(**context)
    hours_label = f'{hours_before} hour' if hours_before == 1 else f'{hours_before} hours'
    subject = f'Reminder: Your {product_title} class is in {hours_label}!'

    max_attempts = settings.MAX_RETRY_ATTEMPTS
    last_error: str | None = None

    for attempt in range(1, max_attempts + 1):
        try:
            _send_via_smtp(
                to_email=learner_email,
                to_name=learner_name,
                subject=subject,
                html_body=html_body,
            )
            log.status = 'sent'
            log.attempt_count = attempt
            log.sent_at = datetime.now(_IST_TZ).replace(tzinfo=None)
            log.error_message = None
            log.save()
            logger.info('Email sent: %s → %s (attempt %d)', batch_code, learner_email, attempt)
            return
        except Exception as exc:
            last_error = str(exc)
            log.attempt_count = attempt
            log.status = 'failed'
            log.error_message = last_error
            log.save()
            logger.warning(
                'Email send failed (attempt %d/%d) for %s: %s',
                attempt, max_attempts, learner_email, last_error,
            )
            if attempt < max_attempts:
                backoff = 2 ** attempt  # 2s, 4s, 8s
                logger.debug('Backing off %ds before retry.', backoff)
                time.sleep(backoff)

    log.status = 'failed'
    log.error_message = last_error
    log.save()
    logger.error(
        'All %d attempts failed for %s / %s / %s. Last error: %s',
        max_attempts, batch_code, learner_email, class_date, last_error,
    )


# ---------------------------------------------------------------------------
# Public: send_cancellation_email (learner)
# ---------------------------------------------------------------------------

def send_cancellation_email(
    *,
    batch_code: str,
    learner_email: str,
    learner_name: str,
    product_title: str,
    class_date: date,
    class_time: str,
    instructor_name: str,
    hours_before: int = 1,
) -> None:
    """
    Send a class-cancellation notice to a single learner.
    Log key prefix: cancel__{learner_email}
    """
    log_key_email = f'cancel__{learner_email}'
    log = _create_or_get_log(batch_code, log_key_email, class_date)

    if log.status == 'sent':
        logger.info('Cancellation email already sent to %s for %s/%s.', learner_email, batch_code, class_date)
        return

    context = {
        'learner_name': learner_name,
        'learner_email': learner_email,
        'batch_code': batch_code,
        'product_title': product_title,
        'class_time': class_time,
        'class_date': class_date.strftime('%A, %d %B %Y'),
        'instructor_name': instructor_name,
    }
    html_body = _jinja_env.get_template('email_cancelled.html').render(**context)
    subject = f"Class Cancelled: {product_title} on {class_date.strftime('%d %B %Y')}"

    max_attempts = settings.MAX_RETRY_ATTEMPTS
    last_error: str | None = None

    for attempt in range(1, max_attempts + 1):
        try:
            _send_via_smtp(
                to_email=learner_email,
                to_name=learner_name,
                subject=subject,
                html_body=html_body,
            )
            log.status = 'sent'
            log.attempt_count = attempt
            log.sent_at = datetime.now(_IST_TZ).replace(tzinfo=None)
            log.error_message = None
            log.save()
            logger.info('Cancellation email sent: %s → %s (attempt %d)', batch_code, learner_email, attempt)
            return
        except Exception as exc:
            last_error = str(exc)
            log.attempt_count = attempt
            log.status = 'failed'
            log.error_message = last_error
            log.save()
            logger.warning('Cancellation email failed (attempt %d/%d) for %s: %s', attempt, max_attempts, learner_email, last_error)
            if attempt < max_attempts:
                time.sleep(2 ** attempt)

    log.status = 'failed'
    log.error_message = last_error
    log.save()


# ---------------------------------------------------------------------------
# Public: send_postponement_email (learner)
# ---------------------------------------------------------------------------

def send_postponement_email(
    *,
    batch_code: str,
    learner_email: str,
    learner_name: str,
    product_title: str,
    original_date: date,
    new_date: date,
    new_time: str,
    instructor_name: str,
    hours_before: int = 1,
) -> None:
    """
    Send a class-postponement notice to a single learner.
    Log key prefix: postpone__{learner_email}
    """
    log_key_email = f'postpone__{learner_email}'
    log = _create_or_get_log(batch_code, log_key_email, original_date)

    if log.status == 'sent':
        logger.info('Postponement email already sent to %s for %s/%s.', learner_email, batch_code, original_date)
        return

    context = {
        'learner_name': learner_name,
        'learner_email': learner_email,
        'batch_code': batch_code,
        'product_title': product_title,
        'original_date': original_date.strftime('%A, %d %B %Y'),
        'new_date': new_date.strftime('%A, %d %B %Y'),
        'new_time': new_time,
        'instructor_name': instructor_name,
    }
    html_body = _jinja_env.get_template('email_postponed.html').render(**context)
    subject = f"Class Rescheduled: {product_title} — New date {new_date.strftime('%d %B %Y')}"

    max_attempts = settings.MAX_RETRY_ATTEMPTS
    last_error: str | None = None

    for attempt in range(1, max_attempts + 1):
        try:
            _send_via_smtp(
                to_email=learner_email,
                to_name=learner_name,
                subject=subject,
                html_body=html_body,
            )
            log.status = 'sent'
            log.attempt_count = attempt
            log.sent_at = datetime.now(_IST_TZ).replace(tzinfo=None)
            log.error_message = None
            log.save()
            logger.info('Postponement email sent: %s → %s (attempt %d)', batch_code, learner_email, attempt)
            return
        except Exception as exc:
            last_error = str(exc)
            log.attempt_count = attempt
            log.status = 'failed'
            log.error_message = last_error
            log.save()
            logger.warning('Postponement email failed (attempt %d/%d) for %s: %s', attempt, max_attempts, learner_email, last_error)
            if attempt < max_attempts:
                time.sleep(2 ** attempt)

    log.status = 'failed'
    log.error_message = last_error
    log.save()


# ---------------------------------------------------------------------------
# Public: mark_cancelled
# ---------------------------------------------------------------------------

def mark_cancelled(
    *,
    batch_code: str,
    learner_email: str,
    class_date: date,
) -> None:
    """Mark a specific email log entry as 'cancelled' without sending."""
    log = _create_or_get_log(batch_code, learner_email, class_date)
    if log.status not in ('sent',):
        log.status = 'cancelled'
        log.save()


# ---------------------------------------------------------------------------
# Public: send_instructor_reminder_email
# ---------------------------------------------------------------------------

def send_instructor_reminder_email(
    *,
    batch_code: str,
    instructor_email: str,
    instructor_name: str,
    product_title: str,
    class_date: date,
    class_time: str,
) -> None:
    """
    Send a class-start reminder to the instructor.
    Log key prefix: instructor_remind__{instructor_email}
    """
    log_key_email = f'instructor_remind__{instructor_email}'
    log = _create_or_get_log(batch_code, log_key_email, class_date)

    if log.status == 'sent':
        logger.info('Instructor reminder already sent to %s for %s/%s.', instructor_email, batch_code, class_date)
        return

    context = {
        'instructor_name': instructor_name,
        'instructor_email': instructor_email,
        'batch_code': batch_code,
        'product_title': product_title,
        'class_time': class_time,
        'class_date': class_date.strftime('%A, %d %B %Y'),
    }
    html_body = _jinja_env.get_template('email_instructor_reminder.html').render(**context)
    subject = f'Action Required: Start your {product_title} class in 1 hour!'

    max_attempts = settings.MAX_RETRY_ATTEMPTS
    last_error: str | None = None

    for attempt in range(1, max_attempts + 1):
        try:
            _send_via_smtp(
                to_email=instructor_email,
                to_name=instructor_name,
                subject=subject,
                html_body=html_body,
            )
            log.status = 'sent'
            log.attempt_count = attempt
            log.sent_at = datetime.now(_IST_TZ).replace(tzinfo=None)
            log.error_message = None
            log.save()
            logger.info('Instructor reminder sent: %s → %s (attempt %d)', batch_code, instructor_email, attempt)
            return
        except Exception as exc:
            last_error = str(exc)
            log.attempt_count = attempt
            log.status = 'failed'
            log.error_message = last_error
            log.save()
            logger.warning('Instructor reminder failed (attempt %d/%d) for %s: %s', attempt, max_attempts, instructor_email, last_error)
            if attempt < max_attempts:
                time.sleep(2 ** attempt)

    log.status = 'failed'
    log.error_message = last_error
    log.save()


# ---------------------------------------------------------------------------
# Public: send_instructor_postponement_email
# ---------------------------------------------------------------------------

def send_instructor_postponement_email(
    *,
    batch_code: str,
    instructor_email: str,
    instructor_name: str,
    product_title: str,
    original_date: date,
    new_date: date,
    new_time: str,
) -> None:
    """
    Send a postponement notice to the instructor immediately.
    Log key prefix: instructor_postpone__{instructor_email}
    """
    log_key_email = f'instructor_postpone__{instructor_email}'
    log = _create_or_get_log(batch_code, log_key_email, original_date)

    if log.status == 'sent':
        logger.info('Instructor postponement email already sent to %s for %s/%s.', instructor_email, batch_code, original_date)
        return

    context = {
        'instructor_name': instructor_name,
        'instructor_email': instructor_email,
        'batch_code': batch_code,
        'product_title': product_title,
        'original_date': original_date.strftime('%A, %d %B %Y'),
        'new_date': new_date.strftime('%A, %d %B %Y'),
        'new_time': new_time,
    }
    html_body = _jinja_env.get_template('email_instructor_postponed.html').render(**context)
    subject = f"Class Rescheduled: {product_title} — New date {new_date.strftime('%d %B %Y')}"

    max_attempts = settings.MAX_RETRY_ATTEMPTS
    last_error: str | None = None

    for attempt in range(1, max_attempts + 1):
        try:
            _send_via_smtp(
                to_email=instructor_email,
                to_name=instructor_name,
                subject=subject,
                html_body=html_body,
            )
            log.status = 'sent'
            log.attempt_count = attempt
            log.sent_at = datetime.now(_IST_TZ).replace(tzinfo=None)
            log.error_message = None
            log.save()
            logger.info('Instructor postponement email sent: %s → %s (attempt %d)', batch_code, instructor_email, attempt)
            return
        except Exception as exc:
            last_error = str(exc)
            log.attempt_count = attempt
            log.status = 'failed'
            log.error_message = last_error
            log.save()
            logger.warning('Instructor postponement email failed (attempt %d/%d) for %s: %s', attempt, max_attempts, instructor_email, last_error)
            if attempt < max_attempts:
                time.sleep(2 ** attempt)

    log.status = 'failed'
    log.error_message = last_error
    log.save()


# ---------------------------------------------------------------------------
# Public: send_instructor_cancellation_email
# ---------------------------------------------------------------------------

def send_instructor_cancellation_email(
    *,
    batch_code: str,
    instructor_email: str,
    instructor_name: str,
    product_title: str,
    class_date: date,
    class_time: str,
) -> None:
    """
    Send a cancellation notice to the instructor.
    Log key prefix: instructor_cancel__{instructor_email}
    """
    log_key_email = f'instructor_cancel__{instructor_email}'
    log = _create_or_get_log(batch_code, log_key_email, class_date)

    if log.status == 'sent':
        logger.info('Instructor cancellation email already sent to %s for %s/%s.', instructor_email, batch_code, class_date)
        return

    context = {
        'instructor_name': instructor_name,
        'instructor_email': instructor_email,
        'batch_code': batch_code,
        'product_title': product_title,
        'class_time': class_time,
        'class_date': class_date.strftime('%A, %d %B %Y'),
    }
    html_body = _jinja_env.get_template('email_instructor_cancelled.html').render(**context)
    subject = f"Class Cancelled: {product_title} on {class_date.strftime('%d %B %Y')}"

    max_attempts = settings.MAX_RETRY_ATTEMPTS
    last_error: str | None = None

    for attempt in range(1, max_attempts + 1):
        try:
            _send_via_smtp(
                to_email=instructor_email,
                to_name=instructor_name,
                subject=subject,
                html_body=html_body,
            )
            log.status = 'sent'
            log.attempt_count = attempt
            log.sent_at = datetime.now(_IST_TZ).replace(tzinfo=None)
            log.error_message = None
            log.save()
            logger.info('Instructor cancellation email sent: %s → %s (attempt %d)', batch_code, instructor_email, attempt)
            return
        except Exception as exc:
            last_error = str(exc)
            log.attempt_count = attempt
            log.status = 'failed'
            log.error_message = last_error
            log.save()
            logger.warning('Instructor cancellation email failed (attempt %d/%d) for %s: %s', attempt, max_attempts, instructor_email, last_error)
            if attempt < max_attempts:
                time.sleep(2 ** attempt)

    log.status = 'failed'
    log.error_message = last_error
    log.save()