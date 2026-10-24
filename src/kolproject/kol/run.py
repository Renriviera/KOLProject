"""Run selected experiments, then write tables, a per-HCP score file, metadata, and report.md."""
from __future__ import annotations

import hashlib
import json
from importlib.metadata import version
from pathlib import Path
import time

import numpy as np
import pandas as pd

from ..common import ROOT, dump, now
from .data import load_cohort
from .experiments import ALL_EXPERIMENTS, EXPERIMENTS, Context
from .graph import WEIGHTING_NOTES

QUICK = {('community', 'seeds'): 6, ('community', 'small_seeds'): 3, ('bootstrap', 'replicates'): 8,
         ('bootstrap', 'leiden_seeds'): 1,
         ('perturbation', 'draws'): 200, ('face_validity', 'auc_bootstrap'): 50,
         ('temporal', 'difference_bootstrap'): 50, ('node_removal', 'top_k'): [1, 10],
         ('movers', 'breadth_bootstrap'): 50}


def resolve(cfg: dict) -> dict:
    cfg = json.loads(json.dumps(cfg))
    if cfg.get('quick'):
        for (section, key), value in QUICK.items():
            cfg[section][key] = value
        # Smoke-test outputs go to an ignored folder so they never mix with real runs.
        cfg['output_dir'] = 'evaluations/runs/kol_quick'
    selected = cfg.get('experiments', 'all')
    cfg['experiments'] = list(ALL_EXPERIMENTS) if selected in ('all', None) else list(selected)
    unknown = set(cfg['experiments']) - set(ALL_EXPERIMENTS)
    if unknown:
        raise ValueError(f'Unknown experiments {sorted(unknown)}; choose from {ALL_EXPERIMENTS}.')
    # Community tiers and mover shares reuse earlier experiments' runs, so those run too.
    needs = {'interpretability': ['stability', 'bootstrap', 'node_removal', 'movers'], 'movers': ['bootstrap']}
    for _ in range(2):
        for experiment, required in needs.items():
            if experiment in cfg['experiments']:
                cfg['experiments'] += [r for r in required if r not in cfg['experiments']]
    cfg['experiments'] = [e for e in ALL_EXPERIMENTS if e in cfg['experiments']]
    return cfg


def run_id(cfg: dict, input_hashes: dict) -> str:
    keyed = {k: v for k, v in cfg.items() if k not in ('experiments', 'output_dir')}
    payload = json.dumps({'config': keyed, 'inputs': input_hashes}, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:12]


def _md_table(frame: pd.DataFrame, columns=None, digits=3, limit=None) -> str:
    frame = frame if columns is None else frame[columns]
    if limit:
        frame = frame.head(limit)

    def cell(value):
        if isinstance(value, (float, np.floating)):
            return '' if np.isnan(value) else f'{value:.{digits}f}'
        return str(value).replace('|', '/')
    lines = ['| ' + ' | '.join(map(str, frame.columns)) + ' |', '|' + '---|' * len(frame.columns)]
    lines += ['| ' + ' | '.join(cell(v) for v in row) + ' |' for row in frame.itertuples(index=False)]
    return '\n'.join(lines)


def _temporal_compact(table: pd.DataFrame) -> pd.DataFrame:
    """One readable column per target: rho, and the change versus the volume-only null with its 95% CI."""
    out = pd.DataFrame({'score': table.score})
    targets = [c[len('spearman_'):] for c in table.columns if c.startswith('spearman_')]
    for target in targets:
        out[target] = [f"{r:.2f} ({d:+.2f} [{lo:+.2f}, {hi:+.2f}])" for r, d, lo, hi in zip(
            table[f'spearman_{target}'], table[f'delta_vs_volume_{target}'],
            table[f'delta_ci_low_{target}'], table[f'delta_ci_high_{target}'])]
    out['guideline_panel_auc'] = [f'{a:.2f} [{lo:.2f}, {hi:.2f}]' for a, lo, hi in zip(
        table.guideline_panel_auc, table.guideline_panel_auc_ci_low, table.guideline_panel_auc_ci_high)]
    return out


def hcp_table(ctx: Context, tables: dict) -> pd.DataFrame:
    hcps = ctx.cohort.hcps
    f = ctx.primary.features
    out = pd.DataFrame({'npi': hcps.npi, 'display_name': hcps.display_name, 'institution': hcps.institution,
                        'npi_state': hcps.npi_state, 'community': f.community,
                        'community_node_consensus': ctx.node_consensus, 'hub_role': f.hub_role,
                        'participation': f.participation, 'within_module_z': f.within_module_z,
                        'pagerank_within_community_pct': f.pagerank_within_community,
                        'publications': f.publications})
    if 'community_profiles' in tables:
        profiles = tables['community_profiles'].set_index('community')
        out['community_label'] = out.community.map(profiles.label)
        out['community_tier'] = out.community.map(profiles.tier)
        out['community_archetype'] = out.community.map(profiles.archetype)
        out['community_theme'] = out.community.map(profiles.theme)
        out['community_nameable'] = out.community.map(profiles.nameable)
    if 'movers' in tables:
        movers = tables['movers']
        for column in ['mover_class', 'mover_score', 'home_communities', 'community_split',
                       'manufacturers_2024']:
            out[column] = movers[column]
    for axis in ctx.primary.axes.columns:
        out[f'axis_{axis}'] = ctx.primary.axes[axis]
    top_k = int(ctx.cfg['top_k'])
    for name, frame in ctx.primary.scores.items():
        out[f'{name}_score'] = frame.score
        out[f'{name}_rank'] = frame['rank']
        for column in [c for c in frame.columns if c.startswith('contrib_')]:
            out[f'{name}_{column}'] = frame[column]
        boot = tables.get(f'bootstrap_ranks_{name}')
        if boot is not None:
            out[f'{name}_bootstrap_rank_p2_5'] = boot.bootstrap_rank_p2_5
            out[f'{name}_bootstrap_rank_p97_5'] = boot.bootstrap_rank_p97_5
            out[f'{name}_bootstrap_p_top{top_k}'] = boot[f'p_top{top_k}']
        pert = tables.get(f'weight_perturbation_{name}')
        if pert is not None:
            out[f'{name}_weight_p_top{top_k}'] = pert[f'p_top{top_k}_near']
            out[f'{name}_robustness_class'] = pert.robustness_class
    for label in ctx.cohort.labels.columns:
        out[f'label_{label}'] = ctx.cohort.labels[label]
    first = next(iter(ctx.primary.scores))
    return out.sort_values(f'{first}_rank')


def report(ctx: Context, cfg: dict, results: dict, tables: dict, scores: pd.DataFrame, rid: str) -> str:
    top_k = int(cfg['top_k'])
    lines = [f'# KOL community and composite-score experiments (run {rid})', '',
             'Scores describe academic visibility and observed 2024 industry engagement in a '
             'publication-selected cohort of 500 CAR-T lymphoma HCPs. They are not measures of '
             'prescribing influence, clinical quality, or referral volume.', '',
             f'- Inputs: {len(ctx.cohort.nodes)} HCPs, {ctx.primary.graph.ecount()} coauthorship edges, '
             f'{len(ctx.incidence.pmids)} included papers.',
             f'- Primary partition: Leiden (RB configuration), resolution {ctx.resolution}, medoid of '
             f'{cfg["community"]["seeds"]} seeds; {int(ctx.primary.membership.max())} communities.',
             '- Weak labels: ' + ', '.join(f'{c} ({int(ctx.cohort.labels[c].sum())} positives)'
                                           for c in ctx.cohort.labels.columns) + '.']
    lines += [f'- {note}' for note in ctx.cohort.notes]
    lines += ['', '## Composite definitions', '',
              'Each axis is the mean cohort percentile of its features; each composite is 100 × the weighted sum of axes. '
              'Per-HCP contribution columns in `kol_scores.csv` sum to the score.', '']
    axes_rows = pd.DataFrame([{'axis': a, 'features': ' + '.join(f), **{n: w.get(a, 0) for n, w in ctx.composites.items()}}
                              for a, f in ctx.axes_def.items()])
    lines += [_md_table(axes_rows, digits=2), '']
    for name in ctx.composites:
        cols = ['display_name', 'institution', f'{name}_score', f'{name}_rank']
        extra = [c for c in [f'{name}_bootstrap_rank_p2_5', f'{name}_bootstrap_rank_p97_5',
                             f'{name}_weight_p_top{top_k}', f'{name}_robustness_class'] if c in scores]
        frame = scores.sort_values(f'{name}_rank')
        lines += [f'### Top 15: {name}', '', _md_table(frame, cols + extra, digits=2, limit=15), '']
    titles = {'stability': '1. Partition stability', 'weighting': '2. Edge-weighting ablation',
              'bootstrap': '3. Bootstrap over papers', 'node_removal': '4. Node-removal sensitivity',
              'movers': '4b. Movers between communities',
              'leave_one_axis_out': '5. Leave-one-axis-out importance',
              'weight_perturbation': '6. Weight perturbation',
              'collinearity': '7. Collinearity and volume loading', 'face_validity': '8. Face validity vs weak labels',
              'temporal': '9. Temporal holdout', 'interpretability': '10. Community interpretability audit'}
    key_tables = {'stability': [('method_comparison', None), ('resolution_sweep', None)],
                  'weighting': [('weighting_comparison', None)],
                  'bootstrap': [('bootstrap_communities', None), ('bootstrap_summary', None)],
                  'node_removal': [('node_removal_global', None), ('node_removal_leader', None)],
                  'movers': [('movers_top', None), ('mover_breadth_test', None), ('mover_breadth_strata', None)],
                  'leave_one_axis_out': [('axis_importance', None)],
                  'collinearity': [('volume_loading', None), ('feature_vif', None)],
                  'face_validity': [('face_validity', ['label', 'score', 'circular', 'auc', 'auc_ci_low',
                                                       'auc_ci_high', 'precision_at_25', 'lift_at_50'])],
                  'temporal': [('temporal_compact', None)],
                  'interpretability': [('community_profiles', ['community', 'label', 'tier', 'archetype', 'size',
                                                               'theme', 'seed_consensus', 'jaccard_literature_drop',
                                                               'flags', 'leaders']),
                                       ('community_profiles', ['community', 'tier', 'tier_reason', 'drifts_to'])]}
    if 'temporal_holdout' in tables:
        tables = dict(tables, temporal_compact=_temporal_compact(tables['temporal_holdout']))
    if 'movers' in tables:
        top = tables['movers'][tables['movers'].mover_class != 'anchored'].head(25)
        tables = dict(tables, movers_top=top[['display_name', 'institution', 'mover_class', 'mover_score',
                                              'community_split', 'publications', 'manufacturers_2024']])
    for name in cfg['experiments']:
        result = results[name]
        lines += [f'## {titles[name]}', '', f'_{EXPERIMENTS[name].__doc__.strip().splitlines()[0]}_', '']
        for key, value in result['summary'].items():
            lines.append(f'- **{key}**: {value}')
        lines.append(f'- runtime: {result["seconds"]:.1f}s')
        if name == 'temporal':
            lines.append('- cells read: Spearman rho (change versus the volume-only null [95% bootstrap CI])')
        lines.append('')
        for table, columns in key_tables.get(name, []):
            if table in tables:
                lines += [_md_table(tables[table].reset_index(drop=True), columns), '']
    lines += ['## Edge weightings', ''] + [f'- **{k}**: {v}' for k, v in WEIGHTING_NOTES.items()]
    return '\n'.join(lines) + '\n'


def run(cfg: dict, root: Path | str = ROOT) -> Path:
    root = Path(root)
    cfg = resolve(cfg)
    started = time.time()
    cohort = load_cohort(root)
    rid = run_id(cfg, cohort.input_hashes)
    out = root / cfg['output_dir'] / rid
    out.mkdir(parents=True, exist_ok=True)
    print(f'KOL run {rid}: building primary analysis', flush=True)
    ctx = Context(cohort, cfg)
    tables, results = {}, {}
    for name in cfg['experiments']:
        print(f'  running {name}', flush=True)
        t0 = time.time()
        produced, summary = EXPERIMENTS[name](ctx)
        results[name] = {'summary': summary, 'seconds': time.time() - t0}
        tables.update(produced)
        if name == 'bootstrap':
            ctx.cache['bootstrap'] = produced['bootstrap_communities']
        if name == 'node_removal':
            ctx.cache['node_removal'] = produced['node_removal_leader']
        if name == 'movers':
            ctx.cache['movers'] = produced['movers']
        for table_name, frame in produced.items():
            frame.to_csv(out / f'{table_name}.csv', index=not isinstance(frame.index, pd.RangeIndex))
    scores = hcp_table(ctx, tables)
    scores.to_csv(out / 'kol_scores.csv', index_label='hcp_id')
    ctx.primary.features.to_csv(out / 'features.csv', index_label='hcp_id')
    (out / 'report.md').write_text(report(ctx, cfg, results, tables, scores, rid), encoding='utf-8')
    dump(out / 'run_metadata.json', {
        'run_id': rid, 'created_at_utc': now(), 'seconds': round(time.time() - started, 1),
        'config': cfg, 'inputs_sha256': cohort.input_hashes, 'notes': cohort.notes,
        'library_versions': {lib: version(lib) for lib in ['igraph', 'leidenalg', 'networkx', 'numpy',
                                                          'pandas', 'scikit-learn', 'scipy']},
        'summaries': {k: v['summary'] for k, v in results.items()},
        'description': 'Descriptive community and composite-score experiments; no causal or prescribing claim.'})
    print(f'KOL run written to {out.relative_to(root)} in {time.time() - started:.0f}s', flush=True)
    return out
