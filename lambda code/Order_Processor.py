"""
LogiHaul - Lambda 1: Order Processor

Trigger: API Gateway HTTP API, POST /orders
Responsibility: validate incoming order request, write it to DynamoDB with
status PENDING, then drop a message on SQS so Lambda 2 can send the
confirmation notification asynchronously.

This function does NOT send notifications itself. That separation is what
lets order intake keep absorbing traffic during a 50x spike even if the
notification side (Lambda 2 / SNS) is temporarily slower to catch up -
SQS holds the backlog in between.
"""

import json
import os
import uuid
from datetime import datetime, timezone

import boto3

dynamodb = boto3.resource("dynamodb")
sqs = boto3.client("sqs")

TABLE_NAME = os.environ["ORDERS_TABLE_NAME"]
QUEUE_URL = os.environ["ORDER_QUEUE_URL"]

orders_table = dynamodb.Table(TABLE_NAME)

REQUIRED_FIELDS = ["shipperId", "pickupAddress", "dropoffAddress"]


def lambda_handler(event, context):
    try:
        body = _parse_body(event)
    except (ValueError, TypeError) as exc:
        return _response(400, {"error": f"Invalid request body: {exc}"})

    missing = [field for field in REQUIRED_FIELDS if not body.get(field)]
    if missing:
        return _response(
            400, {"error": f"Missing required field(s): {', '.join(missing)}"}
        )

    order_id = f"ORD-{datetime.now(timezone.utc).strftime('%Y%m%d')}-{uuid.uuid4().hex[:8].upper()}"
    now_iso = datetime.now(timezone.utc).isoformat()

    order_item = {
        "orderId": order_id,
        "shipperId": body["shipperId"],
        "pickupAddress": body["pickupAddress"],
        "dropoffAddress": body["dropoffAddress"],
        "status": "PENDING",
        "driverId": None,
        "createdAt": now_iso,
        "updatedAt": now_iso,
    }

    try:
        orders_table.put_item(Item=order_item)
    except Exception as exc:
        print(f"ERROR writing order {order_id} to DynamoDB: {exc}")
        return _response(500, {"error": "Could not save order. Please retry."})

    try:
        sqs.send_message(
            QueueUrl=QUEUE_URL,
            MessageBody=json.dumps(
                {
                    "orderId": order_id,
                    "shipperId": body["shipperId"],
                    "pickupAddress": body["pickupAddress"],
                    "dropoffAddress": body["dropoffAddress"],
                    "createdAt": now_iso,
                }
            ),
        )
    except Exception as exc:
        # The order is already saved at this point - intake succeeded.
        # We log this loudly but still return success to the shipper,
        # since the order itself was not lost. Worth a CloudWatch alarm.
        print(f"ERROR queuing notification for order {order_id}: {exc}")

    return _response(201, {"orderId": order_id, "status": "PENDING"})


def _parse_body(event):
    raw_body = event.get("body")
    if raw_body is None:
        raise ValueError("Request body is required")
    if event.get("isBase64Encoded"):
        import base64

        raw_body = base64.b64decode(raw_body).decode("utf-8")
    return json.loads(raw_body)


def _response(status_code, payload):
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(payload),
    }
