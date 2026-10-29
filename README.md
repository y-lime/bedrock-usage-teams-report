# Bedrock 利用料金 Teams レポート

CUR 2.0 のデータエクスポート作成から、毎朝 Teams へのグラフ付き投稿までを、スクリプト1本でデプロイします。

## 構成

| ファイル | リージョン | 作るもの |
|---|---|---|
| `cur/infra.yaml` | 東京 | CUR 出力先バケット（Data Exports 用ポリシー付き）、Athena 結果用バケット（30日で自動削除）、Glue データベースとテーブル |
| `cur/export.yaml` | us-east-1 | CUR 2.0 データエクスポート（日別・IAM プリンシパル付き） |
| `template.yaml` + `src/` | 東京 | 毎朝9時に集計して Teams に投稿する Lambda |
| `deploy.sh` | — | 上の3つを順番にデプロイ |

手作業版との違い：

- Athena テーブルは**パーティション射影**で定義しているので、Glue Crawler も Lambda も不要。新しい月のデータは自動で見えます。
- エクスポートは集計に使う列だけを出力するので、データ量が小さく済みます。
- Data Exports は us-east-1 のサービスなので、エクスポートだけ us-east-1 にデプロイします（データは東京のバケットに届きます）。

## 1. Teams 側：Webhook URL を発行する

1. 投稿したいチャネルの「…」→「ワークフロー」
2. テンプレート「Webhook 要求を受信したらチャネルに投稿する」を選択
3. チームとチャネルを確認して作成
4. 表示された URL をコピー

URL は SSM パラメータストア（SecureString）に保存され、Lambda の環境変数には残りません。

## 2. デプロイ（CloudShell・東京リージョン）

zip をアップロードして実行します。

```bash
unzip bedrock-usage-teams-report.zip
cd bedrock-usage-teams-report
export TEAMS_WEBHOOK_URL='https://...'   # 手順1の URL（シングルクォートで囲む）
./deploy.sh
```

2回目以降（コードや設定を変えたとき）は `./deploy.sh` だけで OK です。URL を変えたいときだけ `TEAMS_WEBHOOK_URL` を付けます。

バケット名などを変えたい場合は環境変数で上書きできます：`CUR_BUCKET` / `RESULTS_BUCKET` / `DATABASE` / `EXPORT_NAME` / `REGION`

## 3. 動作確認（CUR の初回配信後）

初回配信まで最大24時間かかります。S3 の `cur2/bedrock-usage/data/` にファイルができたら：

```bash
aws lambda invoke --function-name bedrock-usage-teams-report \
  --cli-binary-format raw-in-base64-out --payload '{}' out.json && cat out.json
```

Athena で直接見るときは、データベース `bedrock_cur`、テーブル `data` です。

```sql
SELECT regexp_extract(line_item_iam_principal, '[^/]+$') AS user_name,
       ROUND(SUM(line_item_unblended_cost), 2) AS cost_usd
FROM bedrock_cur.data
WHERE billing_period = '2026-10'
  AND line_item_iam_principal <> ''
GROUP BY 1
ORDER BY 2 DESC;
```

## 手作業で作った分の後片付け

新しい仕組みで投稿できることを確認したら、二重にならないよう手作業分を削除します。

1. Billing and Cost Management → データエクスポート → 手作業で作ったエクスポートを削除
2. CloudFormation → Athena 用に作ったスタック（crawler-cfn.yml から作ったもの）を削除
3. 不要なら手作業で作った CUR 用 S3 バケットを削除

## 補足

- 新しく作ったエクスポートには、作成した月の分から入ります。過去の月が必要な場合は、AWS サポートにバックフィルを依頼します。
- IAM プリンシパルのデータは 2026年4月8日以降の利用分だけが対象です。
- AWS Organizations を使っている場合、組織全体を見るにはこのスクリプトを管理アカウントで実行します。
- 集計対象は「前日が属する月」です。毎月1日の朝は前月分の最終レポートになります。
- CUR は1日数回の更新なので、直近1〜2日分は金額が確定していません。
- 投稿時刻は `template.yaml` の `ScheduleExpression`（例：`cron(30 8 ? * MON-FRI *)` で平日 8:30）で変更できます。
- 削除するとき：3つのスタック（`bedrock-usage-teams-report` / `bedrock-usage-cur-infra` / us-east-1 の `bedrock-usage-cur-export`）を削除します。CUR バケットはデータ保護のため残る設定なので、不要なら手動で削除してください。
