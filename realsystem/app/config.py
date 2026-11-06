"""Environment-derived settings shared by every service. Defaults target the compose host ports."""
from __future__ import annotations

import os

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+psycopg://dagscope:dagscope@localhost:5433/dagscope")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6380/0")
GATEWAY_URL = os.getenv("GATEWAY_URL", "http://localhost:8000")
ORDERS_URL = os.getenv("ORDERS_URL", "http://localhost:8001")
INVENTORY_URL = os.getenv("INVENTORY_URL", "http://localhost:8002")
VERIFIER_URL = os.getenv("VERIFIER_URL", "http://localhost:8003")

STREAM = os.getenv("ORDER_STREAM", "orders.events")
CONSUMER_GROUP = os.getenv("CONSUMER_GROUP", "workers")
WORKER_THREADS = int(os.getenv("WORKER_THREADS", "3"))
# A delivery left un-ACKed this long is reclaimed by XAUTOCLAIM and redelivered as a new attempt.
RETRY_IDLE_MS = int(os.getenv("RETRY_IDLE_MS", "1500"))
MAX_DELIVERY_ATTEMPTS = int(os.getenv("MAX_DELIVERY_ATTEMPTS", "5"))
HTTP_TIMEOUT_S = float(os.getenv("HTTP_TIMEOUT_S", "5"))
