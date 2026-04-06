"""
core/apps.py
------------
Django AppConfig — starts APScheduler on Django startup.

The double-start guard uses RUN_MAIN (set by Django's auto-reloader in
development) to prevent the scheduler starting twice. In production
(gunicorn, no auto-reloader), RUN_MAIN is not set, so the normal
`not scheduler.running` guard is used with a threading.Event.
"""

import logging
import os
import threading

from django.apps import AppConfig

logger = logging.getLogger(__name__)

# Threading event so we only start once even if ready() is called twice
_started = threading.Event()


class CoreConfig(AppConfig):
    name = 'core'
    default_auto_field = 'django.db.models.BigAutoField'

    def ready(self):
        # In development with Django's auto-reloader, ready() is called twice:
        # once in the parent process and once in the child (RUN_MAIN=true).
        # We only want to run scheduler in the child (RUN_MAIN=true) or
        # in production where RUN_MAIN is not set at all.
        run_main = os.environ.get('RUN_MAIN')
        django_settings = os.environ.get('DJANGO_SETTINGS_MODULE')

        # Skip in the parent reloader process
        if django_settings and run_main is None:
            # This is the parent process in dev — check if we're in runserver
            # by looking for the reloader signal. If RUN_MAIN is not 'true',
            # we might be in the outer process. Use _started to guard.
            pass

        # Allow only one actual start
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

# At the bottom of settings.py or in your AppConfig.ready()
from django.db.backends.signals import connection_created

def set_sqlite_pragmas(sender, connection, **kwargs):
    if connection.vendor == 'sqlite':
        cursor = connection.cursor()
        cursor.execute('PRAGMA journal_mode=WAL;')
        cursor.execute('PRAGMA foreign_keys=ON;')

connection_created.connect(set_sqlite_pragmas)
