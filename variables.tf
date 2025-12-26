variable "project_id" {
  type        = string
  description = "GCP project ID"
}

variable "region" {
  type        = string
  description = "GCP region for Cloud Functions and Artifact Registry"
  default     = "asia-northeast1"
}

variable "function_name" {
  type        = string
  description = "Cloud Functions Gen2 name"
  default     = "line-gemini-bot"
}

variable "artifact_repo_name" {
  type        = string
  description = "Artifact Registry repository name"
  default     = "functions"
}

variable "source_bucket_name" {
  type        = string
  description = "GCS bucket for function source archive"
}

variable "source_bucket_force_destroy" {
  type        = bool
  description = "Whether to allow bucket force destroy"
  default     = false
}

variable "source_dir" {
  type        = string
  description = "Directory to archive and deploy as source"
  default     = "."
}

variable "source_excludes" {
  type        = list(string)
  description = "Files or globs to exclude from the source archive"
  default = [
    ".git/**",
    ".github/**",
    ".terraform/**",
    "function-source.zip",
    "main.tf",
    "variables.tf",
    "terraform.tfstate",
    "terraform.tfstate.backup",
  ]
}

variable "memory" {
  type        = string
  description = "Memory allocation for Cloud Functions Gen2"
  default     = "512M"
}

variable "timeout_seconds" {
  type        = number
  description = "Function timeout in seconds"
  default     = 60
}

variable "min_instance_count" {
  type        = number
  description = "Minimum instances"
  default     = 0
}

variable "max_instance_count" {
  type        = number
  description = "Maximum instances"
  default     = 3
}

variable "function_env" {
  type        = map(string)
  description = "Runtime environment variables for the function"
  default     = {}
  sensitive   = true
}

variable "line_channel_access_token" {
  type        = string
  description = "LINE channel access token"
  default     = ""
  sensitive   = true
}

variable "line_channel_secret" {
  type        = string
  description = "LINE channel secret"
  default     = ""
  sensitive   = true
}

variable "gemini_api_key" {
  type        = string
  description = "Gemini API key"
  default     = ""
  sensitive   = true
}

variable "calendar_id" {
  type        = string
  description = "Google Calendar ID"
  default     = ""
  sensitive   = true
}

variable "spreadsheet_id" {
  type        = string
  description = "Google Spreadsheet ID"
  default     = ""
  sensitive   = true
}

variable "google_service_account_file" {
  type        = string
  description = "Path to service account JSON inside the container"
  default     = ""
  sensitive   = true
}

variable "google_service_account_secret" {
  type        = string
  description = "Secret Manager resource name for service account JSON"
  default     = ""
  sensitive   = true
}

variable "timezone" {
  type        = string
  description = "IANA timezone for scheduling (optional)"
  default     = ""
}

variable "work_start" {
  type        = string
  description = "Workday start time (HH:MM)"
  default     = ""
}

variable "work_end" {
  type        = string
  description = "Workday end time (HH:MM)"
  default     = ""
}

variable "default_duration_minutes" {
  type        = string
  description = "Default booking duration in minutes"
  default     = ""
}

variable "container_image_uri" {
  type        = string
  description = "Container image URI built by CI (optional)"
  default     = ""
}

variable "function_service_account_id" {
  type        = string
  description = "Service account ID for the Cloud Function runtime"
  default     = "line-bot-runtime"
}

variable "service_account_secret_id" {
  type        = string
  description = "Secret Manager secret ID for service account JSON (optional)"
  default     = ""
}

variable "github_owner" {
  type        = string
  description = "GitHub organization or user"
}

variable "github_repo" {
  type        = string
  description = "GitHub repository name"
}

variable "github_wif_pool_id" {
  type        = string
  description = "Workload Identity Pool ID"
  default     = "github-actions-pool"
}

variable "github_wif_provider_id" {
  type        = string
  description = "Workload Identity Pool Provider ID"
  default     = "github-actions-provider"
}

variable "github_service_account_id" {
  type        = string
  description = "Service account ID for GitHub Actions"
  default     = "github-actions-deployer"
}

variable "github_actions_roles" {
  type        = list(string)
  description = "Project roles for the GitHub Actions service account"
  default = [
    "roles/artifactregistry.writer",
    "roles/cloudfunctions.developer",
    "roles/run.admin",
    "roles/iam.serviceAccountUser",
    "roles/iam.serviceAccountAdmin",
    "roles/iam.workloadIdentityPoolAdmin",
    "roles/storage.admin",
    "roles/serviceusage.serviceUsageAdmin",
  ]
}

variable "enable_apis" {
  type        = list(string)
  description = "APIs to enable"
  default = [
    "artifactregistry.googleapis.com",
    "cloudbuild.googleapis.com",
    "cloudfunctions.googleapis.com",
    "iam.googleapis.com",
    "iamcredentials.googleapis.com",
    "run.googleapis.com",
    "secretmanager.googleapis.com",
    "serviceusage.googleapis.com",
    "sheets.googleapis.com",
    "calendar.googleapis.com",
    "storage.googleapis.com",
    "sts.googleapis.com",
  ]
}
