"""Checks for the GitHub Pages data bundle."""
import json
import shutil

import pandas as pd
import pytest
import yaml

from kolproject.common import ROOT
from kolproject.kol.run import run
from kolproject.kol.site import build, main


@pytest.fixture(scope='module')
def quick_run(tmp_path_factory):
    root = tmp_path_factory.mktemp('site')
    shutil.copytree(ROOT / 'Data' / 'processed', root / 'Data' / 'processed')
    cfg = yaml.safe_load((ROOT / 'configs' / 'kol.yaml').read_text())
    cfg.pop('hydra'), cfg.pop('defaults')
    cfg.update(quick=True, experiments='all')
    return root, run(cfg, root=root)


def test_bundle_is_consistent(quick_run):
    root, out = quick_run
    bundle = build(out, root=root)
    text = json.dumps(bundle)  # must be JSON-serializable with no NaN
    assert 'NaN' not in text
    assert len(bundle['nodes']) == 500
    assert len(bundle['edges']) == 9378
    assert all(0 <= e['s'] < 500 and 0 <= e['t'] < 500 for e in bundle['edges'])
    communities = {n['community'] for n in bundle['nodes']}
    assert {c['community'] for c in bundle['communities']} == communities
    assert all(set(c['bbox']) == {'x0', 'x1', 'y0', 'y1', 'cx', 'cy'} for c in bundle['communities'])
    assert all(c['tier'] in bundle['meta']['tier_order'] for c in bundle['communities'])
    assert set(bundle['labels']['roc_curves']) == {'guideline_panel', 'cart_industry_advisor'}
    assert all(abs(sum(row['shares']) - 1) < 1e-2 for row in bundle['stability']['membership_shares'])
    assert len(bundle['temporal']['hcps']) == 500
    assert {s['name'] for s in bundle['sources']['sources']} >= {'Europe PMC / PubMed MED', 'CMS Open Payments'}
    # No dollar amounts leave the run directory.
    feature_names = text.lower().replace('log_consulting_usd', '').replace('log_cart_product_usd', '')
    assert 'usd' not in feature_names


def test_cli_writes_file(quick_run, tmp_path):
    root, out = quick_run
    target = tmp_path / 'site.json'
    main([str(out), '--out', str(target)])
    bundle = json.loads(target.read_text())
    assert bundle['meta']['hcps'] == 500 and target.stat().st_size < 2_000_000
