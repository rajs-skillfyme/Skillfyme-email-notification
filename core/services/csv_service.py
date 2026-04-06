"""
core/services/csv_service.py
------------------------------
Parses the Learner Enrollments CSV — 1:1 migration from FastAPI version.

Preserved exactly:
  - UTF-8-BOM tried first, latin-1 fallback
  - Required columns: {"Learner Details", "Product title"}
  - Optional columns: {"Enrolled Type", "Enrolled On"}
  - Per-row validation: empty email → skip, invalid email → skip,
    empty product title → skip, no matching batch → skip
  - Per-batch atomic write in transaction.atomic():
    delete → bulk_create → reschedule → commit
  - Rollback on exception, warning added, continue to next batch
  - email.lower() applied to stored email
  - Return dict with 5 keys matching FastAPI response shape exactly
"""

from __future__ import annotations

import csv
import io
import logging
from datetime import datetime
from typing import Dict, List, Optional

from django.db import transaction

from core.models import Batch, Learner
from core.utils.validators import derive_name_from_email, is_valid_email, parse_iso_datetime

logger = logging.getLogger(__name__)

_REQUIRED_COLUMNS = {'Learner Details', 'Product title'}
_OPTIONAL_COLUMNS = {'Enrolled Type', 'Enrolled On'}


def _build_product_title_map() -> Dict[str, Batch]:
    """Return dict mapping lowercased-stripped product_title → Batch."""
    batches = Batch.objects.all()
    return {b.product_title.strip().lower(): b for b in batches}


def process_learner_csv(file_bytes: bytes) -> dict:
    """
    Main entry point called by the upload view.

    Returns a dict with keys:
      total_rows, matched_rows, skipped_rows, warnings, learners_per_batch
    """
    # Decode bytes → text (try UTF-8 with BOM, fall back to latin-1)
    try:
        text = file_bytes.decode('utf-8-sig')
    except UnicodeDecodeError:
        text = file_bytes.decode('latin-1')

    reader = csv.DictReader(io.StringIO(text))
    fieldnames = set(reader.fieldnames or [])

    # Validate required columns before processing any row
    missing = _REQUIRED_COLUMNS - fieldnames
    if missing:
        raise ValueError(
            f"CSV is missing required column(s): {', '.join(sorted(missing))}. "
            f"Columns found: {', '.join(sorted(fieldnames))}"
        )

    product_map = _build_product_title_map()

    batch_learners: Dict[str, List[dict]] = {}
    warnings: List[str] = []
    total_rows = 0
    matched_rows = 0
    skipped_rows = 0

    for row_num, row in enumerate(reader, start=2):
        total_rows += 1

        email = (row.get('Learner Details') or '').strip()
        product_title_raw = (row.get('Product title') or '').strip()

        # --- Email validation ---
        if not email:
            warnings.append(f'Row {row_num}: Empty email — skipped.')
            skipped_rows += 1
            continue

        if not is_valid_email(email):
            warnings.append(f"Row {row_num}: Invalid email '{email}' — skipped.")
            skipped_rows += 1
            continue

        # --- Product title matching ---
        lookup_key = product_title_raw.lower().strip()
        if not lookup_key:
            warnings.append(f'Row {row_num}: Empty product title for {email} — skipped.')
            skipped_rows += 1
            continue

        matched_batch = product_map.get(lookup_key)
        if matched_batch is None:
            warnings.append(
                f"Row {row_num}: No batch found for product title '{product_title_raw}' "
                f"(email: {email}) — skipped."
            )
            skipped_rows += 1
            continue

        # --- Parse optional columns ---
        enrolled_type: Optional[str] = (row.get('Enrolled Type') or '').strip() or None
        enrolled_on_raw = (row.get('Enrolled On') or '').strip()
        enrolled_on: Optional[datetime] = parse_iso_datetime(enrolled_on_raw)

        bc = matched_batch.batch_code
        if bc not in batch_learners:
            batch_learners[bc] = []

        batch_learners[bc].append({
            'email': email.lower(),
            'learner_name': derive_name_from_email(email),
            'batch_id': bc,
            'enrolled_type': enrolled_type,
            'enrolled_on': enrolled_on,
        })
        matched_rows += 1

    # --- Write to DB in one transaction per batch ---
    learners_per_batch: Dict[str, int] = {}

    for batch_code, learner_dicts in batch_learners.items():
        try:
            with transaction.atomic():
                # Delete old learner records for this batch
                deleted_count, _ = Learner.objects.filter(batch_id=batch_code).delete()
                logger.info('Deleted %d old learners from batch %s.', deleted_count, batch_code)

                # Insert new records
                new_learners = [Learner(**ld) for ld in learner_dicts]
                Learner.objects.bulk_create(new_learners)

                # Reschedule APScheduler jobs (must happen within the transaction)
                try:
                    batch = Batch.objects.get(batch_code=batch_code)
                    from core.services import scheduler_service
                    scheduler_service.remove_batch_jobs(batch_code)
                    scheduler_service.schedule_batch_jobs(batch)
                except Batch.DoesNotExist:
                    pass

                # transaction commits automatically on context exit
            learners_per_batch[batch_code] = len(learner_dicts)
            logger.info('Imported %d learners into batch %s.', len(learner_dicts), batch_code)
        except Exception as exc:
            msg = f'DB error importing batch {batch_code}: {exc}'
            warnings.append(msg)
            logger.error(msg)

    return {
        'total_rows': total_rows,
        'matched_rows': matched_rows,
        'skipped_rows': skipped_rows,
        'warnings': warnings,
        'learners_per_batch': learners_per_batch,
    }
