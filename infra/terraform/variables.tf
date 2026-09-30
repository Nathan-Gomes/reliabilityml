variable "project_id" {
  description = "GCP project to deploy into."
  type        = string
}

variable "region" {
  description = "Region for Cloud Run, Pub/Sub, BigQuery and storage."
  type        = string
  default     = "us-central1"
}

variable "billing_account" {
  description = "Billing account ID, used only for the budget alert."
  type        = string
}

variable "budget_usd" {
  description = "Monthly budget that triggers alerts at 50%, 90% and 100%."
  type        = number
  default     = 10
}

variable "image" {
  description = "Container image for the API (built by the deploy workflow)."
  type        = string
}

variable "alert_email" {
  description = "Where budget notifications go."
  type        = string
}
