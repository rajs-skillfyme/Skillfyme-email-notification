"""
edtech/urls.py
--------------
Root URL configuration.

CRITICAL ORDERING: /api/batch/upload-learners/ MUST be defined BEFORE
/api/batch/<batch_code>/ to prevent Django's sequential URL matching
from capturing "upload-learners" as a batch_code.
"""

from django.urls import path
from core.views import auth_views, batch_views, cancel_views, log_views, dashboard_views
from django.shortcuts import redirect

urlpatterns = [
    # Root redirect
    path('', lambda request: redirect('/login/', permanent=False)),

    # Auth
    path('login/', auth_views.login_view, name='login'),
    path('logout/', auth_views.logout_view, name='logout'),

    # Health
    path('health/', batch_views.health_view, name='health'),

    # ----------------------------------------------------------------
    # API — batch (upload-learners MUST come before <batch_code>)
    # ----------------------------------------------------------------
    path('api/batch/create/', batch_views.create_batch, name='batch-create'),
    path('api/batch/upload-learners/', batch_views.upload_learners, name='batch-upload-learners'),
    path('api/batch/<str:batch_code>/', batch_views.batch_detail, name='batch-detail'),
    path('api/batches/', batch_views.list_batches, name='batch-list'),

    # API — cancellations / postponements
    path('api/batch/<str:batch_code>/cancel-class/', cancel_views.cancel_class_view, name='cancel-class'),
    path('api/batch/<str:batch_code>/postpone-class/', cancel_views.postpone_class_view, name='postpone-class'),

    # API — email logs
    path('api/batch/<str:batch_code>/email-logs/', log_views.get_email_logs, name='email-logs'),
    path('api/dashboard/email-logs/sent-today/', log_views.api_sent_today, name='api-sent-today'),
    path('api/dashboard/email-logs/failed-today/', log_views.api_failed_today, name='api-failed-today'),

    # Dashboard HTML pages
    path('dashboard/', dashboard_views.dashboard, name='dashboard'),
    path('dashboard/email-logs/sent-today/', dashboard_views.sent_today_page, name='sent-today-page'),
    path('dashboard/email-logs/failed-today/', dashboard_views.failed_today_page, name='failed-today-page'),
    path('dashboard/email-logs/last-sent/', dashboard_views.last_sent_page, name='last-sent-page'),
    path('dashboard/batches/', dashboard_views.batches_page, name='batches-page'),
    path('dashboard/batch/<str:batch_code>/', dashboard_views.batch_detail_dashboard, name='batch-detail-dashboard'),
]
