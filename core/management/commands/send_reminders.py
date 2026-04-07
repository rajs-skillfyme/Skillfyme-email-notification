from django.core.management.base import BaseCommand
from django.conf import settings
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
import logging

logger = logging.getLogger(__name__)
_IST = ZoneInfo(settings.APP_TIMEZONE)


class Command(BaseCommand):
    help = 'Send reminder emails for classes starting in MINUTES_BEFORE_CLASS minutes'

    def handle(self, *args, **options):
        from core.models import Batch, Learner, Cancellation
        from core.services.email_service import send_email_with_retry, send_instructor_reminder_email

        now_ist = datetime.now(tz=_IST)
        minutes_before = settings.MINUTES_BEFORE_CLASS
        target_time = now_ist + timedelta(minutes=minutes_before)

        logger.info('Cron running at %s — looking for classes at %s', now_ist.strftime('%H:%M'), target_time.strftime('%H:%M'))

        today = date.today()
        active_batches = Batch.objects.filter(
            batch_start_date__lte=today,
            batch_end_date__gte=today,
        )

        for batch in active_batches:
            # Check if today is a class day
            day_map = {'Mon': 0, 'Tue': 1, 'Wed': 2, 'Thu': 3, 'Fri': 4, 'Sat': 5, 'Sun': 6}
            class_days = [day_map[d.strip()] for d in batch.class_days.split(',') if d.strip() in day_map]
            if now_ist.weekday() not in class_days:
                continue

            # Check if class time matches target time (within 1 minute window)
            h, m = map(int, batch.class_time.split(':'))
            class_dt = datetime(today.year, today.month, today.day, h, m, 0, tzinfo=_IST)
            diff = abs((class_dt - target_time).total_seconds())
            if diff > 60:
                continue

            # Check if cancelled
            cancelled = Cancellation.objects.filter(batch_id=batch.batch_code, cancelled_date=today).first()
            if cancelled:
                logger.info('Batch %s is cancelled today — skipping.', batch.batch_code)
                continue

            # Send emails
            learners = list(Learner.objects.filter(batch_id=batch.batch_code))
            if not learners:
                logger.info('No learners for batch %s — skipping.', batch.batch_code)
                continue

            logger.info('Sending %d reminder emails for batch %s.', len(learners), batch.batch_code)

            for learner in learners:
                send_email_with_retry(
                    batch_code=batch.batch_code,
                    learner_email=learner.email,
                    learner_name=learner.learner_name,
                    product_title=batch.product_title,
                    class_date=today,
                    class_time=batch.class_time,
                    instructor_name=batch.instructor_name,
                )

            if batch.instructor_email:
                send_instructor_reminder_email(
                    batch_code=batch.batch_code,
                    instructor_email=batch.instructor_email,
                    instructor_name=batch.instructor_name,
                    product_title=batch.product_title,
                    class_date=today,
                    class_time=batch.class_time,
                )
