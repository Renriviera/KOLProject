"""Load published cohort tables and the weak validation labels used by the KOL harness."""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
from pathlib import Path
import re

import pandas as pd

from ..common import ROOT, SNAPSHOT
from ..identity import HOLDOUT

# Payment natures that represent paid advisory, consulting, or speaking work.
ADVISORY_NATURES = frozenset({
    'Consulting Fee',
    'Honoraria',
    'Compensation for services other than consulting, including serving as faculty or as a speaker '
    'at a venue other than a continuing education program',
    'Compensation for serving as faculty or as a speaker for a medical education program',
})

# Canonical institution names, matched in order against the latest publication affiliation.
INSTITUTIONS = [
    ('Memorial Sloan Kettering', r'sloan.kettering|\bmskcc\b'),
    ('MD Anderson', r'm\.?\s?d\.? anderson'),
    ('Mayo Clinic', r'\bmayo\b'),
    ('Moffitt', r'moffitt'),
    ('Fred Hutch / UW', r'fred hutch|hutchinson cancer|university of washington|seattle cancer care'),
    ('Dana-Farber', r'dana.farber'),
    ('Mass General Brigham', r'massachusetts general|mass general|brigham'),
    ('Stanford', r'stanford'),
    ('Penn / Abramson', r'abramson|university of pennsylvania|perelman'),
    ("Children's Hospital of Philadelphia", r"children'?s hospital of philadelphia"),
    ('City of Hope', r'city of hope'),
    ('Cleveland Clinic', r'cleveland clinic|taussig'),
    ('Ohio State', r'ohio state'),
    ('Medical College of Wisconsin', r'medical college of wisconsin|froedtert'),
    ('Vanderbilt', r'vanderbilt'),
    ('WashU / Siteman', r'washington university|siteman'),
    ('Miami / Sylvester', r'university of miami|sylvester'),
    ('OHSU / Knight', r'knight cancer|oregon health'),
    ('Atrium / Levine', r'levine cancer|atrium health'),
    ('University of Colorado', r'university of colorado'),
    ('Baylor', r'baylor'),
    ('Maryland / Greenebaum', r'greenebaum|university of maryland'),
    ('Weill Cornell', r'cornell'),
    ('University of Minnesota', r'university of minnesota|masonic cancer'),
    ('University of Michigan', r'university of michigan|rogel'),
    ('Roswell Park', r'roswell park'),
    ('University of Chicago', r'university of chicago'),
    ('University of Kansas', r'university of kansas'),
    ('UNC / Lineberger', r'lineberger|university of north carolina'),
    ('Emory / Winship', r'emory|winship'),
    ('Northwestern / Lurie', r'northwestern|lurie'),
    ('Hackensack / Theurer', r'theurer|hackensack'),
    ('Huntsman', r'huntsman|university of utah'),
    ('Iowa', r'university of iowa'),
    ('UAB', r'alabama at birmingham'),
    ('UCSF', r'san francisco'),
    ('UCLA', r'\bucla\b|university of california,? los angeles'),
    ('Yale', r'\byale\b'),
    ('National Cancer Institute', r'national cancer institute'),
]
_GENERIC = re.compile(r'^(the )?(division|department|clinical research|program|section|center for|lymphoma service)', re.I)
_INSTITUTION_WORD = re.compile(r'universit|cancer center|cancer institute|hospital|clinic|institute|college|health|medical center', re.I)


def institution_label(affiliation: str) -> str:
    """Map a free-text affiliation to a short institution name a reader recognizes."""
    text = str(affiliation or '')
    for name, pattern in INSTITUTIONS:
        if re.search(pattern, text, re.I):
            return name
    for part in re.split(r'[;,]', text):
        part = part.strip()
        if part and not _GENERIC.match(part) and _INSTITUTION_WORD.search(part):
            return re.sub(r'^the ', '', part, flags=re.I)[:60]
    return 'Other / unresolved'


def sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@dataclass
class Cohort:
    """Published tables reshaped for graph and feature construction.

    hcps: one row per HCP, indexed and sorted by hcp_id.
    papers: included publications indexed by pmid.
    links: included HCP-publication authorships with a senior-author flag.
    payments: 2024 general payment records.
    labels: weak validation labels indexed by hcp_id.
    """
    hcps: pd.DataFrame
    papers: pd.DataFrame
    links: pd.DataFrame
    payments: pd.DataFrame
    labels: pd.DataFrame
    published_edges: pd.DataFrame
    input_hashes: dict = field(default_factory=dict)
    notes: list = field(default_factory=list)

    @property
    def nodes(self) -> list[str]:
        return list(self.hcps.index)


def load_cohort(root: Path | str = ROOT) -> Cohort:
    root = Path(root)
    base = root / 'Data' / 'processed' / SNAPSHOT
    files = {name: base / f'{name}.csv' for name in
             ['hcps', 'publications', 'authorships', 'coauthor_edges', 'general_payments_2024']}
    ids = {'npi': str, 'hcp_id': str, 'pmid': str, 'record_id': str}
    hcps = pd.read_csv(files['hcps'], dtype=ids).sort_values('hcp_id').set_index('hcp_id')
    publications = pd.read_csv(files['publications'], dtype=ids)
    authorships = pd.read_csv(files['authorships'], dtype=ids)
    payments = pd.read_csv(files['general_payments_2024'], dtype=ids)
    edges = pd.read_csv(files['coauthor_edges'], dtype=ids)

    papers = (publications[publications.analysis_include == 1]
              .set_index('pmid')[['publication_year', 'first_publication_date', 'author_count',
                                  'citation_year_percentile', 'journal']])
    links = authorships[authorships.analysis_include == 1][
        ['hcp_id', 'pmid', 'author_position', 'author_count']].copy()
    links['senior'] = (links.author_position == 1) | (links.author_position == links.author_count)
    links = links.merge(papers[['publication_year']], left_on='pmid', right_index=True,
                        how='left', validate='many_to_one')
    if links.publication_year.isna().any():
        raise ValueError('Included authorships reference publications missing from the included corpus.')
    if not set(links.hcp_id) <= set(hcps.index):
        raise ValueError('Authorships reference HCPs outside the cohort.')

    hcps['institution'] = hcps.latest_publication_affiliation.map(institution_label)

    notes = []
    labels = pd.DataFrame(index=hcps.index)
    # The 2023 ASTCT DLBCL guideline was withheld from every feature and edge at build time.
    panel = set(authorships.loc[authorships.pmid == HOLDOUT, 'hcp_id'])
    labels['guideline_panel'] = labels.index.isin(panel)
    advisors = payments.loc[(payments.lymphoma_cart_product_mentioned == 1)
                            & payments.payment_nature.isin(ADVISORY_NATURES), 'hcp_id']
    labels['cart_industry_advisor'] = labels.index.isin(set(advisors))
    legacy = root / 'Data' / 'legacy' / 'cohort_245' / 'data' / 'hcps.csv'
    if legacy.is_file():
        seeds = pd.read_csv(legacy, dtype={'npi': str})
        seed_ids = set('NPI' + seeds.loc[seeds.cohort_wave == 'original_seed', 'npi'])
        labels['curated_seed'] = labels.index.isin(seed_ids)
        notes.append(f'curated_seed: {len(seed_ids)} manually curated seeds in the local legacy pilot, '
                     f'{int(labels.curated_seed.sum())} present in the 500-HCP cohort.')
        files['legacy_hcps'] = legacy
    else:
        notes.append('curated_seed: local legacy pilot not present; label skipped.')

    return Cohort(hcps=hcps, papers=papers, links=links.reset_index(drop=True), payments=payments,
                  labels=labels, published_edges=edges,
                  input_hashes={str(p.relative_to(root)): sha256(p) for p in files.values()},
                  notes=notes)
