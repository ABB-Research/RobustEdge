from __future__ import annotations

import argparse
import os
import time
from typing import Dict

from influxdb import InfluxDBClient


INTENSITY_TO_PPS: Dict[str, float] = {
    "low": 100.0,
    "medium": 500.0,
    "high": 1000.0,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run burst attack traffic against InfluxDB")
    parser.add_argument("--duration", type=float, default=float(os.getenv("ATTACK_DURATION", "20")))
    parser.add_argument("--intensity", type=str, default=os.getenv("ATTACK_INTENSITY", "medium"), choices=["low", "medium", "high"])
    parser.add_argument("--pps", type=float, default=float(os.getenv("ATTACK_PPS", "0")), help="Overrides intensity if > 0")
    parser.add_argument("--value", type=float, default=float(os.getenv("ATTACK_BURST_VALUE", "0")))
    parser.add_argument("--measurement", type=str, default=os.getenv("ATTACK_MEASUREMENT", "attack_records"))
    parser.add_argument("--tag", type=str, default=os.getenv("ATTACK_TAG", "host"))
    parser.add_argument("--host", type=str, default=os.getenv("INFLUXDB_HOST", "influxdb"))
    parser.add_argument("--port", type=int, default=int(os.getenv("INFLUXDB_PORT", "8086")))
    parser.add_argument("--db", type=str, default=os.getenv("INFLUXDB_DB", "appdb"))
    parser.add_argument("--username", type=str, default=os.getenv("INFLUXDB_ADMIN_USER", "admin"))
    parser.add_argument("--password", type=str, default=os.getenv("INFLUXDB_ADMIN_PASSWORD", "change_me_admin_password"))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    pps = args.pps if args.pps > 0 else INTENSITY_TO_PPS[args.intensity]
    pps = max(0.1, pps)
    interval = 1.0 / pps
    total_points = max(1, int(round(args.duration * pps)))

    print(
        f"attack start duration={args.duration}s intensity={args.intensity} pps={pps} points={total_points}",
        flush=True,
    )

    client = InfluxDBClient(
        host=args.host,
        port=args.port,
        username=args.username,
        password=args.password,
        database=args.db,
    )
    client.create_database(args.db)
    client.switch_database(args.db)

    try:
        for _ in range(total_points):
            point = {
                "measurement": args.measurement,
                "tags": {"paramId": args.tag, "attack": "burst"},
                "time": time.time_ns(),
                "fields": {"value": float(args.value), "pps": float(pps)},
            }
            client.write_points([point], time_precision="n")
            time.sleep(interval)
    finally:
        client.close()

    print("attack complete", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
