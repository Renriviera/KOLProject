"""Paths, public-source snapshots and explicit observation timestamps."""
from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import threading
import time
import unicodedata
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(os.environ.get('KOL_PROJECT_ROOT', Path(__file__).resolve().parents[2]))
SNAPSHOT = '2026-10-09'
RAW = ROOT / 'Data' / 'raw' / SNAPSHOT
INTERIM = ROOT / 'Data' / 'interim' / SNAPSHOT
PROCESSED = ROOT / 'Data' / 'processed' / SNAPSHOT
for path in (RAW, INTERIM, PROCESSED):
    path.mkdir(parents=True, exist_ok=True)
_lock = threading.Lock()
_next_request = 0.0

def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds').replace('+00:00', 'Z')

def config_input(name):
    """Prefer machine-local evidence while keeping tracked templates reusable."""
    local = ROOT / 'configs' / 'local' / name
    return local if local.is_file() else ROOT / 'configs' / name

def norm(value):
    return ''.join(c for c in unicodedata.normalize('NFKD', str(value).lower()) if not unicodedata.combining(c))

def tokens(value):
    return re.findall(r'[a-z]+', norm(value))

def plain(value):
    import html
    return re.sub(r'\s+', ' ', html.unescape(re.sub('<[^>]+>', ' ', value or ''))).strip()

def clean_affiliation(value):
    value = re.sub(r'\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b', '', plain(value))
    return re.sub(r'Electronic address:\s*', '', value).strip(' ;.')

def dump(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding='utf-8')
    temporary.replace(path)

def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))

def write_csv(path, rows, fields=None):
    rows = list(rows)
    fields = fields or list(dict.fromkeys(k for r in rows for k in r))
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with Path(path).open('w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)

def read_csv(path):
    with Path(path).open(newline='', encoding='utf-8') as stream:
        return list(csv.DictReader(stream))

def fetch_json(url, source, force=False):
    """Cache an immutable response with the actual UTC retrieval time and hash."""
    global _next_request
    key = hashlib.sha256(url.encode()).hexdigest()[:24]
    path = RAW / source / (key + '.json')
    meta_path = path.with_suffix('.meta.json')
    if path.exists() and meta_path.exists() and not force:
        return load(path), load(meta_path)
    for attempt in range(3):
        try:
            with _lock:
                delay = max(0, _next_request - time.monotonic())
                _next_request = max(_next_request, time.monotonic()) + 0.35
            if delay:
                time.sleep(delay)
            request = urllib.request.Request(url, headers={'User-Agent': 'Columbia-CART-KOL-Academic-Project/0.2 (public research metadata)'})
            with urllib.request.urlopen(request, timeout=45) as response:
                payload = response.read()
                observed = now()
                headers = dict(response.headers)
            result = json.loads(payload)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
            meta = {
                'source_id': source + ':' + key, 'source_name': source,
                'source_url': url, 'retrieved_at_utc': observed,
                'retrieval_time_precision': 'second',
                'http_last_modified': headers.get('Last-Modified'),
                'sha256': hashlib.sha256(payload).hexdigest(),
                'bytes': len(payload), 'local_path': str(path.relative_to(ROOT)),
            }
            dump(meta_path, meta)
            return result, meta
        except Exception:
            if attempt == 2:
                raise
            time.sleep(1 + 2 * attempt)

