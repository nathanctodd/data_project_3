# ---------------------------------------------------------------------------
# Build the Lambda deployment package
#
# Uses pip's --platform flag to pull manylinux wheels so the package runs
# on the Lambda (Amazon Linux 2) runtime even when built on macOS.
# Re-runs whenever requirements.txt or handler.py changes.
# ---------------------------------------------------------------------------
resource "null_resource" "build_ingest_lambda" {
  triggers = {
    requirements_hash = filemd5("${path.module}/../lambda/requirements.txt")
    handler_hash      = filemd5("${path.module}/../lambda/handler.py")
  }

  provisioner "local-exec" {
    command = <<-EOF
      set -e
      echo "==> Cleaning previous build..."
      rm -rf "${path.module}/../lambda/package"
      mkdir -p "${path.module}/../lambda/package"

      echo "==> Installing dependencies (manylinux wheels for Lambda runtime)..."
      pip install \
        --platform manylinux2014_x86_64 \
        --target "${path.module}/../lambda/package" \
        --implementation cp \
        --python-version 3.11 \
        --only-binary=:all: \
        -r "${path.module}/../lambda/requirements.txt"

      echo "==> Copying handler..."
      cp "${path.module}/../lambda/handler.py" \
         "${path.module}/../lambda/package/"
      echo "==> Build complete."
    EOF
  }
}

data "archive_file" "ingest_lambda_zip" {
  depends_on  = [null_resource.build_ingest_lambda]
  type        = "zip"
  source_dir  = "${path.module}/../lambda/package"
  output_path = "${path.module}/../lambda/lambda.zip"
}

# Upload zip to S3 to avoid the 50 MB direct-upload limit
resource "aws_s3_object" "ingest_lambda_zip" {
  bucket = aws_s3_bucket.lambda_artifacts.id
  key    = "ingest_lambda.zip"
  source = data.archive_file.ingest_lambda_zip.output_path
  etag   = data.archive_file.ingest_lambda_zip.output_md5
}

# ---------------------------------------------------------------------------
# Ingest Lambda function
# ---------------------------------------------------------------------------
resource "aws_lambda_function" "ingest" {
  s3_bucket        = aws_s3_bucket.lambda_artifacts.id
  s3_key           = aws_s3_object.ingest_lambda_zip.key
  source_code_hash = data.archive_file.ingest_lambda_zip.output_base64sha256

  function_name = "${var.project_name}-ingest"
  role          = aws_iam_role.lambda_ingest.arn
  handler       = "handler.lambda_handler"
  runtime       = "python3.11"
  timeout       = var.lambda_timeout
  memory_size   = var.lambda_memory_size

  environment {
    variables = {
      DYNAMODB_TABLE_NAME = aws_dynamodb_table.btc_prices.name
      S3_BUCKET_NAME      = aws_s3_bucket.plots.id
      S3_PLOT_KEY         = "latest.png"
      APP_AWS_REGION      = var.aws_region
    }
  }

  depends_on = [
    aws_iam_role_policy_attachment.lambda_basic_execution,
    aws_iam_role_policy.lambda_ingest_data,
    aws_s3_object.ingest_lambda_zip,
  ]

  tags = {
    Project = var.project_name
    Purpose = "Bitcoin price ingestion + plot generation"
  }
}

# CloudWatch log group with 14-day retention (avoids unbounded log storage)
resource "aws_cloudwatch_log_group" "ingest_lambda" {
  name              = "/aws/lambda/${aws_lambda_function.ingest.function_name}"
  retention_in_days = 14

  tags = {
    Project = var.project_name
  }
}

# ---------------------------------------------------------------------------
# EventBridge scheduled rule — fires every 15 minutes
# ---------------------------------------------------------------------------
resource "aws_cloudwatch_event_rule" "ingest_schedule" {
  name                = "${var.project_name}-ingest-schedule"
  description         = "Trigger Bitcoin price ingestion on a fixed cadence"
  schedule_expression = var.ingestion_schedule

  tags = {
    Project = var.project_name
  }
}

resource "aws_cloudwatch_event_target" "ingest_lambda" {
  rule      = aws_cloudwatch_event_rule.ingest_schedule.name
  target_id = "IngestLambdaTarget"
  arn       = aws_lambda_function.ingest.arn
}

resource "aws_lambda_permission" "allow_eventbridge" {
  statement_id  = "AllowEventBridgeInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.ingest.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.ingest_schedule.arn
}
