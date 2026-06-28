# LogiHaul — Pan-Nigeria Logistics Platform

**BaseStack Academy · AWS Cloud Accelerator · Cohort 1 · Capstone Project**
**Scenario C — LogiHaul**

A serverless-first, multi-AZ logistics platform built on AWS, designed to absorb 50x traffic spikes during promotional events or flood-driven demand surges across 36 Nigerian states, without manual intervention.

## Architecture Diagram

![LogiHaul Architecture](work_Doc&screenshot/Architecture%20%26%20Documentation/S7-C1.png)

---

## Overview

LogiHaul connects shippers placing delivery orders with a fleet of truck drivers operating nationwide. The platform must:

- Accept order placements reliably during sudden, unpredictable 50x traffic spikes
- Track live driver location with sub-2-second read latency
- Survive an Availability Zone failure without data loss
- Stay under **$150/month** at steady state and **$400/month** at peak

The architecture is split deliberately into two independent paths so that a spike in one never blocks the other:

- **Order intake** is fully serverless (API Gateway → Lambda → DynamoDB → SQS → Lambda → SNS), so it scales horizontally and absorbs bursts without pre-provisioned capacity.
- **General web/dashboard traffic** runs on an EC2 Auto Scaling Group behind an ALB, scaling on CPU independently of the order-intake path.

This separation is the core design decision behind the entire system — see [Architecture Diagram](#architecture-diagram) and [50x Spike Design Rationale](#50x-spike-design-rationale) below.

---

## Architecture Diagram

![LogiHaul Architecture](./docs/logihaul-architecture.png)

*Six layers, top to bottom: Users → Networking (VPC) → Compute & Scaling → Serverless & Events → Storage → Observability.*

Full editable version: [Lucid diagram](https://lucid.app/lucidchart/380599a9-6b73-48c5-be92-fb6f1710fe56/edit)

---

## Services Used

| Layer | Services | Purpose |
|---|---|---|
| **Foundation & Identity** | IAM, VPC | Custom VPC (`10.0.0.0/16`), 2 AZs, 4 subnets (2 public, 2 private), bastion host, least-privilege IAM roles |
| **Compute & Scaling** | EC2, ALB, Auto Scaling | Self-identifying web tier behind an ALB; ASG scales out at CPU > 60%, 2 → 10 instances |
| **Storage** | S3 | Static site bucket (public) + documents bucket (private, presigned URLs), versioning, lifecycle, SSE, cross-region replication |
| **Databases** | RDS MySQL, DynamoDB, ElastiCache Redis | Relational data (drivers/invoices), order tracking, live driver location cache |
| **Serverless & Events** | Lambda, API Gateway, SQS, SNS | Order intake pipeline, decoupled from notification delivery |
| **Observability** | CloudWatch | Dashboard, alarms, Logs Insights, error simulation cycle |
| **Documentation** | — | This README, architecture diagram, portfolio post |

---

## Data Layer Design

### DynamoDB — `LogiHaul-Orders`

**Access patterns (written before schema, per rubric requirement):**

1. *Primary:* Get the full order record and current status, by Order ID — used by Lambda 1 on write and by any status-check call afterward.
2. *Secondary:* Get all orders assigned to a specific driver, sorted by most recent — used by the ops dashboard for manual assignment.

**Schema:**

| Key | Attribute | Example |
|---|---|---|
| Partition key | `orderId` | `ORD-20260621-A1B2C3D4` |
| — | `shipperId` | `SHIP-4471` |
| — | `pickupAddress` | string |
| — | `dropoffAddress` | string |
| — | `status` | `PENDING` / `ASSIGNED` / `IN_TRANSIT` / `DELIVERED` |
| — | `driverId` | nullable, set on manual assignment |
| — | `createdAt` / `updatedAt` | ISO 8601 |

**GSI:** `driverId-createdAt-index` — partition key `driverId`, sort key `createdAt`.

### RDS MySQL — Multi-AZ

Holds driver profiles, shipper accounts, and invoices — relationship-heavy, steady-state data, distinct from the spike-prone order intake path.

**Multi-AZ over Aurora, and over a Read Replica, because:** LogiHaul's relational workload is steady-state, not spike-prone — the 50x spike hits order intake (DynamoDB) and tracking, not billing, which settles asynchronously after delivery. Aurora's higher throughput ceiling isn't needed here, and its higher baseline cost would threaten the $150/month steady-state budget. Multi-AZ gives automatic failover for driver/billing data without Aurora's premium.

### ElastiCache Redis — live driver location

| | |
|---|---|
| Key pattern | `driver:location:{driverId}` |
| Value | `{"lat": ..., "lng": ..., "orderId": "...", "updatedAt": "..."}` |
| TTL | 300 seconds |

**Cache-aside flow:**
1. **Write** — driver app pings location → backend `SET`s directly into Redis with a 300s TTL. No DynamoDB write — this data is ephemeral by design.
2. **Hit** — dashboard/tracker `GET`s the key within 5 minutes → returns live location instantly.
3. **Miss/expiry** — `GET` returns `(nil)` after 5 minutes of silence → UI shows "location unavailable" instead of a stale pin.
4. **No write-through** — there is no permanent backing store for this data; Redis is the source of truth for "right now," since LogiHaul never needs historical GPS trails.

---

## Serverless Pipeline

```
API Gateway (POST /orders)
        │
        ▼
  Lambda 1 — Order Processor
  (writes order, status PENDING)
        │                    │
        ▼                    ▼
   DynamoDB              SQS Queue
   (LogiHaul-Orders)   (OrderConfirmations)
                              │
                              ▼
                    Lambda 2 — Notification Processor
                    (order confirmation only)
                              │
                              ▼
                          SNS Topic
                    (confirmed email alert)
```

**Lambda 1 — Order Processor**
- Trigger: API Gateway HTTP API, `POST /orders`
- Validates required fields, writes to DynamoDB, sends message to SQS
- Reserved concurrency: **100** — absorbs spike writes directly

**Lambda 2 — Notification Processor**
- Trigger: SQS (`LogiHaul-OrderConfirmations`)
- Publishes a single, scoped responsibility: order confirmation email via SNS
- Reserved concurrency: **50** — notification delivery can tolerate brief queueing; SQS buffers the backlog
- Uses `batchItemFailures` (SQS partial-batch failure reporting) so one bad message doesn't trigger duplicate notifications for the rest of the batch

**SQS Queue — `LogiHaul-OrderConfirmations`**
- Decouples order intake from notification delivery
- Visibility timeout: **30 seconds** — well above Lambda 2's typical sub-second runtime, accounting for cold starts and SNS publish latency
- Batch size: **10**

Source code: [`/lambda/order_processor`](./lambda/order_processor) · [`/lambda/notification_processor`](./lambda/notification_processor)

### IAM — least privilege per function

Each Lambda's role is scoped only to the resource it directly touches — Lambda 1 never publishes notifications, Lambda 2 never writes orders. No wildcard (`*`) resources.

| Role | Permissions |
|---|---|
| `LogiHaul-OrderProcessor-Role` | `dynamodb:PutItem` (orders table ARN only), `sqs:SendMessage` (queue ARN only), CloudWatch Logs |
| `LogiHaul-NotificationProcessor-Role` | `sqs:ReceiveMessage` / `DeleteMessage` / `GetQueueAttributes` (queue ARN only), `sns:Publish` (topic ARN only), CloudWatch Logs |

Full policy JSON: [`/iam`](./iam)

---

## 50x Spike Design Rationale

| Layer | How it absorbs a 50x spike |
|---|---|
| ASG (EC2/ALB) | Scales out at CPU > 60%, 2 → 10 instances, no manual intervention |
| DynamoDB | On-demand capacity mode absorbs spike writes without pre-provisioned throughput |
| Lambda concurrency | Order Processor reserved at 100; Notification Processor at 50 — intake never throttles even if notification delivery lags |
| SQS | Buffers the backlog between Lambda 1 (intake) and Lambda 2 (notify), so a slowdown in one never blocks the other |
| Redis | Sub-second reads keep delivery confirmation under 2 seconds regardless of order volume |

> During a 50x order spike, Lambda 1 absorbs writes independently of notification delivery. SQS buffers the backlog so Lambda 2 and SNS can catch up without blocking new order acceptance.

---

## Region

Primary region: **af-south-1 (Cape Town)** — opt-in region, enabled at account level before deployment.
Cross-region replication target: **eu-west-1 (Ireland)**.

---

## How to Test

### Order intake (Lambda 1 / API Gateway)

```bash
curl -X POST https://<api-id>.execute-api.af-south-1.amazonaws.com/orders \
  -H "Content-Type: application/json" \
  -d '{
    "shipperId": "SHIP-4471",
    "pickupAddress": "12 Yakubu Gowon Way, Jos",
    "dropoffAddress": "45 Murtala Mohammed Way, Abuja"
  }'
```
Expected: `201` response with `orderId` and `status: "PENDING"`. Confirm the item appears in the DynamoDB `LogiHaul-Orders` table.

### Notification delivery (Lambda 2 / SNS)

After placing an order above, check the subscribed inbox for a "LogiHaul order confirmation" email within ~1 minute.

### DynamoDB GSI

```bash
aws dynamodb query \
  --table-name LogiHaul-Orders \
  --index-name driverId-createdAt-index \
  --key-condition-expression "driverId = :d" \
  --expression-attribute-values '{":d":{"S":"DRV-1042"}}'
```

### Redis cache-aside + TTL expiry

```bash
redis-cli SET driver:location:DRV-1042 '{"lat":9.8965,"lng":8.8583}' EX 300
redis-cli GET driver:location:DRV-1042   # returns the value
# wait 5 minutes, or EXPIRE the key manually for a faster demo
redis-cli GET driver:location:DRV-1042   # returns (nil)
```

### Pre-signed URL (S3 documents bucket)

```python
import boto3
s3 = boto3.client("s3", region_name="af-south-1")
url = s3.generate_presigned_url(
    "get_object",
    Params={"Bucket": "logihaul-documents-<suffix>", "Key": "test-invoice.pdf"},
    ExpiresIn=3600,
)
print(url)
```
Paste the printed URL into a browser — it should load despite the bucket being fully private.

### EC2 / ASG live verification

Open the ALB DNS name in a browser and refresh repeatedly — the page displays the serving instance's ID, private IP, and Availability Zone, confirming the ALB is distributing traffic across the ASG.

### Error simulation (CloudWatch)

1. Temporarily add `raise ValueError("Simulated error for testing")` to Lambda 1, deploy, hit the API endpoint.
2. Confirm the `LogiHaul-OrderProcessor-ErrorAlarm` CloudWatch alarm transitions to `ALARM` and the SNS alert arrives.
3. Review the annotated log stream and the Logs Insights query filtering for `ERROR`.
4. Remove the line, redeploy, confirm a clean `201` response.

---

## Repository Structure

```
.
├── README.md
├── docs/
│   └── logihaul-architecture.png
├── lambda/
│   ├── order_processor/
│   │   └── lambda_function.py
│   └── notification_processor/
│       └── lambda_function.py
└── iam/
    ├── order-processor-policy.json
    └── notification-processor-policy.json
```

---

## Cost Notes

Per BaseStack submission rules, the following resources accrue real cost and should be deleted promptly once grading is confirmed:

- Aurora/RDS instance (if `db.t3.medium` class)
- ElastiCache `cache.t3.micro` node
- EC2 instances (if outside Free Tier)

Lambda, SQS, SNS, DynamoDB (on-demand), and S3 remain negligible or Free Tier eligible.
