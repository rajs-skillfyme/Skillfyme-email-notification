"""
core/apps.py
------------
Django AppConfig — starts APScheduler on Django startup.
"""

import logging
import os
import threading

from django.apps import AppConfig

logger = logging.getLogger(__name__)

_started = threading.Event()


class CoreConfig(AppConfig):
    name = 'core'
    default_auto_field = 'django.db.models.BigAutoField'

    def ready(self):
        if _started.is_set():
            return
        _started.set()

        try:
            from core.services.scheduler_service import scheduler, reschedule_all_batches
            if not scheduler.running:
                scheduler.start()
                logger.info('APScheduler started from AppConfig.ready()')
            reschedule_all_batches()
        except Exception:
            logger.exception('Failed to start scheduler in AppConfig.ready()')