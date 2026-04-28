resource "aws_dynamodb_table" "btc_prices" {
  name         = "${var.project_name}-prices"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "coin"
  range_key    = "timestamp"

  attribute {
    name = "coin"
    type = "S"
  }

  attribute {
    name = "timestamp"
    type = "N"
  }

  # Auto-expire records after 30 days to keep table size manageable
  ttl {
    attribute_name = "ttl"
    enabled        = true
  }

  tags = {
    Project = var.project_name
    Purpose = "Bitcoin price time-series data"
  }
}
