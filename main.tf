terraform {
  required_version = ">= 1.5.0"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 5.0"
    }
    google-beta = {
      source  = "hashicorp/google-beta"
      version = ">= 5.0"
    }
    archive = {
      source  = "hashicorp/archive"
      version = ">= 2.4"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

provider "google-beta" {
  project = var.project_id
  region  = var.region
}

resource "google_project_service" "apis" {
  for_each           = toset(var.enable_apis)
  service            = each.value
  disable_on_destroy = false
}

resource "google_artifact_registry_repository" "functions" {
  location      = var.region
  repository_id = var.artifact_repo_name
  format        = "DOCKER"
  description   = "Container images for Cloud Functions Gen2"
  depends_on    = [google_project_service.apis]
}

resource "google_storage_bucket" "source" {
  name                        = var.source_bucket_name
  location                    = var.region
  uniform_bucket_level_access = true
  force_destroy               = var.source_bucket_force_destroy
  depends_on                  = [google_project_service.apis]
}

data "archive_file" "source" {
  type        = "zip"
  source_dir  = var.source_dir
  excludes    = var.source_excludes
  output_path = "${path.module}/function-source.zip"
}

resource "google_storage_bucket_object" "source" {
  bucket       = google_storage_bucket.source.name
  name         = "function-source.zip"
  source       = data.archive_file.source.output_path
  content_type = "application/zip"
}

resource "google_service_account" "function" {
  account_id   = var.function_service_account_id
  display_name = "Cloud Functions runtime"
}

resource "google_iam_workload_identity_pool" "github" {
  provider                  = google-beta
  location                  = "global"
  workload_identity_pool_id = var.github_wif_pool_id
  display_name              = "GitHub Actions"
  depends_on                = [google_project_service.apis]
}

resource "google_iam_workload_identity_pool_provider" "github" {
  provider                           = google-beta
  location                           = "global"
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = var.github_wif_provider_id
  display_name                       = "GitHub Actions Provider"
  attribute_mapping = {
    "google.subject"         = "assertion.sub"
    "attribute.repository"   = "assertion.repository"
    "attribute.repository_owner" = "assertion.repository_owner"
    "attribute.ref"          = "assertion.ref"
  }
  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
  attribute_condition = "assertion.repository == '${var.github_owner}/${var.github_repo}'"
}

resource "google_service_account" "github_actions" {
  account_id   = var.github_service_account_id
  display_name = "GitHub Actions deployer"
}

resource "google_service_account_iam_member" "github_actions_wif" {
  service_account_id = google_service_account.github_actions.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repository/${var.github_owner}/${var.github_repo}"
}

resource "google_project_iam_member" "github_actions_roles" {
  for_each = toset(var.github_actions_roles)
  project  = var.project_id
  role     = each.value
  member   = "serviceAccount:${google_service_account.github_actions.email}"
}

resource "google_cloudfunctions2_function" "bot" {
  provider = google-beta
  name     = var.function_name
  location = var.region

  build_config {
    runtime         = "custom"
    entry_point     = "main"
    docker_repository = google_artifact_registry_repository.functions.id
    source {
      storage_source {
        bucket = google_storage_bucket.source.name
        object = google_storage_bucket_object.source.name
      }
    }
  }

  service_config {
    max_instance_count             = var.max_instance_count
    min_instance_count             = var.min_instance_count
    available_memory               = var.memory
    timeout_seconds                = var.timeout_seconds
    all_traffic_on_latest_revision = true
    ingress_settings               = "ALLOW_ALL"
    service_account_email          = google_service_account.function.email
    environment_variables = merge(
      var.function_env,
      var.container_image_uri == "" ? {} : { CONTAINER_IMAGE_URI = var.container_image_uri }
    )
  }

  depends_on = [google_project_service.apis]
}

resource "google_cloudfunctions2_function_iam_member" "invoker" {
  provider       = google-beta
  project        = var.project_id
  location       = var.region
  cloud_function = google_cloudfunctions2_function.bot.name
  role           = "roles/cloudfunctions.invoker"
  member         = "allUsers"
}

resource "google_secret_manager_secret_iam_member" "function_sa_secret" {
  count     = var.service_account_secret_id == "" ? 0 : 1
  secret_id = var.service_account_secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.function.email}"
}

output "function_url" {
  value = google_cloudfunctions2_function.bot.service_config[0].uri
}

output "artifact_registry_repo" {
  value = google_artifact_registry_repository.functions.id
}

output "github_actions_service_account_email" {
  value = google_service_account.github_actions.email
}

output "workload_identity_provider" {
  value = google_iam_workload_identity_pool_provider.github.name
}
