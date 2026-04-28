# ---------------------------------------------------------------------------
# IAM role for the ingest Lambda
# ---------------------------------------------------------------------------
resource "aws_iam_role" "lambda_ingest" {
  name = "${var.project_name}-ingest-lambda-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "lambda.amazonaws.com"
        }
      }
    ]
  })

  tags = {
    Project = var.project_name
  }
}

# CloudWatch Logs (create log groups, write log events)
resource "aws_iam_role_policy_attachment" "lambda_basic_execution" {
  role       = aws_iam_role.lambda_ingest.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# DynamoDB writes + reads (for plot generation), S3 plot uploads
resource "aws_iam_role_policy" "lambda_ingest_data" {
  name = "${var.project_name}-ingest-data-policy"
  role = aws_iam_role.lambda_ingest.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "DynamoDBReadWrite"
        Effect = "Allow"
        Action = [
          "dynamodb:PutItem",
          "dynamodb:GetItem",
          "dynamodb:Query",
          "dynamodb:Scan",
        ]
        Resource = aws_dynamodb_table.btc_prices.arn
      },
      {
        Sid    = "S3PlotWrite"
        Effect = "Allow"
        Action = [
          "s3:PutObject",
          "s3:GetObject",
        ]
        Resource = "${aws_s3_bucket.plots.arn}/*"
      }
    ]
  })
}
