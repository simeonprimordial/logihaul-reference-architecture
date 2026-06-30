import json
import boto3
import uuid
import random
from datetime import datetime, timezone

dynamodb = boto3.resource("dynamodb")
sqs = boto3.client("sqs")

TABLE_NAME = "LogiHaul-Orders"
QUEUE_URL = ""

AVAILABLE_DRIVERS = ["DRV-1042", "DRV-1043", "DRV-1044"]


def lambda_handler(event, context):
    # Parse body
    try:
        body = json.loads(event.get("body", "{}"))
    except Exception:
        return {
            "statusCode": 400,
            "body": json.dumps({"error": "Invalid JSON body"})
        }

    # Extract fields
    shipper_id      = body.get("shipperId")
    pickup_address  = body.get("pickupAddress")
    dropoff_address = body.get("dropoffAddress")

    # Validate required fields
    if not all([shipper_id, pickup_address, dropoff_address]):
        return {
            "statusCode": 400,
            "body": json.dumps({"error": "shipperId, pickupAddress and dropoffAddress are required"})
        }

    # Generate order ID and timestamp
    order_id  = f"ORD-{datetime.now(timezone.utc).strftime('%Y%m%d')}-{uuid.uuid4().hex[:8].upper()}"
    now_iso   = datetime.now(timezone.utc).isoformat()

    # Auto-assign driver
    assigned_driver = random.choice(AVAILABLE_DRIVERS)

    # Write to DynamoDB
    table = dynamodb.Table(TABLE_NAME)
    try:
        table.put_item(Item={
            "orderId":        order_id,
            "shipperId":      shipper_id,
            "pickupAddress":  pickup_address,
            "dropoffAddress": dropoff_address,
            "status":         "ASSIGNED",
            "driverId":       assigned_driver,
            "createdAt":      now_iso,
            "updatedAt":      now_iso,
        })
    except Exception as exc:
        print(f"ERROR writing to DynamoDB: {exc}")
        return {
            "statusCode": 500,
            "body": json.dumps({"error": "Could not save order. Please retry."})
        }

    # Send to SQS
    try:
        sqs.send_message(
            QueueUrl=QUEUE_URL,
            MessageBody=json.dumps({
                "orderId":        order_id,
                "shipperId":      shipper_id,
                "pickupAddress":  pickup_address,
                "dropoffAddress": dropoff_address,
                "driverId":       assigned_driver,
                "createdAt":      now_iso,
            })
        )
    except Exception as exc:
        print(f"ERROR sending to SQS: {exc}")

    return {
        "statusCode": 201,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps({
            "orderId":  order_id,
            "status":   "ASSIGNED",
            "driverId": assigned_driver
        })
    }