import json
import boto3
import os

sns = boto3.client("sns")

TOPIC_ARN = os.environ["ORDER_ALERTS_TOPIC_ARN"]


def lambda_handler(event, context):
    failures = []

    for record in event.get("Records", []):
        message_id = record.get("messageId")
        try:
            # Parse the message Lambda 1 sent to SQS
            order = json.loads(record["body"])

            _publish_confirmation(order)

        except Exception as exc:
            print(f"ERROR processing message {message_id}: {exc}")
            failures.append({"itemIdentifier": message_id})

    return {"batchItemFailures": failures}


def _publish_confirmation(order):
    order_id        = order.get("orderId", "UNKNOWN")
    shipper_id      = order.get("shipperId", "UNKNOWN")
    pickup_address  = order.get("pickupAddress", "Not provided")
    dropoff_address = order.get("dropoffAddress", "Not provided")
    driver_id       = order.get("driverId", "TBD")
    created_at      = order.get("createdAt", "Not provided")

    message = (
        f"New LogiHaul Order Confirmed\n"
        f"==============================\n"
        f"Order ID:    {order_id}\n"
        f"Shipper ID:  {shipper_id}\n"
        f"Pickup:      {pickup_address}\n"
        f"Dropoff:     {dropoff_address}\n"
        f"Driver:      {driver_id}\n"
        f"Created At:  {created_at}\n"
        f"==============================\n"
        f"Your order has been received and a driver has been assigned.\n"
        f"You will be notified when your delivery is in transit."
    )

    sns.publish(
        TopicArn=TOPIC_ARN,
        Subject=f"LogiHaul Order Confirmed - {order_id}",
        Message=message,
    )

    print(f"Notification sent for order {order_id}, driver {driver_id}, shipper {shipper_id}")