terraform {
  required_version = ">= 1.6"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.30"
    }
  }
  # Remote state is recommended once more than one person applies:
  # backend "gcs" { bucket = "<project>-tfstate" prefix = "reliabilityml" }
}

provider "google" {
  project = var.project_id
  region  = var.region
}
