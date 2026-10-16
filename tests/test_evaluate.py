"""Regression checks for the validation available in a GitHub clone."""
from copy import deepcopy
import shutil

import pytest

from kolproject.common import ROOT, read_csv, write_csv
from kolproject.evaluate import evaluate


@pytest.fixture
def release(tmp_path):
    shutil.copytree(ROOT / 'Data' / 'processed', tmp_path / 'Data' / 'processed')
    return tmp_path


def test_published_tables_pass_without_source_caches(release):
    report = evaluate(release)
    assert not (release / 'Data' / 'raw').exists()
    assert report['passed']
    assert report['counts']['hcps'] == 500
    assert report['processed_files_sha256']


def test_invalid_authorship_identity_fails_even_with_valid_hcp_id(release):
    path = release / 'Data' / 'processed' / '2026-10-09' / 'authorships.csv'
    rows = read_csv(path)
    rows[0]['npi'] = '0000000000'
    write_csv(path, rows)
    report = evaluate(release)
    assert not report['passed']
    assert 'authorship_identity_errors' in report['failures']


def test_missing_feature_row_and_duplicate_service_are_detected(release):
    base = release / 'Data' / 'processed' / '2026-10-15'
    path = base / 'hcp_utilization_features.csv'
    write_csv(path, read_csv(path)[1:])
    path = base / 'cms_provider_service.csv'
    rows = read_csv(path)
    write_csv(path, rows + [deepcopy(rows[0])])
    report = evaluate(release)
    assert not report['passed']
    assert 'utilization_feature_coverage_errors' in report['failures']
    assert 'duplicate_service_keys' in report['failures']
