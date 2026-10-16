"""Transparent baseline ranking, with weights and input hash recorded per run."""
import hashlib
import json
from pathlib import Path
import pandas as pd
from .common import ROOT, PROCESSED, dump, now

def rank(weights):
    path = PROCESSED / 'hcps.csv'
    frame = pd.read_csv(path, dtype={'npi': str, 'hcp_id': str})
    assert abs(sum(weights.values()) - 1) < 1e-9
    frame['score'] = 0.0
    for feature, weight in weights.items():
        percentile = (frame[feature].rank(method='average') - .5) / len(frame)
        frame['score'] += 100 * weight * percentile
    frame = frame.sort_values(['score', 'hcp_id'], ascending=[False, True])
    frame['rank'] = range(1, len(frame) + 1)
    identity = hashlib.sha256(json.dumps(weights, sort_keys=True).encode()).hexdigest()[:12]
    out = ROOT / 'models' / 'runs' / identity
    out.mkdir(parents=True, exist_ok=True)
    frame[['hcp_id', 'npi', 'display_name', 'score', 'rank']].to_csv(out / 'ranking.csv', index=False)
    dump(out / 'run_metadata.json', {'created_at_utc': now(), 'weights': weights,
                                   'input_path': str(path.relative_to(ROOT)), 'input_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                                   'description': 'Unfitted descriptive baseline; no causal or prescribing-performance claim.'})
    print('RANKING', out)

