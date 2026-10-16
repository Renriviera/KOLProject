"""Optional overlap of independent CMS collection with the NPI screening run."""
import time
from kolproject.common import INTERIM, load
from kolproject.payments import collect_payments

while True:
    screened = [load(path) for path in (INTERIM / 'screening').glob('*.json')]
    qualified = {}
    for row in screened:
        if row['status'] == 'qualifies':
            npi = str(row['accepted']['record']['number'])
            qualified[npi] = row
    pending = [row for npi, row in qualified.items() if not (INTERIM / 'payments' / (npi + '.json')).exists()]
    if pending:
        collect_payments(pending, workers=6)
        print('PREFETCHED', len(qualified), 'qualified identities', flush=True)
    extra_path = INTERIM / 'supplemental_candidates_to_screen.json'
    expected = 1357 + (len(load(extra_path)) if extra_path.exists() else 0)
    if len(screened) >= expected:
        break
    time.sleep(15)
