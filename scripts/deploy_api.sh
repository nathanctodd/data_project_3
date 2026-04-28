#!/usr/bin/env bash
# deploy_api.sh — Wire Terraform outputs into .chalice/config.json, then deploy.
#
# Usage: ./scripts/deploy_api.sh
#
# Prerequisites:
#   - Terraform has already been applied (terraform apply in ./terraform/)
#   - pip install chalice boto3  (or: pip install -r api/requirements.txt)
#   - AWS credentials configured (same profile used for Terraform)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
TERRAFORM_DIR="${REPO_ROOT}/terraform"
CHALICE_DIR="${REPO_ROOT}/api"
CONFIG_FILE="${CHALICE_DIR}/.chalice/config.json"

echo "==> Reading Terraform outputs..."
cd "${TERRAFORM_DIR}"

TABLE_NAME=$(terraform output -raw dynamodb_table_name 2>/dev/null)
S3_BUCKET=$(terraform output -raw s3_plots_bucket 2>/dev/null)
AWS_REGION=$(terraform output -raw s3_plots_bucket_region 2>/dev/null)

if [[ -z "${TABLE_NAME}" || -z "${S3_BUCKET}" ]]; then
  echo "ERROR: Could not read Terraform outputs. Run 'terraform apply' first." >&2
  exit 1
fi

echo "    TABLE_NAME = ${TABLE_NAME}"
echo "    S3_BUCKET  = ${S3_BUCKET}"
echo "    AWS_REGION = ${AWS_REGION}"

echo "==> Patching .chalice/config.json with live values..."
# Use Python (always available if Chalice is installed) for reliable JSON editing
python3 - "${CONFIG_FILE}" "${TABLE_NAME}" "${S3_BUCKET}" <<'PYEOF'
import sys, json

config_path, table_name, s3_bucket = sys.argv[1], sys.argv[2], sys.argv[3]
with open(config_path) as f:
    cfg = json.load(f)

env = cfg["stages"]["dev"]["environment_variables"]
env["DYNAMODB_TABLE_NAME"] = table_name
env["S3_BUCKET_NAME"] = s3_bucket

with open(config_path, "w") as f:
    json.dump(cfg, f, indent=2)
    f.write("\n")

print(f"    Config updated: DYNAMODB_TABLE_NAME={table_name}, S3_BUCKET_NAME={s3_bucket}")
PYEOF

echo "==> Deploying Chalice app..."
cd "${CHALICE_DIR}"
chalice deploy --stage dev

echo ""
echo "==> Deployment complete!"
echo "    Copy the REST API URL above and register with the Discord bot:"
echo "    /register <project-id> <your-username> <api-url>"
