"""Create a separate descriptive modeling table without changing either input."""
import argparse
from pathlib import Path
import pandas as pd
from kolproject.common import ROOT, dump, now
from kolproject.utilization_source import COHORT, OUT, sha

def join(output, year=2024):
    output = Path(output).resolve()
    if (ROOT / 'Data').resolve() in output.parents:
        raise ValueError('Save the modeling join outside Data to preserve source and processed tables')
    if year != 2024:
        raise ValueError('This release contains 2024 utilization only')
    original_hash = sha(COHORT)
    h = pd.read_csv(COHORT, dtype={'npi': str, 'hcp_id': str})
    f = pd.read_csv(OUT / 'hcp_utilization_features.csv', dtype={'npi': str, 'hcp_id': str})
    f = f.loc[f.service_year == year].drop(columns=['cohort_snapshot_date'])
    joined = h.merge(f, on=['npi', 'hcp_id'], how='left', validate='one_to_one', indicator=True)
    assert len(joined) == 500 and joined['_merge'].eq('both').all()
    assert joined.npi.is_unique and sha(COHORT) == original_hash
    output.parent.mkdir(parents=True, exist_ok=True)
    joined.drop(columns='_merge').to_csv(output, index=False)
    dump(output.with_suffix('.metadata.json'), {
        'created_at_utc': now(), 'rows': len(joined), 'service_year': year,
        'cohort_sha256': original_hash, 'features_sha256': sha(OUT / 'hcp_utilization_features.csv'),
        'purpose': 'descriptive cross-sectional analysis',
        'temporal_warning': '2026-observed publication/network features must not be used as prospective predictors of 2024 utilization outcomes.'})
    print('JOINED', len(joined), 'HCPs', output)

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=ROOT / 'models' / 'inputs' / 'hcp_utilization_2024.csv')
    parser.add_argument('--year', type=int, default=2024)
    args = parser.parse_args()
    join(args.output, args.year)
