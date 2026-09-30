"""Optional artifact sync with Google Cloud Storage (used by the Cloud Run deployment).

When RELIABILITYML_ARTIFACTS_BUCKET is set, the pipeline uploads artifacts/ after a build and the API
downloads them at start-up, so a retrain promoted by the scheduled job reaches the API without a new
image. Without the variable (local, Docker, Render) nothing happens.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

log = logging.getLogger(__name__)
PREFIX = "artifacts/"


def _bucket():
    name = os.environ.get("RELIABILITYML_ARTIFACTS_BUCKET")
    if not name:
        return None
    try:
        from google.cloud import storage  # optional dependency: pip install '.[gcp]'
    except ImportError:
        log.warning("RELIABILITYML_ARTIFACTS_BUCKET is set but google-cloud-storage is not installed")
        return None
    return storage.Client().bucket(name)


def upload(root: Path) -> int:
    bucket = _bucket()
    if bucket is None:
        return 0
    count = 0
    for path in root.rglob("*"):
        if path.is_file() and "mlflow.db" not in path.name:
            bucket.blob(PREFIX + str(path.relative_to(root))).upload_from_filename(str(path))
            count += 1
    log.info("uploaded %d artifact files to gs://%s", count, bucket.name)
    return count


def download(root: Path) -> int:
    bucket = _bucket()
    if bucket is None:
        return 0
    count = 0
    for blob in bucket.list_blobs(prefix=PREFIX):
        target = root / blob.name[len(PREFIX) :]
        target.parent.mkdir(parents=True, exist_ok=True)
        blob.download_to_filename(str(target))
        count += 1
    log.info("downloaded %d artifact files from gs://%s", count, bucket.name)
    return count
