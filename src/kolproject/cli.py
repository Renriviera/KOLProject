"""Hydra entry point. Default invocation validates the delivered snapshot offline."""
import os
import time
os.environ['TZ'] = 'UTC'
if hasattr(time, 'tzset'):
    time.tzset()
import hydra
from omegaconf import DictConfig
from .common import ROOT, SNAPSHOT

@hydra.main(version_base='1.3', config_path=str(ROOT / 'configs'), config_name='config')
def main(cfg: DictConfig):
    if str(cfg.snapshot) != SNAPSHOT:
        raise ValueError('This release is a frozen snapshot. Create a new snapshot directory and source configuration for a fresh collection.')
    fixed = (str(cfg.eligibility.publication_start), str(cfg.eligibility.publication_end), int(cfg.eligibility.minimum_supported_publications))
    if fixed != ('2017-01-01', '2025-12-31', 2):
        raise ValueError('Eligibility is fixed for this release; overrides require a separately versioned cohort.')
    from .identity import TAXONOMIES, HOLDOUT
    if (str(cfg.eligibility.active_npi_status) != 'A' or str(cfg.eligibility.npi_type) != 'NPI-1'
            or str(cfg.eligibility.country) != 'US' or str(cfg.eligibility.holdout_pmid) != HOLDOUT
            or set(cfg.eligibility.taxonomy_codes) != TAXONOMIES):
        raise ValueError('NPI and holdout eligibility are fixed for this release.')
    if int(cfg.ranking.recent_start_year) != 2023:
        raise ValueError('Recent-publication features use the frozen 2023–2025 window.')
    if (int(cfg.payments.program_year) != 2024 or str(cfg.payments.dataset_id) != 'e6b17c6a-2534-4207-a4a1-6746a14911ff'
            or str(cfg.payments.release_date) != '2026-06-30'):
        raise ValueError('Payment source is fixed for this release; use a new version to change it.')
    stage = str(cfg.stage)
    weights = {'pagerank': float(cfg.ranking.pagerank_weight),
               'mean_citation_year_percentile': float(cfg.ranking.citation_percentile_weight),
               'recent_publications_2023_2025': float(cfg.ranking.recent_publications_weight),
               'weighted_betweenness': float(cfg.ranking.betweenness_weight)}
    if any(w < 0 for w in weights.values()) or abs(sum(weights.values()) - 1) > 1e-9:
        raise ValueError('Ranking weights must be nonnegative and sum to one.')
    if stage in ['extend', 'all']:
        from .corpus import extend_corpus
        from .discover import discover
        extend_corpus(workers=3)
        discover(int(cfg.supplemental_candidate_limit))
    if stage in ['screen', 'all']:
        from .screen import screen_all
        screen_all(workers=int(cfg.workers))
    if stage in ['select', 'rebuild', 'all']:
        from .rescreen import rescreen
        from .build import select_people
        rescreen()
        select_people(int(cfg.target_hcps), float(cfg.deduplication.fuzzy_name_threshold), float(cfg.deduplication.same_surname_first_threshold))
    if stage in ['payments', 'all']:
        from .payments import collect_payments
        collect_payments(workers=int(cfg.workers))
    if stage in ['build', 'rebuild', 'all']:
        from .build import build_dataset
        build_dataset(int(cfg.target_hcps), weights)
    if stage in ['edav', 'rebuild', 'all']:
        from .edav import profile
        profile(int(cfg.seed))
    if stage in ['rank', 'rebuild', 'all']:
        from .model import rank
        rank(weights)
    if stage in ['validate', 'rebuild', 'all']:
        from .validate import validate
        validate(int(cfg.target_hcps))
    if stage in ['manifests', 'rebuild', 'all']:
        from .manifest import make_manifests
        make_manifests()
    if stage not in ['extend', 'screen', 'select', 'payments', 'build', 'edav', 'rank', 'validate', 'manifests', 'rebuild', 'all']:
        raise ValueError('Unknown stage: ' + stage)

if __name__ == '__main__':
    main()
