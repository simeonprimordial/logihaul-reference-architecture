"""
LogiHaul - Lambda 2: Notification Processor

Trigger: SQS (LogiHaul-OrderConfirmations queue)
Responsibility: read order details from the queued message and publish an
"order received" confirmation to SNS. This function has a single job -
it does not write to DynamoDB, does not touch order status beyond what's
already in the message, and does not handle any other notification type.

Batch size and visibility timeout are configured on the SQS trigger /
queue itself (see Section 5 evidence), not in this code.
"""

import json
import os

import boto3

sns = boto3.client("sns")

TOPIC_ARN = os.environ["ORDER_ALERTS_TOPIC_ARN"]


def lambda_handler(event, context):
    failures = []

    for record in event.get("Records", []):
        message_id = record.get("messageId")
        try:
            order = json.loads(record["body"])
            _publish_confirmation(order)
        except Exception as exc:
            print(f"ERROR processing message {message_id}: {exc}")
            failures.append({"itemIdentifier": message_id})

    # Returning batchItemFailures tells SQS to only retry the records
    # that actually failed, not the whole batch. Requires
    # FunctionResponseTypes = ReportBatchItemFailures on the event
    # source mapping.
    return {"batchItemFailures": failures}


def _publish_confirmation(order):
    order_id = order["orderId"]
    pickup = order.get("pickupAddress", "pickup location")
    dropoff = order.get("dropoffAddress", "destination")

    message = (
        f"Order {order_id} received.\n"
        f"Pickup: {pickup}\n"
        f"Dropoff: {dropoff}\n\n"
        f"We will notify you again once a driver is assigned."
    )

    sns.publish(
        TopicArn=TOPIC_ARN,
        Subject=f"LogiHaul order confirmation - {order_id}",
        Message=message,
    )

    print(f"Published confirmation for order {order_id}")
