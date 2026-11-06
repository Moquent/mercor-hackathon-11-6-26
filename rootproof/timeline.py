"""Wall-clock labels for simulated seconds. Second 0 of the simulation is 14:00:00 on the packet's clock."""
from datetime import datetime, timedelta

CLOCK_ZERO = datetime(2026, 11, 6, 14, 0, 0)


def clock(t):
    return (CLOCK_ZERO + timedelta(seconds=t)).strftime("%H:%M:%S")
