"""
Bedrock のユーザー別利用料金を CUR 2.0 (Athena) から集計し、
Teams の Workflows Webhook にグラフ付き Adaptive Card で投稿する。
"""
import datetime
import json
import os
import time
import urllib.request
from collections import defaultdict

import boto3

athena = boto3.client("athena")
JST = datetime.timezone(datetime.timedelta(hours=9))

DB = os.environ["ATHENA_DATABASE"]
TABLE = os.environ.get("ATHENA_TABLE", "data")
WORKGROUP = os.environ.get("ATHENA_WORKGROUP", "primary")
OUTPUT = os.environ.get("ATHENA_OUTPUT_LOCATION", "")
WEBHOOK_PARAM = os.environ["TEAMS_WEBHOOK_PARAM"]  # SSM パラメータ名（SecureString）
TOP_N = int(os.environ.get("TOP_N_USERS", "8"))


def webhook_url():
    ssm = boto3.client("ssm")
    return ssm.get_parameter(Name=WEBHOOK_PARAM, WithDecryption=True)["Parameter"]["Value"]


def run_query(sql):
    params = {
        "QueryString": sql,
        "WorkGroup": WORKGROUP,
        "QueryExecutionContext": {"Database": DB},
    }
    if OUTPUT:
        params["ResultConfiguration"] = {"OutputLocation": OUTPUT}
    qid = athena.start_query_execution(**params)["QueryExecutionId"]

    while True:
        status = athena.get_query_execution(QueryExecutionId=qid)["QueryExecution"]["Status"]
        if status["State"] in ("SUCCEEDED", "FAILED", "CANCELLED"):
            break
        time.sleep(1)
    if status["State"] != "SUCCEEDED":
        raise RuntimeError(f"Athena query {status['State']}: {status.get('StateChangeReason')}")

    rows = []
    for page in athena.get_paginator("get_query_results").paginate(QueryExecutionId=qid):
        for r in page["ResultSet"]["Rows"]:
            rows.append([c.get("VarCharValue") for c in r["Data"]])
    return rows[1:]  # 先頭はヘッダー行


def build_query(period):
    return f"""
SELECT CAST(date(line_item_usage_start_date) AS varchar) AS day,
       regexp_extract(line_item_iam_principal, '[^/]+$') AS user_name,
       SUM(line_item_unblended_cost) AS cost
FROM "{DB}"."{TABLE}"
WHERE billing_period = '{period}'
  AND line_item_iam_principal <> ''
GROUP BY 1, 2
ORDER BY 1, 2
"""


def aggregate(rows):
    daily = defaultdict(lambda: defaultdict(float))  # user -> day -> cost
    days = set()
    for day, user, cost in rows:
        daily[user or "(unknown)"][day] += float(cost or 0)
        days.add(day)
    totals = {u: sum(d.values()) for u, d in daily.items()}
    ranking = sorted(totals.items(), key=lambda kv: kv[1], reverse=True)
    return sorted(days), daily, ranking


def md(day):
    # "2026-10-05" -> "10/05"
    return day[5:].replace("-", "/")


def build_card(period, days, daily, ranking):
    total = sum(v for _, v in ranking)
    last_day = md(days[-1]) if days else "-"

    body = [
        {"type": "TextBlock", "text": f"Bedrock 利用料金レポート（{period}）",
         "weight": "Bolder", "size": "Large", "wrap": True},
        {"type": "TextBlock", "text": f"CUR 2.0 より集計／{last_day} 分まで（直近1〜2日は未確定）",
         "isSubtle": True, "size": "Small", "spacing": "None", "wrap": True},
        {"type": "FactSet", "facts": [
            {"title": "月累計", "value": f"${total:,.2f}"},
            {"title": "利用ユーザー数", "value": f"{len(ranking)} 人"},
        ]},
    ]

    if not ranking:
        body.append({"type": "TextBlock", "text": "この月の利用データはまだありません。", "wrap": True})
        return wrap(body)

    top = ranking[:TOP_N]
    others = [u for u, _ in ranking[TOP_N:]]

    # ユーザー別 月累計（横棒）
    body.append({
        "type": "Chart.HorizontalBar",
        "title": "ユーザー別 月累計（USD）",
        "data": [{"x": u, "y": round(v, 2)} for u, v in top],
        "fallback": "drop",
    })

    # 日別推移（折れ線）
    series = [
        {"legend": u, "values": [{"x": md(d), "y": round(daily[u].get(d, 0.0), 2)} for d in days]}
        for u, _ in top
    ]
    if others:
        series.append({"legend": "その他", "values": [
            {"x": md(d), "y": round(sum(daily[u].get(d, 0.0) for u in others), 2)} for d in days
        ]})
    body.append({
        "type": "Chart.Line",
        "title": "日別推移（USD）",
        "xAxisTitle": "日付",
        "yAxisTitle": "USD",
        "data": series,
        "fallback": "drop",
        "spacing": "Large",
    })

    # 数値の内訳（グラフが表示できない環境でもここは必ず見える）
    body.append({"type": "TextBlock", "text": "ユーザー別内訳", "weight": "Bolder",
                 "spacing": "Large", "separator": True})
    body.append({"type": "FactSet", "facts": [
        {"title": u, "value": f"${v:,.2f}"} for u, v in ranking
    ]})
    return wrap(body)


def wrap(body):
    return {
        "type": "message",
        "attachments": [{
            "contentType": "application/vnd.microsoft.card.adaptive",
            "contentUrl": None,
            "content": {
                "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                "type": "AdaptiveCard",
                "version": "1.5",
                "msteams": {"width": "Full"},
                "body": body,
            },
        }],
    }


def post(payload):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(webhook_url(), data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as res:
        print(f"Teams webhook status: {res.status}")


def lambda_handler(event, context):
    # 前日（JST）が属する月を集計対象にする。月初1日は前月分の最終レポートになる。
    # event に {"period": "2026-09"} を渡すと任意の月で実行できる。
    target = datetime.datetime.now(JST).date() - datetime.timedelta(days=1)
    period = (event or {}).get("period") or target.strftime("%Y-%m")

    rows = run_query(build_query(period))
    days, daily, ranking = aggregate(rows)
    post(build_card(period, days, daily, ranking))

    return {"period": period, "users": len(ranking),
            "total_usd": round(sum(v for _, v in ranking), 2)}
