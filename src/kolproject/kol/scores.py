"""Explainable composite scores: named axes of percentile features with stated weights."""
from __future__ import annotations

import numpy as np
import pandas as pd


def percentile(values: pd.Series) -> pd.Series:
    """Cohort percentile in (0, 1); missing values rank lowest, ties share the average rank."""
    filled = values.astype(float).fillna(-np.inf)
    return (filled.rank(method='average') - 0.5) / len(filled)


def axis_table(features: pd.DataFrame, axes: dict) -> pd.DataFrame:
    """Each axis is the mean percentile of its component features."""
    out = {}
    for axis, components in axes.items():
        missing = [c for c in components if c not in features]
        if missing:
            raise KeyError(f'Axis {axis!r} uses unknown features {missing}.')
        out[axis] = pd.concat([percentile(features[c]) for c in components], axis=1).mean(axis=1)
    return pd.DataFrame(out, index=features.index)


def validate_weights(weights: dict, axes) -> dict:
    weights = {k: float(v) for k, v in weights.items()}
    unknown = set(weights) - set(axes)
    if unknown:
        raise KeyError(f'Weights reference unknown axes {sorted(unknown)}.')
    if any(w < 0 for w in weights.values()):
        raise ValueError('Composite weights must be nonnegative.')
    if abs(sum(weights.values()) - 1) > 1e-9:
        raise ValueError(f'Composite weights must sum to one, got {sum(weights.values()):.6f}.')
    return weights


def renormalize(weights: dict, drop=()) -> dict:
    kept = {k: v for k, v in weights.items() if k not in drop and v > 0}
    total = sum(kept.values())
    if total <= 0:
        raise ValueError('No positive weight remains after dropping axes.')
    return {k: v / total for k, v in kept.items()}


def composite(axes: pd.DataFrame, weights: dict) -> pd.DataFrame:
    """Score on 0-100 with one contribution column per axis; contributions sum to the score."""
    weights = validate_weights(weights, axes.columns)
    contributions = pd.DataFrame({f'contrib_{k}': 100 * w * axes[k] for k, w in weights.items()},
                                 index=axes.index)
    out = contributions.copy()
    out['score'] = contributions.sum(axis=1)
    out['rank'] = rank_of(out.score)
    return out


def rank_of(score: pd.Series) -> pd.Series:
    """Rank 1 = best; ties broken by hcp_id for reproducibility, as in the baseline model."""
    order = sorted(score.index, key=lambda i: (-score[i], i))
    return pd.Series(np.arange(1, len(order) + 1), index=order).reindex(score.index)


def top_set(score: pd.Series, k: int) -> set:
    return set(rank_of(score).nsmallest(k).index)


def overlap(a: pd.Series, b: pd.Series, k: int) -> float:
    return len(top_set(a, k) & top_set(b, k)) / k
