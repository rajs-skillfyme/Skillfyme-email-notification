"""
core/serializers.py
-------------------
DRF serializers replacing Pydantic schemas.
All field names, validation logic, and output shapes match the FastAPI originals.
"""

import re
from datetime import date
from typing import Optional

from rest_framework import serializers

from core.models import Batch, Learner, EmailLog


VALID_DAY_ALIASES = {
    'monday': 'Mon', 'tuesday': 'Tue', 'wednesday': 'Wed',
    'thursday': 'Thu', 'friday': 'Fri', 'saturday': 'Sat', 'sunday': 'Sun',
    'mon': 'Mon', 'tue': 'Tue', 'wed': 'Wed', 'thu': 'Thu',
    'fri': 'Fri', 'sat': 'Sat', 'sun': 'Sun',
}


def _validate_class_days(value: str) -> str:
    days = [d.strip() for d in value.split(',')]
    normalised = []
    for d in days:
        key = d.lower()
        if key not in VALID_DAY_ALIASES:
            raise serializers.ValidationError(
                f"Invalid day '{d}'. Must be one of: Mon, Tue, Wed, Thu, Fri, Sat, Sun"
            )
        normalised.append(VALID_DAY_ALIASES[key])
    return ','.join(normalised)


def _validate_time(value: str) -> str:
    v = value.strip()
    if not re.match(r'^\d{2}:\d{2}$', v):
        raise serializers.ValidationError(
            "class_time must be in HH:MM 24-hour format (e.g. '19:00')"
        )
    h, m = map(int, v.split(':'))
    if not (0 <= h <= 23 and 0 <= m <= 59):
        raise serializers.ValidationError('class_time has invalid hour or minute values')
    return v


class BatchCreateSerializer(serializers.Serializer):
    batch_code       = serializers.CharField()
    product_title    = serializers.CharField()
    class_days       = serializers.CharField()
    class_time       = serializers.CharField()
    batch_start_date = serializers.DateField()
    batch_end_date   = serializers.DateField()
    instructor_name  = serializers.CharField(required=False, allow_null=True, allow_blank=True, default=None)
    instructor_email = serializers.CharField(required=False, allow_null=True, allow_blank=True, default=None)

    def validate_batch_code(self, value):
        return value.strip()

    def validate_class_days(self, value):
        return _validate_class_days(value)

    def validate_class_time(self, value):
        return _validate_time(value)

    def validate(self, data):
        start = data.get('batch_start_date')
        end = data.get('batch_end_date')
        if start and end and end < start:
            raise serializers.ValidationError({
                'batch_end_date': 'batch_end_date must be on or after batch_start_date'
            })
        return data


class BatchUpdateSerializer(serializers.Serializer):
    class_days       = serializers.CharField(required=False, allow_null=True)
    class_time       = serializers.CharField(required=False, allow_null=True)
    batch_start_date = serializers.DateField(required=False, allow_null=True)
    batch_end_date   = serializers.DateField(required=False, allow_null=True)
    instructor_name  = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    instructor_email = serializers.CharField(required=False, allow_null=True, allow_blank=True)

    def validate_class_days(self, value):
        if value is None:
            return value
        return _validate_class_days(value)

    def validate_class_time(self, value):
        if value is None:
            return value
        return _validate_time(value)


class LearnerOutSerializer(serializers.Serializer):
    id            = serializers.IntegerField()
    learner_name  = serializers.CharField()
    email         = serializers.CharField()
    batch_code    = serializers.SerializerMethodField()
    enrolled_type = serializers.CharField(allow_null=True)
    enrolled_on   = serializers.DateTimeField(allow_null=True)
    created_at    = serializers.DateTimeField()

    def get_batch_code(self, obj):
        return obj.batch_id


class BatchOutSerializer(serializers.Serializer):
    batch_code       = serializers.CharField()
    product_title    = serializers.CharField()
    class_days       = serializers.CharField()
    class_time       = serializers.CharField()
    batch_start_date = serializers.DateField()
    batch_end_date   = serializers.DateField()
    instructor_name  = serializers.CharField(allow_null=True)
    instructor_email = serializers.CharField(allow_null=True)
    learner_count    = serializers.IntegerField(default=0)
    created_at       = serializers.DateTimeField()
    updated_at       = serializers.DateTimeField(allow_null=True)


class BatchDetailOutSerializer(BatchOutSerializer):
    learners = LearnerOutSerializer(many=True, default=[])


class CancelClassRequestSerializer(serializers.Serializer):
    date   = serializers.DateField()
    reason = serializers.CharField(required=False, allow_null=True, allow_blank=True, default=None)


class UndoCancelRequestSerializer(serializers.Serializer):
    date = serializers.DateField()


class CancellationOutSerializer(serializers.Serializer):
    id             = serializers.IntegerField()
    batch_code     = serializers.SerializerMethodField()
    cancelled_date = serializers.DateField()
    reason         = serializers.CharField(allow_null=True)
    created_at     = serializers.DateTimeField()

    def get_batch_code(self, obj):
        return obj.batch_id


class PostponeClassRequestSerializer(serializers.Serializer):
    original_date = serializers.DateField()
    new_date      = serializers.DateField()
    new_time      = serializers.CharField()
    reason        = serializers.CharField(required=False, allow_null=True, allow_blank=True, default=None)

    def validate_new_time(self, value):
        return _validate_time(value)


class PostponementOutSerializer(serializers.Serializer):
    id            = serializers.IntegerField()
    batch_code    = serializers.SerializerMethodField()
    original_date = serializers.DateField()
    new_date      = serializers.DateField()
    new_time      = serializers.CharField()
    reason        = serializers.CharField(allow_null=True)
    created_at    = serializers.DateTimeField()

    def get_batch_code(self, obj):
        return obj.batch_id


class EmailLogOutSerializer(serializers.Serializer):
    id            = serializers.IntegerField()
    batch_code    = serializers.SerializerMethodField()
    learner_email = serializers.CharField()
    class_date    = serializers.DateField()
    status        = serializers.CharField()
    attempt_count = serializers.IntegerField()
    error_message = serializers.CharField(allow_null=True)
    sent_at       = serializers.DateTimeField(allow_null=True)
    created_at    = serializers.DateTimeField()

    def get_batch_code(self, obj):
        return obj.batch_id


class CSVUploadResultSerializer(serializers.Serializer):
    total_rows        = serializers.IntegerField()
    matched_rows      = serializers.IntegerField()
    skipped_rows      = serializers.IntegerField()
    warnings          = serializers.ListField(child=serializers.CharField())
    learners_per_batch = serializers.DictField(child=serializers.IntegerField())
