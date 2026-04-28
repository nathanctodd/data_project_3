# ---------------------------------------------------------------------------
# S3 bucket for publicly-readable plot images
# ---------------------------------------------------------------------------
resource "aws_s3_bucket" "plots" {
  bucket = "${var.project_name}-plots-${data.aws_caller_identity.current.account_id}"

  tags = {
    Project = var.project_name
    Purpose = "Bitcoin price chart images - public read"
  }
}

# Disable Block Public Access so the bucket policy below can take effect
resource "aws_s3_bucket_public_access_block" "plots" {
  bucket = aws_s3_bucket.plots.id

  block_public_acls       = false
  block_public_policy     = false
  ignore_public_acls      = false
  restrict_public_buckets = false
}

# Allow anyone to GET objects — required so the Discord bot can display the chart
resource "aws_s3_bucket_policy" "plots_public_read" {
  bucket     = aws_s3_bucket.plots.id
  depends_on = [aws_s3_bucket_public_access_block.plots]

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "PublicReadGetObject"
        Effect    = "Allow"
        Principal = "*"
        Action    = "s3:GetObject"
        Resource  = "${aws_s3_bucket.plots.arn}/*"
      }
    ]
  })
}

resource "aws_s3_bucket_cors_configuration" "plots" {
  bucket = aws_s3_bucket.plots.id

  cors_rule {
    allowed_headers = ["*"]
    allowed_methods = ["GET", "HEAD"]
    allowed_origins = ["*"]
    max_age_seconds = 3000
  }
}

# ---------------------------------------------------------------------------
# S3 bucket for Lambda deployment artifacts (avoids the 50 MB direct-upload limit)
# ---------------------------------------------------------------------------
resource "aws_s3_bucket" "lambda_artifacts" {
  bucket = "${var.project_name}-lambda-artifacts-${data.aws_caller_identity.current.account_id}"

  tags = {
    Project = var.project_name
    Purpose = "Lambda function deployment zips"
  }
}

resource "aws_s3_bucket_versioning" "lambda_artifacts" {
  bucket = aws_s3_bucket.lambda_artifacts.id

  versioning_configuration {
    status = "Enabled"
  }
}
