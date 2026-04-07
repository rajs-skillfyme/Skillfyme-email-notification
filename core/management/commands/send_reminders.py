from django.core.management.base import BaseCommand
from django.conf import settings
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
import logging, time

logger = logging.getLogger(__name__)
_IST = ZoneInfo(settings.APP_TIMEZONE)
_CHUNK_OFFSETS = [180, 120, 60]

def _split_learners(learners, n=3):
    total = len(learners)
    chunk_size = max(1, (total + n - 1) // n)
    return [learners[i:i + chunk_size] for i in range(0, total, chunk_size)]

class Command(BaseCommand):
    help = 'Send chunked reminder emails at 3h/2h/1h before class'

    def handle(self, *args, **options):
        from core.models import Batch, Learner, Cancellation
        from core.services.email_service import send_email_with_retry, send_instructor_reminder_email
        now_ist = datetime.now(tz=_IST)
        today = date.today()
        logger.info('Cron running at %s IST', now_ist.strftime('%H:%M'))
        active_batches = Batch.objects.filter(batch_start_date__lte=today, batch_end_date__gte=today)
        for batch in active_batches:
            day_map = {'Mon': 0, 'Tue': 1, 'Wed': 2, 'Thu': 3, 'Fri': 4, 'Sat': 5, 'Sun': 6}
            class_days = [day_map[d.strip()] for d in batch.class_days.split(',') if d.strip() in day_map]
            if now_ist.weekday() not in class_days:
                continue
            h, m = map(int, batch.class_time.split(':'))
            class_dt = datetime(today.year, today.month, today.day, h, m, 0, tzinfo=_IST)
            cancelled = Cancellation.objects.filter(batch_id=batch.batch_code, cancelled_date=today).first()
            if cancelled:
                logger.info('Batch %s cancelled today — skipping.', batch.batch_code)
                continue
            all_learners = list(Learner.objects.filter(batch_id=batch.batch_code))
            if not all_learners:
                continue
            chunks = _split_learners(all_learners, 3)
            for chunk_index, minutes_before in enumerate(_CHUNK_OFFSETS):
                trigger_dt = class_dt - timedelta(minutes=minutes_before)
                diff = abs((trigger_dt - now_ist).total_seconds())
                if diff > 60:
                    continue
                chunk = chunks[chunk_index] if chunk_index < len(chunks) else []
                if not chunk:
                    continue
                logger.info('Sending chunk %d (%d learners) for batch %s — %d min before class.', chunk_index + 1, len(chunk), batch.batch_code, minutes_before)
                for idx, learner in enumerate(chunk):
                    try:
                        send_email_with_retry(batch_code=batch.batch_code, learner_email=learner.email, learner_name=learner.learner_name, product_title=batch.product_title, class_date=today, class_time=batch.class_time, instructor_name=batch.instructor_name, hours_before=minutes_before // 60)
                    except Exception as exc:
                        logger.error('Email failed for %s: %s', learner.email, exc)
                    if idx < len(chunk) - 1:
                        time.sleep(1)
                if chunk_index == 2 and batch.instructor_email:
                    try:
                        send_instructor_reminder_email(batch_code=batch.batch_code, instructor_email=batch.instructor_email, instructor_name=batch.instructor_name, product_title=batch.product_title, class_date=today, class_time=batch.class_time)
                        logger.info('Instructor reminder sent for batch %s.', batch.batch_code)
                    except Exception as exc:
                        logger.error('Instructor email failed for batch %s: %s', batch.batch_code, exc)
