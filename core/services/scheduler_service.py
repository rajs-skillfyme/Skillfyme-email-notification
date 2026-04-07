from __future__ import annotations
import logging, os, time
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

_CHUNK_HOURS = [3, 2, 1]

def _job_id(batch_code, class_date, chunk):
    return f'email_reminder__{batch_code}__{class_date.isoformat()}__{chunk}'

def _split_learners(learners, n=3):
    total = len(learners)
    chunk_size = max(1, (total + n - 1) // n)
    return [learners[i:i + chunk_size] for i in range(0, total, chunk_size)]

def _send_chunk_emails(batch_code, class_date_iso, chunk_index):
    import django.db
    from core.models import Batch, Cancellation, Learner
    from core.services.email_service import mark_cancelled, send_email_with_retry, send_instructor_reminder_email
    class_date = date.fromisoformat(class_date_iso)
    django.db.close_old_connections()
    try:
        cancelled = Cancellation.objects.filter(batch_id=batch_code, cancelled_date=class_date).first()
        if cancelled:
            logger.info('Class cancelled for batch %s on %s — marking as cancelled.', batch_code, class_date)
            for learner in Learner.objects.filter(batch_id=batch_code):
                mark_cancelled(batch_code=batch_code, learner_email=learner.email, class_date=class_date)
            return
        try:
            batch = Batch.objects.get(batch_code=batch_code)
        except Batch.DoesNotExist:
            logger.warning('Scheduler fired for unknown batch %r — skipping.', batch_code)
            return
        all_learners = list(Learner.objects.filter(batch_id=batch_code))
        if not all_learners:
            logger.info('No learners for batch %s — nothing to send.', batch_code)
            return
        chunks = _split_learners(all_learners, 3)
        if chunk_index >= len(chunks):
            logger.info('Chunk %d does not exist for batch %s — skipping.', chunk_index + 1, batch_code)
            return
        chunk = chunks[chunk_index]
        logger.info('Sending chunk %d/%d (%d learners) for batch %s (class: %s).', chunk_index + 1, len(chunks), len(chunk), batch_code, class_date)
        for idx, learner in enumerate(chunk):
            try:
                send_email_with_retry(batch_code=batch_code, learner_email=learner.email, learner_name=learner.learner_name, product_title=batch.product_title, class_date=class_date, class_time=batch.class_time, instructor_name=batch.instructor_name, hours_before=_CHUNK_HOURS[chunk_index])
            except Exception as exc:
                logger.error('Email failed for %s in batch %s: %s', learner.email, batch_code, exc)
            if idx < len(chunk) - 1:
                time.sleep(1)
        if chunk_index == 2:
            if batch.instructor_email:
                send_instructor_reminder_email(batch_code=batch_code, instructor_email=batch.instructor_email, instructor_name=batch.instructor_name, product_title=batch.product_title, class_date=class_date, class_time=batch.class_time)
                logger.info('Instructor reminder sent for batch %s.', batch_code)
            else:
                logger.info('No instructor_email for batch %s — skipping.', batch_code)
    except Exception:
        logger.exception('Unexpected error in _send_chunk_emails for batch %s chunk %d.', batch_code, chunk_index + 1)
    finally:
        django.db.connection.close()

def schedule_batch_jobs(batch):
    from core.utils.validators import get_upcoming_class_dates
    upcoming = get_upcoming_class_dates(class_days_str=batch.class_days, batch_start_date=batch.batch_start_date, batch_end_date=batch.batch_end_date)
    h, m = map(int, batch.class_time.split(':'))
    added = 0
    for class_date in upcoming:
        class_dt_ist = datetime(class_date.year, class_date.month, class_date.day, h, m, 0, tzinfo=_IST)
        now_ist = datetime.now(tz=_IST)
        for chunk_index, hours_before in enumerate(_CHUNK_HOURS):
            trigger_dt = class_dt_ist - timedelta(hours=hours_before)
            if trigger_dt <= now_ist:
                if class_dt_ist > now_ist:
                    trigger_dt = now_ist + timedelta(seconds=5 + chunk_index * 10)
                else:
                    continue
            job_id = _job_id(batch.batch_code, class_date, chunk_index + 1)
            scheduler.add_job(_send_chunk_emails, trigger=DateTrigger(run_date=trigger_dt), id=job_id, name=f'Reminder chunk {chunk_index + 1}: {batch.batch_code} on {class_date}', args=[batch.batch_code, class_date.isoformat(), chunk_index], misfire_grace_time=3600, replace_existing=True)
            added += 1
    logger.info('Scheduled %d jobs (3 per date) for batch %s.', added, batch.batch_code)
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
