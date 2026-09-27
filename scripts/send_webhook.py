"""Act as the payment provider: sign and send a webhook event.

    python scripts/send_webhook.py --booking-id 1 --reference pay_abc --amount 350.00 \
        --event payment.succeeded [--event-id evt_123] [--times 3]

`--times` resends the identical event to demonstrate idempotency.
"""

import argparse
import hashlib
import hmac
import json
import os
import uuid
import urllib.request
from urllib.error import HTTPError


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("--url", default=os.getenv("API_URL", "http://localhost:8000") + "/payments/webhook/")
    parser.add_argument("--secret", default=os.getenv("WEBHOOK_SECRET", "dev-only-webhook-secret"))
    parser.add_argument("--event-id", default=f"evt_{uuid.uuid4().hex[:12]}")
    parser.add_argument("--event", choices=["payment.succeeded", "payment.failed"], required=True)
    parser.add_argument("--reference", required=True, help="payment provider_reference")
    parser.add_argument("--booking-id", type=int, required=True)
    parser.add_argument("--amount", required=True)
    parser.add_argument("--times", type=int, default=1)
    args = parser.parse_args()

    body = json.dumps(
        {
            "event_id": args.event_id,
            "event_type": args.event,
            "payment_reference": args.reference,
            "booking_id": args.booking_id,
            "amount": args.amount,
        }
    ).encode()
    signature = hmac.new(args.secret.encode(), body, hashlib.sha256).hexdigest()

    for attempt in range(1, args.times + 1):
        request = urllib.request.Request(
            args.url,
            data=body,
            method="POST",
            headers={"Content-Type": "application/json", "X-Webhook-Signature": signature},
        )
        try:
            with urllib.request.urlopen(request) as response:
                print(f"[{attempt}] {response.status} {response.read().decode()}")
        except HTTPError as exc:
            print(f"[{attempt}] {exc.code} {exc.read().decode()}")


if __name__ == "__main__":
    main()
