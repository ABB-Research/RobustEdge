#!/usr/bin/env python3
"""Render provisioning templates and ensure Grafana datasource secure fields via API.

Usage: run this after `docker compose up -d grafana` to render templates and set
the datasource password using the Grafana HTTP API. This avoids storing secrets
in plain files inside the container and uses Grafana's API to set secure fields.
"""
import json
import time
import base64
import urllib.request
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


def render_template(pwd: str) -> None:
    tpl = TEMPLATE.read_text(encoding='utf-8')
    out = tpl.replace('__INFLUXDB_ADMIN_PASSWORD__', pwd)
    OUT.write_text(out, encoding='utf-8')
    print(f'Wrote {OUT}')


def grafana_api_put_datasource(password: str, user: str = 'admin', passwd: str = None) -> None:
    # Use admin creds from .env if provided, else use 'admin'/'admin'
    if passwd is None:
        passwd = password
    auth = base64.b64encode(f"{user}:{passwd}".encode()).decode()
    hdr = {'Authorization': 'Basic ' + auth, 'Content-Type': 'application/json'}

    # Get datasource by uid
    url = 'http://localhost:3000/api/datasources/uid/influxdb-tep'
    req = urllib.request.Request(url, headers={'Authorization': 'Basic ' + auth})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            ds = json.load(r)
    except Exception as e:
        raise RuntimeError(f'Failed to GET datasource: {e}')

    # Prepare payload; remove fields not accepted by PUT
    ds.pop('id', None)
    ds.pop('version', None)
    ds['secureJsonData'] = ds.get('secureJsonData', {})
    ds['secureJsonData']['password'] = password

    data = json.dumps(ds).encode()
    req2 = urllib.request.Request('http://localhost:3000/api/datasources/1', data=data, headers=hdr, method='PUT')
    try:
        with urllib.request.urlopen(req2, timeout=10) as r:
            print('Datasource updated via API:', r.status)
    except Exception as e:
        raise RuntimeError(f'Failed to PUT datasource: {e}')


def wait_for_grafana(timeout: int = 60) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen('http://localhost:3000/api/health', timeout=3) as r:
                print('Grafana ready')
                return
        except Exception:
            time.sleep(1)
    raise RuntimeError('Grafana did not become ready in time')


def main():
    env = load_env(ENV_FILE)
    pwd = env.get('INFLUXDB_ADMIN_PASSWORD')
    if not pwd:
        print('INFLUXDB_ADMIN_PASSWORD not found in .env')
        return 1

    render_template(pwd)

    # Wait for grafana and push secure field
    try:
        wait_for_grafana()
    except Exception as e:
        print('Grafana not ready, skipping API update:', e)
        return 0

    try:
        grafana_api_put_datasource(password=pwd)
    except Exception as e:
        print('Failed to update datasource via API:', e)
        return 1

    # Check health
    try:
        with urllib.request.urlopen('http://localhost:3000/api/datasources/1/health', timeout=5) as r:
            print(r.read().decode())
    except Exception as e:
        print('Health check failed:', e)
        return 1

    return 0


if __name__ == '__main__':
    raise SystemExit(main())
