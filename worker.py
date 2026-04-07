import os
import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'edtech.settings')
django.setup()

from core.services.scheduler_service import scheduler, reschedule_all_batches

if not scheduler.running:
    scheduler.start()

reschedule_all_batches()

import time
while True:
    time.sleep(60)
