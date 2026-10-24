"""Local evidence remains active without replacing tracked config templates."""
from kolproject import common, identity


def test_identity_evidence_prefers_local_file_and_falls_back_to_template(tmp_path, monkeypatch):
    monkeypatch.setattr(common, 'ROOT', tmp_path)
    template = tmp_path / 'configs' / 'affiliation_evidence.csv'
    local = tmp_path / 'configs' / 'local' / 'affiliation_evidence.csv'
    template.parent.mkdir()
    local.parent.mkdir()
    common.write_csv(template, [{'npi': '1234567890', 'city': 'Boston'}])
    common.write_csv(local, [{'npi': '1234567890', 'city': 'Seattle'}])
    identity.verified_institution_evidence.cache_clear()
    try:
        assert identity.verified_institution_evidence()['1234567890'][0]['city'] == 'Seattle'
        local.unlink()
        identity.verified_institution_evidence.cache_clear()
        assert identity.verified_institution_evidence()['1234567890'][0]['city'] == 'Boston'
    finally:
        identity.verified_institution_evidence.cache_clear()
