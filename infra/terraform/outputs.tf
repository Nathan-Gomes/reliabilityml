output "api_url" {
  value = google_cloud_run_v2_service.api.uri
}

output "artifacts_bucket" {
  value = google_storage_bucket.artifacts.name
}

output "telemetry_topic" {
  value = google_pubsub_topic.telemetry.id
}
