#!/usr/bin/env python3
"""Render Grafana provisioning templates from .env.

Writes grafana/provisioning/datasources/influxdb.yml from the template
grafana/provisioning/datasources/influxdb.yml.template by substituting
the `__INFLUXDB_ADMIN_PASSWORD__` placeholder with the value from `.env`.

This ensures the provisioning file on the host contains secrets before the
Grafana container starts and avoids editing files inside the container.
"""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
TEMPLATE = ROOT / 'provisioning' / 'datasources' / 'influxdb.yml.template'
OUT = ROOT / 'provisioning' / 'datasources' / 'influxdb.yml'
ENV_FILE = Path(__file__).resolve().parent.parent / '.env'


def load_env(path: Path) -> dict:
    result = {}
    if not path.exists():
        return result
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        k, v = line.split('=', 1)
        result[k.strip()] = v.strip()
    return result


def main() -> int:
    env = load_env(ENV_FILE)
    pwd = env.get('INFLUXDB_ADMIN_PASSWORD')
    if not pwd:
        print('INFLUXDB_ADMIN_PASSWORD not found in .env', flush=True)
        return 1

    tpl = TEMPLATE.read_text(encoding='utf-8')
    out = tpl.replace('__INFLUXDB_ADMIN_PASSWORD__', pwd)
    OUT.write_text(out, encoding='utf-8')
    print(f'Wrote {OUT}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
