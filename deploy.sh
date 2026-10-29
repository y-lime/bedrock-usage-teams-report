#!/usr/bin/env bash
# CUR 2.0 のエクスポート作成から Teams 通知までを一括デプロイする。
# CloudShell（東京リージョン）で実行する想定。何度実行しても同じ状態になる。
#
#   export TEAMS_WEBHOOK_URL='https://...'   # 初回だけ必要（2回目以降は省略可）
#   ./deploy.sh
set -euo pipefail
cd "$(dirname "$0")"

REGION="${REGION:-ap-northeast-1}"
ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"

EXPORT_NAME="${EXPORT_NAME:-bedrock-usage}"
CUR_BUCKET="${CUR_BUCKET:-bedrock-usage-cur-${ACCOUNT_ID}}"
RESULTS_BUCKET="${RESULTS_BUCKET:-bedrock-usage-athena-results-${ACCOUNT_ID}}"
DATABASE="${DATABASE:-bedrock_cur}"
WEBHOOK_PARAM="/bedrock-usage/teams-webhook-url"

echo "== 1/4 Teams Webhook URL を SSM に保存"
if [[ -n "${TEAMS_WEBHOOK_URL:-}" ]]; then
  aws ssm put-parameter --region "$REGION" --name "$WEBHOOK_PARAM" \
    --type SecureString --value "$TEAMS_WEBHOOK_URL" --overwrite >/dev/null
  echo "   保存しました"
elif aws ssm get-parameter --region "$REGION" --name "$WEBHOOK_PARAM" >/dev/null 2>&1; then
  echo "   既存の値を使います"
else
  echo "   TEAMS_WEBHOOK_URL を設定してから実行してください" >&2
  exit 1
fi

echo "== 2/4 S3 バケットと Athena テーブル（${REGION}）"
aws cloudformation deploy --region "$REGION" \
  --stack-name bedrock-usage-cur-infra \
  --template-file cur/infra.yaml \
  --no-fail-on-empty-changeset \
  --parameter-overrides \
    CurBucketName="$CUR_BUCKET" \
    AthenaResultsBucketName="$RESULTS_BUCKET" \
    ExportName="$EXPORT_NAME" \
    DatabaseName="$DATABASE"

echo "== 3/4 CUR 2.0 データエクスポート（us-east-1）"
aws cloudformation deploy --region us-east-1 \
  --stack-name bedrock-usage-cur-export \
  --template-file cur/export.yaml \
  --no-fail-on-empty-changeset \
  --parameter-overrides \
    ExportName="$EXPORT_NAME" \
    CurBucketName="$CUR_BUCKET" \
    CurBucketRegion="$REGION"

echo "== 4/4 Teams 通知 Lambda（${REGION}）"
sam build
sam deploy --region "$REGION" \
  --stack-name bedrock-usage-teams-report \
  --resolve-s3 \
  --capabilities CAPABILITY_IAM \
  --no-confirm-changeset \
  --no-fail-on-empty-changeset \
  --parameter-overrides \
    TeamsWebhookParam="$WEBHOOK_PARAM" \
    AthenaDatabase="$DATABASE" \
    CurBucketName="$CUR_BUCKET" \
    AthenaResultsBucketName="$RESULTS_BUCKET"

echo
echo "完了。CUR の初回配信まで最大24時間かかります。配信後に次で動作確認できます："
echo "  aws lambda invoke --region $REGION --function-name bedrock-usage-teams-report \\"
echo "    --cli-binary-format raw-in-base64-out --payload '{}' out.json && cat out.json"
