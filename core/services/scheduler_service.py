from __future__ import annotations
import logging, os, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from apscheduler.executors.pool import ThreadPoolExecutor as APThreadPoolExecutor
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.date import DateTrigger
from django.conf import settings
from django.db import OperationalError, ProgrammingError

logger = logging.getLogger(__name__)
_IST = ZoneInfo(settings.APP_TIMEZONE)
_db_url = os.environ.get('DATABASE_URL', '').replace('postgresql://', 'postgresql+psycopg2://')

scheduler = BackgroundScheduler(
    jobstores={'default': SQLAlchemyJobStore(url=_db_url)},
    executors={'default': APThreadPoolExecutor(10)},
    timezone=settings.APP_TIMEZONE,
    daemon=True,
)

def _job_id(batch_code, class_date):
    return f'email_reminder__{batch_code}__{class_date.isoformat()}'

def _send_batch_emails(batch_code, class_date_iso):
    import django.db
    from core.models import Batch, Cancellation, Learner
    from core.services.email_service import mark_cancelled, send_email_with_retry, send_instructor_reminder_email
    class_date = date.fromisoformat(class_date_iso)
    django.db.close_old_connections()
    try:
        cancelled = Cancellation.objects.filter(batch_id=batch_code, cancelled_date=class_date).first()
        if cancelled:
            logger.info('Class cancelled for batch %s on %s — marking all emails as cancelled.', batch_code, class_date)
            for learner in Learner.objects.filter(batch_id=batch_code):
                mark_cancelled(batch_code=batch_code, learner_email=learner.email, class_date=class_date)
            return
        try:
            batch = Batch.objects.get(batch_code=batch_code)
        except Batch.DoesNotExist:
            logger.warning('Scheduler fired for unknown batch %r — skipping.', batch_code)
            return
        learners = list(Learner.objects.filter(batch_id=batch_code))
        if not learners:
            logger.info('No learners for batch %s on %s — nothing to send.', batch_code, class_date)
            return
        logger.info('Sending %d reminder emails for batch %s (class date: %s).', len(learners), batch_code, class_date)
        def _send_one(learner):
            send_email_with_retry(batch_code=batch_code, learner_email=learner.email, learner_name=learner.learner_name, product_title=batch.product_title, class_date=class_date, class_time=batch.class_time, instructor_name=batch.instructor_name)
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(_send_one, l) for l in learners]
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as exc:
                    logger.error('Email send failed for a learner in batch %s: %s', batch_code, exc)
        if batch.instructor_email:
            send_instructor_reminder_email(batch_code=batch_code, instructor_email=batch.instructor_email, instructor_name=batch.instructor_name, product_title=batch.product_title, class_date=class_date, class_time=batch.class_time)
        else:
            logger.info('No instructor_email set for batch %s — skipping instructor reminder.', batch_code)
    except Exception:
        logger.exception('Unexpected error in _send_batch_emails for batch %s.', batch_code)
    finally:
        django.db.connection.close()

def schedule_batch_jobs(batch):
    from core.utils.validators import get_upcoming_class_dates
    upcoming = get_upcoming_class_dates(class_days_str=batch.class_days, batch_start_date=batch.batch_start_date, batch_end_date=batch.batch_end_date)
    minutes_before = settings.MINUTES_BEFORE_CLASS
    h, m = map(int, batch.class_time.split(':'))
    added = 0
    for class_date in upcoming:
        class_dt_ist = datetime(class_date.year, class_date.month, class_date.day, h, m, 0, tzinfo=_IST)
        trigger_dt = class_dt_ist - timedelta(minutes=minutes_before)
        now_ist = datetime.now(tz=_IST)
        if trigger_dt <= now_ist:
            if class_dt_ist > now_ist:
                trigger_dt = now_ist + timedelta(seconds=5)
            else:
                continue
        scheduler.add_job(_send_batch_emails, trigger=DateTrigger(run_date=trigger_dt), id=_job_id(batch.batch_code, class_date), name=f'Reminder: {batch.batch_code} on {class_date}', args=[batch.batch_code, class_date.isoformat()], misfire_grace_time=600, replace_existing=True)
        added += 1
    logger.info('Scheduled %d jobs for batch %s.', added, batch.batch_code)
    return added

def remove_batch_jobs(batch_code):
    removed = 0
    prefix = f'email_reminder__{batch_code}__'
    for job in scheduler.get_jobs():
        if job.id.startswith(prefix):
            scheduler.remove_job(job.id)
            removed += 1
    logger.info('Removed %d jobs for batch %s.', removed, batch_code)
    return removed

def reschedule_all_batches():
    import django.db
    django.db.close_old_connections()
    try:
        from core.models import Batch
        today = date.today()
        active_batches = list(Batch.objects.filter(batch_end_date__gte=today))
        logger.info('Rescheduling jobs for %d active batches on startup.', len(active_batches))
        for batch in active_batches:
            schedule_batch_jobs(batch)
    except (OperationalError, ProgrammingError) as e:
        logger.warning('reschedule_all_batches skipped — DB tables not ready: %s', e)
    except Exception:
        logger.exception('Error in reschedule_all_batches')
    finally:
        django.db.connection.close()

def get_scheduler_status():
    if scheduler.running:
        return f'running ({len(scheduler.get_jobs())} jobs)'
    return 'stopped'
