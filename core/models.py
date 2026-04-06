"""
core/models.py
--------------
Django ORM models — strict 1:1 migration from SQLAlchemy models.
All db_table names match the original SQLite tables exactly.
All constraints and indexes are preserved.
"""

from django.db import models


class Batch(models.Model):
    batch_code    = models.CharField(max_length=200, primary_key=True)
    product_title = models.CharField(max_length=500, unique=True)
    class_days    = models.CharField(max_length=100)
    class_time    = models.CharField(max_length=5)
    batch_start_date = models.DateField()
    batch_end_date   = models.DateField()
    instructor_name  = models.CharField(max_length=300, null=True, blank=True)
    instructor_email = models.CharField(max_length=300, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = 'batches'

    def __str__(self):
        return f'<Batch {self.batch_code!r} | {self.product_title!r}>'


class Learner(models.Model):
    learner_name  = models.CharField(max_length=300)
    email         = models.CharField(max_length=300)
    batch         = models.ForeignKey(
        Batch,
        on_delete=models.CASCADE,
        db_column='batch_code',
        related_name='learners',
    )
    enrolled_type = models.CharField(max_length=100, null=True, blank=True)
    enrolled_on   = models.DateTimeField(null=True, blank=True)
    created_at    = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'learners'
        constraints = [
            models.UniqueConstraint(
                fields=['email', 'batch'],
                name='uq_learner_email_batch',
            )
        ]

    def __str__(self):
        return f'<Learner {self.email!r} → {self.batch_id!r}>'


class Cancellation(models.Model):
    batch          = models.ForeignKey(
        Batch,
        on_delete=models.CASCADE,
        db_column='batch_code',
        related_name='cancellations',
    )
    cancelled_date = models.DateField()
    reason         = models.TextField(null=True, blank=True)
    created_at     = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'cancellations'
        constraints = [
            models.UniqueConstraint(
                fields=['batch', 'cancelled_date'],
                name='uq_cancellation_batch_date',
            )
        ]
        indexes = [
            models.Index(
                fields=['batch', 'cancelled_date'],
                name='ix_cancellations_batch_date',
            )
        ]

    def __str__(self):
        return f'<Cancellation {self.batch_id!r} on {self.cancelled_date}>'


class Postponement(models.Model):
    batch         = models.ForeignKey(
        Batch,
        on_delete=models.CASCADE,
        db_column='batch_code',
        related_name='postponements',
    )
    original_date = models.DateField()
    new_date      = models.DateField()
    new_time      = models.CharField(max_length=5)
    reason        = models.TextField(null=True, blank=True)
    created_at    = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'postponements'
        constraints = [
            models.UniqueConstraint(
                fields=['batch', 'original_date'],
                name='uq_postponement_batch_date',
            )
        ]
        indexes = [
            models.Index(
                fields=['batch', 'original_date'],
                name='ix_postponements_batch_date',
            )
        ]

    def __str__(self):
        return f'<Postponement {self.batch_id!r} from {self.original_date} → {self.new_date} {self.new_time}>'


class EmailLog(models.Model):
    batch         = models.ForeignKey(
        Batch,
        on_delete=models.CASCADE,
        db_column='batch_code',
        related_name='email_logs',
    )
    learner_email = models.CharField(max_length=300)
    class_date    = models.DateField()
    status        = models.CharField(max_length=20, default='queued')
    attempt_count = models.IntegerField(default=0)
    error_message = models.TextField(null=True, blank=True)
    sent_at       = models.DateTimeField(null=True, blank=True)
    created_at    = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = 'email_logs'
        indexes = [
            models.Index(
                fields=['batch', 'class_date', 'learner_email'],
                name='ix_email_logs_batch_date_email',
            )
        ]

    def __str__(self):
        return f'<EmailLog {self.learner_email!r} | {self.batch_id!r} | {self.class_date} | {self.status!r}>'
