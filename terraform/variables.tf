variable "aws_region" {
  description = "AWS region for all resources"
  type        = string
  default     = "us-east-1"
}

variable "project_name" {
  description = "Project name prefix for all resources"
  type        = string
  default     = "btc-tracker"
}

variable "ingestion_schedule" {
  description = "EventBridge schedule expression for data ingestion"
  type        = string
  default     = "rate(15 minutes)"
}

variable "lambda_timeout" {
  description = "Ingest Lambda timeout in seconds (matplotlib plot generation needs ~30-60s)"
  type        = number
  default     = 90
}

variable "lambda_memory_size" {
  description = "Ingest Lambda memory in MB (matplotlib requires extra memory)"
  type        = number
  default     = 512
}
