"""
core/views/batch_views.py
--------------------------
Batch API views + health endpoint — 1:1 migration from FastAPI batch_routes.py.

Status codes preserved:
  POST /api/batch/create/ → 201 on success
  PUT  /api/batch/<batch_code>/ → 200
  DELETE /api/batch/<batch_code>/ → 200
  GET /api/batches/ → 200
  GET /api/batch/<batch_code>/ → 200
  POST /api/batch/upload-learners/ → 200
  Duplicate batch_code or product_title → 409
  Not found → 404
  Invalid CSV → 422 (ValidationError → custom_exception_handler)
  Empty file → 400
  Wrong file type → 400
"""

import logging
from datetime import datetime

from django.conf import settings
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from core.models import Batch, Learner
from core.serializers import (
    BatchCreateSerializer,
    BatchDetailOutSerializer,
    BatchOutSerializer,
    BatchUpdateSerializer,
    CSVUploadResultSerializer,
    LearnerOutSerializer,
)
from core.services import csv_service, scheduler_service

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_batch_or_404(batch_code: str):
    try:
        return Batch.objects.get(batch_code=batch_code)
    except Batch.DoesNotExist:
        return None


def _batch_out_data(batch: Batch, learner_count: int) -> dict:
    return {
        'batch_code': batch.batch_code,
        'product_title': batch.product_title,
        'class_days': batch.class_days,
        'class_time': batch.class_time,
        'batch_start_date': batch.batch_start_date,
        'batch_end_date': batch.batch_end_date,
        'instructor_name': batch.instructor_name,
        'instructor_email': batch.instructor_email,
        'learner_count': learner_count,
        'created_at': batch.created_at,
        'updated_at': batch.updated_at,
    }


# ---------------------------------------------------------------------------
# Health check — GET /health/
# ---------------------------------------------------------------------------

@api_view(['GET'])
def health_view(request):
    from django.db import connection
    from core.services.scheduler_service import get_scheduler_status

    db_status = 'ok'
    try:
        connection.ensure_connection()
    except Exception as exc:
        db_status = f'error: {exc}'

    return Response({
        'status': 'ok' if db_status == 'ok' else 'degraded',
        'db': db_status,
        'scheduler': get_scheduler_status(),
        'timestamp': datetime.utcnow().isoformat() + 'Z',
    })


# ---------------------------------------------------------------------------
# Create batch — POST /api/batch/create/
# ---------------------------------------------------------------------------

@api_view(['POST'])
def create_batch(request):
    ser = BatchCreateSerializer(data=request.data)
    ser.is_valid(raise_exception=True)
    data = ser.validated_data

    # Check duplicate batch_code
    if Batch.objects.filter(batch_code=data['batch_code']).exists():
        return Response(
            {'detail': f"Batch '{data['batch_code']}' already exists."},
            status=status.HTTP_409_CONFLICT,
        )

    # Check duplicate product_title
    existing_title = Batch.objects.filter(product_title=data['product_title']).first()
    if existing_title:
        return Response(
            {'detail': f"A batch with product_title '{data['product_title']}' already exists "
                       f"(batch_code: '{existing_title.batch_code}')."},
            status=status.HTTP_409_CONFLICT,
        )

    batch = Batch.objects.create(
        batch_code=data['batch_code'],
        product_title=data['product_title'],
        class_days=data['class_days'],
        class_time=data['class_time'],
        batch_start_date=data['batch_start_date'],
        batch_end_date=data['batch_end_date'],
        instructor_name=data.get('instructor_name'),
        instructor_email=data.get('instructor_email'),
    )

    scheduler_service.schedule_batch_jobs(batch)

    learner_count = Learner.objects.filter(batch_id=batch.batch_code).count()
    out = BatchOutSerializer(_batch_out_data(batch, learner_count))
    return Response(out.data, status=status.HTTP_201_CREATED)


# ---------------------------------------------------------------------------
# Batch detail / update / delete — /api/batch/<batch_code>/
# ---------------------------------------------------------------------------

@api_view(['GET', 'PUT', 'DELETE'])
def batch_detail(request, batch_code):
    if request.method == 'GET':
        return _get_batch(request, batch_code)
    elif request.method == 'PUT':
        return _update_batch(request, batch_code)
    elif request.method == 'DELETE':
        return _delete_batch(request, batch_code)


def _get_batch(request, batch_code):
    batch = _get_batch_or_404(batch_code)
    if batch is None:
        return Response({'detail': f"Batch '{batch_code}' not found."}, status=status.HTTP_404_NOT_FOUND)
    learners = list(Learner.objects.filter(batch_id=batch_code))
    data = _batch_out_data(batch, len(learners))
    data['learners'] = [LearnerOutSerializer({
        'id': l.id,
        'learner_name': l.learner_name,
        'email': l.email,
        'batch_code': l.batch_id,
        'enrolled_type': l.enrolled_type,
        'enrolled_on': l.enrolled_on,
        'created_at': l.created_at,
    }).data for l in learners]
    out = BatchDetailOutSerializer(data)
    return Response(out.data)


def _update_batch(request, batch_code):
    batch = _get_batch_or_404(batch_code)
    if batch is None:
        return Response({'detail': f"Batch '{batch_code}' not found."}, status=status.HTTP_404_NOT_FOUND)

    ser = BatchUpdateSerializer(data=request.data, partial=True)
    ser.is_valid(raise_exception=True)
    data = ser.validated_data

    if data.get('class_days') is not None:
        batch.class_days = data['class_days']
    if data.get('class_time') is not None:
        batch.class_time = data['class_time']
    if data.get('batch_start_date') is not None:
        batch.batch_start_date = data['batch_start_date']
    if data.get('batch_end_date') is not None:
        batch.batch_end_date = data['batch_end_date']
    if data.get('instructor_name') is not None:
        batch.instructor_name = data['instructor_name']
    if data.get('instructor_email') is not None:
        batch.instructor_email = data['instructor_email']

    batch.save()

    scheduler_service.remove_batch_jobs(batch_code)
    scheduler_service.schedule_batch_jobs(batch)

    learner_count = Learner.objects.filter(batch_id=batch.batch_code).count()
    out = BatchOutSerializer(_batch_out_data(batch, learner_count))
    return Response(out.data)


def _delete_batch(request, batch_code):
    batch = _get_batch_or_404(batch_code)
    if batch is None:
        return Response({'detail': f"Batch '{batch_code}' not found."}, status=status.HTTP_404_NOT_FOUND)

    scheduler_service.remove_batch_jobs(batch_code)
    batch.delete()
    logger.info("Batch '%s' and all related data deleted.", batch_code)
    return Response({'detail': f"Batch '{batch_code}' has been permanently deleted."})


# ---------------------------------------------------------------------------
# List all batches — GET /api/batches/
# ---------------------------------------------------------------------------

@api_view(['GET'])
def list_batches(request):
    batches = Batch.objects.order_by('batch_code')
    result = []
    for b in batches:
        count = Learner.objects.filter(batch_id=b.batch_code).count()
        result.append(BatchOutSerializer(_batch_out_data(b, count)).data)
    return Response(result)


# ---------------------------------------------------------------------------
# Upload CSV — POST /api/batch/upload-learners/
# ---------------------------------------------------------------------------

@api_view(['POST'])
def upload_learners(request):
    uploaded_file = request.FILES.get('file')

    if not uploaded_file:
        return Response({'detail': 'No file uploaded.'}, status=status.HTTP_400_BAD_REQUEST)

    filename = uploaded_file.name or ''
    if not filename.lower().endswith('.csv'):
        return Response({'detail': 'Only .csv files are accepted.'}, status=status.HTTP_400_BAD_REQUEST)

    content = uploaded_file.read()
    if not content:
        return Response({'detail': 'Uploaded file is empty.'}, status=status.HTTP_400_BAD_REQUEST)

    try:
        result = csv_service.process_learner_csv(content)
    except ValueError as exc:
        from rest_framework.exceptions import ValidationError
        raise ValidationError(str(exc))

    out = CSVUploadResultSerializer(result)
    return Response(out.data)
