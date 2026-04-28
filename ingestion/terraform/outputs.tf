output "dynamodb_table_name" {
  description = "DynamoDB table name — set this in api/.chalice/config.json"
  value       = aws_dynamodb_table.btc_prices.name
}

output "s3_plots_bucket" {
  description = "S3 bucket for plot images — set this in api/.chalice/config.json"
  value       = aws_s3_bucket.plots.id
}

output "s3_plots_bucket_region" {
  description = "AWS region of the plots S3 bucket"
  value       = data.aws_region.current.name
}

output "lambda_function_name" {
  description = "Name of the ingest Lambda function"
  value       = aws_lambda_function.ingest.function_name
}

output "lambda_function_arn" {
  description = "ARN of the ingest Lambda function"
  value       = aws_lambda_function.ingest.arn
}

output "plot_url" {
  description = "Public URL for the latest BTC price chart (updated every 15 minutes)"
  value       = "https://${aws_s3_bucket.plots.id}.s3.${data.aws_region.current.name}.amazonaws.com/latest.png"
}

output "eventbridge_rule_name" {
  description = "EventBridge rule that triggers the ingest Lambda"
  value       = aws_cloudwatch_event_rule.ingest_schedule.name
}
