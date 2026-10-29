# Bedrock 利用料金 Teams レポート

CUR 2.0（Athena）からユーザー別の Bedrock 利用料金を集計し、毎朝 Teams チャネルにグラフ付きで投稿します。

- ユーザー別 月累計（横棒グラフ）
- 日別推移（折れ線、上位 N 人＋その他）
- 全ユーザーの金額一覧

## 1. Teams 側：Webhook URL を発行する

1. 投稿したいチャネルの「…」→「ワークフロー」
2. テンプレート「Webhook 要求を受信したらチャネルに投稿する」を選択
3. チームとチャネルを確認して作成
4. 表示された URL をコピー（後で使います）

## 2. AWS 側：CloudShell からデプロイする

Athena を使っているリージョン（東京）で CloudShell を開き、このフォルダの zip をアップロードして実行します。

```bash
unzip bedrock-usage-teams-report.zip
cd bedrock-usage-teams-report
sam build
sam deploy --guided
```

`--guided` で聞かれるパラメータ：

| パラメータ | 入れる値 |
|---|---|
| Stack Name | `bedrock-usage-teams-report` |
| AWS Region | `ap-northeast-1` |
| TeamsWebhookUrl | 手順1でコピーした URL |
| AthenaDatabase | `athenadataexports_bedrock_usage`（既定のまま） |
| AthenaTable | `data`（既定のまま） |
| CurBucketName | CUR 2.0 の出力先バケット名 |
| AthenaResultsBucketName | Athena のクエリ結果用バケット名 |
| その他 | 既定のままで OK |

最後の確認はすべて `y` で進めて構いません。2回目以降は `sam build && sam deploy` だけで更新できます。

## 3. 動作確認

```bash
aws lambda invoke --function-name bedrock-usage-teams-report \
  --cli-binary-format raw-in-base64-out --payload '{}' out.json && cat out.json
```

Teams に投稿されれば完了です。以降は毎朝 9 時（JST）に自動で投稿されます。

過去の月を投稿したいときは `--payload '{"period":"2026-09"}'` のように月を指定します。

## 補足

- 集計対象は「前日が属する月」です。毎月 1 日の朝は前月分の最終レポートになります。
- CUR は 1 日数回の更新なので、直近 1〜2 日分は金額が確定していません。
- グラフを表示できない古い Teams クライアントでは、グラフは省略され、金額一覧だけが表示されます。
- 時刻を変えたいときは `ScheduleExpression`（例：`cron(30 8 ? * MON-FRI *)` で平日 8:30）を変更して再デプロイします。
