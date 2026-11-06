#!/usr/bin/env python3
"""Seed demo inventory for manual requests. Scenarios seed their own opaque SKUs and don't need this."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx

from app.config import INVENTORY_URL

for sku, available in {"DEMO-WIDGET": 100, "DEMO-LAST-UNIT": 1, "DEMO-SOLD-OUT": 0}.items():
    httpx.put(f"{INVENTORY_URL}/inventory/{sku}", json={"available": available}, timeout=5).raise_for_status()
    print(f"{sku}: {available}")
