# ReliabilityML on GCP. Create the budget alert first (it is the first resource here), deploy only
# working milestones, and `terraform destroy` when not demoing. Cloud Run scales to zero when idle.

locals {
  name = "reliabilityml"
  services = [
    "run.googleapis.com", "pubsub.googleapis.com", "bigquery.googleapis.com", "storage.googleapis.com",
    "secretmanager.googleapis.com", "cloudscheduler.googleapis.com", "artifactregistry.googleapis.com",
    "billingbudgets.googleapis.com",
  ]
}

resource "google_project_service" "apis" {
  for_each           = toset(local.services)
  service            = each.value
  disable_on_destroy = false
}

# ---------------------------------------------------------------- cost control

resource "google_monitoring_notification_channel" "email" {
  display_name = "${local.name} budget"
  type         = "email"
  labels       = { email_address = var.alert_email }
}

resource "google_billing_budget" "monthly" {
  billing_account = var.billing_account
  display_name    = "${local.name} monthly budget"
  budget_filter { projects = ["projects/${var.project_id}"] }
  amount {
    specified_amount {
      currency_code = "USD"
      units         = tostring(var.budget_usd)
    }
  }
  dynamic "threshold_rules" {
    for_each = [0.5, 0.9, 1.0]
    content { threshold_percent = threshold_rules.value }
  }
  all_updates_rule {
    monitoring_notification_channels = [google_monitoring_notification_channel.email.id]
    disable_default_iam_recipients   = false
  }
  depends_on = [google_project_service.apis]
}

# ---------------------------------------------------------------- identity

resource "google_service_account" "api" {
  account_id   = "${local.name}-api"
  display_name = "ReliabilityML API"
}

resource "google_service_account" "pipeline" {
  account_id   = "${local.name}-pipeline"
  display_name = "ReliabilityML retraining pipeline"
}

# ---------------------------------------------------------------- storage

resource "google_storage_bucket" "artifacts" {
  name                        = "${var.project_id}-${local.name}-artifacts"
  location                    = var.region
  uniform_bucket_level_access = true
  force_destroy               = true
  versioning { enabled = true }
  lifecycle_rule {
    condition { num_newer_versions = 5 }
    action { type = "Delete" }
  }
}

resource "google_storage_bucket_iam_member" "pipeline_writes" {
  bucket = google_storage_bucket.artifacts.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.pipeline.email}"
}

resource "google_storage_bucket_iam_member" "api_reads" {
  bucket = google_storage_bucket.artifacts.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.api.email}"
}

resource "google_bigquery_dataset" "telemetry" {
  dataset_id                  = "reliabilityml"
  location                    = var.region
  default_table_expiration_ms = 60 * 24 * 3600 * 1000 # 60 days keeps the sandbox tidy
  delete_contents_on_destroy  = true
}

resource "google_bigquery_table" "metrics" {
  dataset_id          = google_bigquery_dataset.telemetry.dataset_id
  table_id            = "metrics_1m"
  deletion_protection = false
  time_partitioning {
    type  = "DAY"
    field = "ts"
  }
  clustering = ["service"]
  schema = jsonencode([
    { name = "ts", type = "TIMESTAMP", mode = "REQUIRED" },
    { name = "service", type = "STRING", mode = "REQUIRED" },
    { name = "request_rate", type = "FLOAT" },
    { name = "latency_p50", type = "FLOAT" },
    { name = "latency_p95", type = "FLOAT" },
    { name = "latency_p99", type = "FLOAT" },
    { name = "error_rate", type = "FLOAT" },
    { name = "cpu_utilization", type = "FLOAT" },
    { name = "memory_utilization", type = "FLOAT" },
    { name = "db_connections_in_use", type = "FLOAT" },
  ])
}

resource "google_bigquery_table" "incidents" {
  dataset_id          = google_bigquery_dataset.telemetry.dataset_id
  table_id            = "incident_windows"
  deletion_protection = false
  schema = jsonencode([
    { name = "incident_id", type = "STRING", mode = "REQUIRED" },
    { name = "fired_at", type = "TIMESTAMP" },
    { name = "predicted", type = "STRING" },
    { name = "confidence", type = "FLOAT" },
    { name = "features", type = "JSON" },
    { name = "label", type = "STRING", description = "Filled in after the postmortem" },
  ])
}

resource "google_bigquery_dataset_iam_member" "pipeline_bq" {
  dataset_id = google_bigquery_dataset.telemetry.dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = "serviceAccount:${google_service_account.pipeline.email}"
}

# ---------------------------------------------------------------- ingestion

resource "google_pubsub_topic" "telemetry" {
  name                       = "${local.name}-telemetry"
  message_retention_duration = "86400s"
}

resource "google_pubsub_topic" "dead_letter" {
  name = "${local.name}-telemetry-dlq"
}

# BigQuery subscription: telemetry lands in the metrics table with no consumer code to run.
resource "google_pubsub_subscription" "to_bigquery" {
  name  = "${local.name}-telemetry-bq"
  topic = google_pubsub_topic.telemetry.id
  bigquery_config {
    table            = "${var.project_id}.${google_bigquery_dataset.telemetry.dataset_id}.${google_bigquery_table.metrics.table_id}"
    use_table_schema = true
  }
  dead_letter_policy {
    dead_letter_topic     = google_pubsub_topic.dead_letter.id
    max_delivery_attempts = 5
  }
}

# ---------------------------------------------------------------- secrets

resource "google_secret_manager_secret" "anthropic" {
  secret_id = "${local.name}-anthropic-api-key"
  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_iam_member" "api_reads_secret" {
  secret_id = google_secret_manager_secret.anthropic.id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.api.email}"
}

# ---------------------------------------------------------------- compute

resource "google_cloud_run_v2_service" "api" {
  name     = "${local.name}-api"
  location = var.region
  ingress  = "INGRESS_TRAFFIC_ALL"
  template {
    service_account = google_service_account.api.email
    scaling {
      min_instance_count = 0 # scale to zero when idle
      max_instance_count = 2
    }
    containers {
      image = var.image
      resources {
        limits   = { cpu = "1", memory = "1Gi" }
        cpu_idle = true
      }
      env {
        name  = "RELIABILITYML_ARTIFACTS_BUCKET"
        value = google_storage_bucket.artifacts.name
      }
      env {
        name = "RELIABILITYML_ANTHROPIC_API_KEY"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.anthropic.secret_id
            version = "latest"
          }
        }
      }
      startup_probe {
        http_get { path = "/health" }
      }
    }
  }
  depends_on = [google_project_service.apis]
}

resource "google_cloud_run_v2_service_iam_member" "public" {
  name     = google_cloud_run_v2_service.api.name
  location = var.region
  role     = "roles/run.invoker"
  member   = "allUsers"
}

# Daily drift check and gated retraining, as a Cloud Run job triggered by Cloud Scheduler.
resource "google_cloud_run_v2_job" "drift" {
  name     = "${local.name}-drift"
  location = var.region
  template {
    template {
      service_account = google_service_account.pipeline.email
      max_retries     = 1
      timeout         = "1800s"
      containers {
        image   = var.image
        command = ["python", "-m", "reliabilityml.pipelines.flows"]
        resources {
          limits = { cpu = "2", memory = "2Gi" }
        }
        env {
          name  = "RELIABILITYML_ARTIFACTS_BUCKET"
          value = google_storage_bucket.artifacts.name
        }
      }
    }
  }
  depends_on = [google_project_service.apis]
}

resource "google_project_iam_member" "scheduler_runs_jobs" {
  project = var.project_id
  role    = "roles/run.invoker"
  member  = "serviceAccount:${google_service_account.pipeline.email}"
}

resource "google_cloud_scheduler_job" "daily_drift" {
  name      = "${local.name}-daily-drift"
  schedule  = "0 6 * * *"
  time_zone = "Etc/UTC"
  http_target {
    http_method = "POST"
    uri         = "https://${var.region}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${var.project_id}/jobs/${google_cloud_run_v2_job.drift.name}:run"
    oauth_token {
      service_account_email = google_service_account.pipeline.email
    }
  }
}
