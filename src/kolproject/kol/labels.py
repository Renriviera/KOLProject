"""Readable community labels: stability tier, archetype, product lean, and title themes.

Every community gets a label, including ones that fail the stability tests. The
tier says how much weight the label can bear, and the reason column says why.
"""
from __future__ import annotations

import re

import numpy as np
import pandas as pd
from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS, CountVectorizer

# Products, with their trial and generic names, as they appear in paper titles.
PRODUCTS = {
    'axi-cel (Yescarta)': r'axicabtagene|axi-cel|yescarta|zuma-1|zuma-7|zuma-12',
    'liso-cel (Breyanzi)': r'lisocabtagene|liso-cel|breyanzi|transcend',
    'tisa-cel (Kymriah)': r'tisagenlecleucel|tisa-cel|kymriah|juliet|elara',
    'brexu-cel (Tecartus)': r'brexucabtagene|brexu-cel|tecartus|zuma-2',
}
# Terms present across the whole corpus, or product names already covered above.
DOMAIN_STOP = {
    'car', 'cars', 'cell', 'cells', 'lymphoma', 'lymphomas', 'chimeric', 'antigen', 'receptor', 'receptors',
    'therapy', 'therapies', 'therapeutic', 'patients', 'patient', 'large', 'b-cell', 't-cell', 'study', 'studies',
    'results', 'analysis', 'treatment', 'treated', 'treating', 'clinical', 'trial', 'trials', 'phase', 'based',
    'following', 'use', 'role', 'associated', 'outcomes', 'outcome', 'diffuse', 'non-hodgkin', 'relapsed',
    'refractory', 'dlbcl', 'lbcl', 'versus', 'vs', 'report', 'single', 'center', 'real-world', 'world', 'real',
    'retrospective', 'prospective', 'evaluation', 'impact', 'risk', 'response', 'responses', 'efficacy', 'safety',
    'axicabtagene', 'ciloleucel', 'lisocabtagene', 'maraleucel', 'tisagenlecleucel', 'brexucabtagene',
    'autoleucel', 'transcend', 'zuma-1', 'zuma-5', 'zuma-7', 'juliet', 'nhl', 'axi-cel', 'liso-cel', 'tisa-cel',
    'american', 'society', 'follow-up', 'multicenter', 'multicentre', 'multi-center', 'pilot', 'update',
    'experience', 'multiple', 'consortium', 'international', 'national', 'long-term', 'years',
    'don', 'clinic', 'cancer', 'early', 'progress', 'evolving', 'undergoing', 'practical', 'severe', 'tumor',
    'involvement', 'activation', 'car-t', 'anti-cd19', 'cd19', 'cd19-targeted', 'immunotherapy', 'cd19-directed',
}
STOP_WORDS = sorted(ENGLISH_STOP_WORDS | DOMAIN_STOP)

REGIONS = {
    'Northeast': {'CT', 'ME', 'MA', 'NH', 'RI', 'VT', 'NJ', 'NY', 'PA'},
    'Midwest': {'IL', 'IN', 'MI', 'OH', 'WI', 'IA', 'KS', 'MN', 'MO', 'NE', 'ND', 'SD'},
    'South': {'DE', 'DC', 'FL', 'GA', 'MD', 'NC', 'SC', 'VA', 'WV', 'AL', 'KY', 'MS', 'TN', 'AR', 'LA', 'OK', 'TX'},
    'West': {'AZ', 'CO', 'ID', 'MT', 'NV', 'NM', 'UT', 'WY', 'AK', 'CA', 'HI', 'OR', 'WA'},
}
STATE_REGION = {state: region for region, states in REGIONS.items() for state in states}

TIERS = ('firm', 'provisional', 'unstable', 'outside main network')


def region_summary(states: pd.Series) -> str:
    """Name the census region when it holds at least half the members, else 'multi-region'."""
    regions = states.map(STATE_REGION).fillna('Other').value_counts(normalize=True)
    return regions.index[0] if regions.iloc[0] >= 0.5 else 'multi-region'


def archetype(size: int, institution_purity: float, min_size: int) -> str:
    if size < min_size:
        return 'outside main network'
    if institution_purity >= 0.6:
        return 'single-institution orbit'
    if institution_purity >= 0.4:
        return 'anchored network'
    return 'multi-institution network'


def tier(size: int, consensus: float, drop_jaccard: float, min_size: int,
         consensus_threshold: float = 0.8, firm_jaccard: float = 0.75, provisional_jaccard: float = 0.6
         ) -> tuple[str, str]:
    """Stability tier and a plain-language reason. Missing bootstrap evidence counts as passing."""
    if size < min_size:
        return 'outside main network', f'only {size} member(s); too small to test'
    reasons = []
    if consensus < consensus_threshold:
        reasons.append(f'members change community across seeds (consensus {consensus:.2f})')
    if not np.isnan(drop_jaccard) and drop_jaccard < firm_jaccard:
        reasons.append(f'changes when 10% of papers are dropped (Jaccard {drop_jaccard:.2f})')
    if not reasons:
        return 'firm', 'stable across seeds and literature perturbation'
    if consensus >= consensus_threshold and (np.isnan(drop_jaccard) or drop_jaccard >= provisional_jaccard):
        return 'provisional', '; '.join(reasons)
    return 'unstable', '; '.join(reasons)


def _dedupe(terms: list[str], limit: int) -> list[str]:
    """Keep the strongest terms, skipping any that share a word with one already kept."""
    kept, words = [], set()
    for term in terms:
        tokens = set(term.split())
        if tokens & words:
            continue
        kept.append(term)
        words |= tokens
        if len(kept) == limit:
            break
    return kept


class TitleThemes:
    """Distinctive title terms and product lean per community, relative to the other communities.

    Log-odds with an informative Dirichlet prior (Monroe, Colaresi & Quinn 2008).
    A paper counts once for every community with a member among its authors.
    The background for a community is the pooled papers of all other communities,
    so terms common to the large multi-center papers that span many communities
    cancel out. Product and trial names are reported as a product lean, and
    themes describe topics.
    """

    def __init__(self, titles: pd.Series, communities: dict, prior_strength: float = 500.0, min_df: int = 4):
        index = {pmid: i for i, pmid in enumerate(titles.index)}
        vectorizer = CountVectorizer(ngram_range=(1, 2), min_df=min_df, stop_words=STOP_WORDS, binary=True,
                                     token_pattern=r'(?u)\b[a-zA-Z][a-zA-Z0-9\-]+\b')
        matrix = vectorizer.fit_transform(titles.fillna('').str.lower())
        self.terms = np.array(vectorizer.get_feature_names_out())
        lowered = titles.fillna('').str.lower()
        hits = np.column_stack([lowered.str.contains(pattern, regex=True).to_numpy()
                                for pattern in PRODUCTS.values()]).astype(float)
        self.rows = {label: sorted({index[p] for p in pmids if p in index}) for label, pmids in communities.items()}
        self.term_counts = {label: np.asarray(matrix[rows].sum(axis=0)).ravel().astype(float) if rows
                            else np.zeros(len(self.terms)) for label, rows in self.rows.items()}
        self.product_counts = {label: hits[rows].sum(axis=0) if rows else np.zeros(len(PRODUCTS))
                               for label, rows in self.rows.items()}
        self.pooled_terms = sum(self.term_counts.values())
        self.pooled_products = sum(self.product_counts.values())
        self.pooled_papers = sum(len(rows) for rows in self.rows.values())
        self.prior_strength = prior_strength

    def themes(self, label, limit: int = 4, min_z: float = 1.96) -> list[str]:
        inside = self.term_counts[label]
        if not inside.sum():
            return []
        outside = self.pooled_terms - inside
        prior = (self.pooled_terms + 1) / (self.pooled_terms + 1).sum() * self.prior_strength
        a0 = prior.sum()
        delta = (np.log((inside + prior) / (inside.sum() + a0 - inside - prior))
                 - np.log((outside + prior) / (outside.sum() + a0 - outside - prior)))
        z = delta / np.sqrt(1 / (inside + prior) + 1 / (outside + prior))
        order = [i for i in np.argsort(-z) if z[i] >= min_z and inside[i] >= 2]
        return _dedupe(list(self.terms[order]), limit)

    def product_lean(self, label, min_lift: float = 1.3, min_papers: int = 5) -> list[str]:
        papers = len(self.rows[label])
        other_papers = self.pooled_papers - papers
        if not papers or not other_papers:
            return []
        inside = self.product_counts[label]
        outside = self.pooled_products - inside
        out = []
        for i, name in enumerate(PRODUCTS):
            if inside[i] >= min_papers and outside[i] > 0:
                lift = (inside[i] / papers) / (outside[i] / other_papers)
                if lift >= min_lift:
                    out.append((lift, f'{name} {lift:.1f}x'))
        return [text for _, text in sorted(out, reverse=True)]


def community_name(tier_name: str, archetype_name: str, institutions: pd.Series, region: str,
                   member_names: list[str], degrees: list[int]) -> str:
    """Short label a reader recognizes, worded by archetype and tier."""
    top = institutions.index[0]
    second = institutions.index[1] if len(institutions) > 1 else None
    if archetype_name == 'outside main network':
        if max(degrees, default=0) == 0:
            who = member_names[0] if len(member_names) == 1 else f'{len(member_names)} HCPs'
            return f'Isolated: {who} ({top}), no coauthorship with the cohort'
        return f'Isolated pair at {top}, coauthors only with each other'
    if archetype_name in ('single-institution orbit', 'anchored network'):
        base = f'{top} orbit'
    else:
        base = f'{top} – {second} network' if second else f'{top} network'
        if region != 'multi-region':
            base += f' ({region})'
    return base if tier_name != 'unstable' else f'Loose {base}'
