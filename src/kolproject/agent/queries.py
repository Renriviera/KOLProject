"""Named queries: the only way numbers reach the agent and the interface.

A query is a fixed SQL function with typed parameters, registered with `@query(...)` in one of the
`queries_*` modules. `run` validates the parameters, runs it read-only, and returns rows with labelled
columns plus a short preview for the model. There is no free SQL and no code execution.
"""
from __future__ import annotations

import importlib

from .packbase import SCOPES, connect, scope_label

REGISTRY = {}
PARTS = ['queries_people', 'queries_money', 'queries_claims', 'queries_referral']
PREVIEW_ROWS = 25
MAX_ROWS = 2000
UNITS = {'usd', 'count', 'share', 'year', 'month', 'text', 'rank', 'id', 'days', 'index'}
BRANDS = ['Kymriah', 'Yescarta', 'Tecartus', 'Breyanzi', 'Abecma', 'Carvykti', 'Aucatzyl']
PARAM_KINDS = {
    'scope': {'type': 'string', 'enum': list(SCOPES)},
    'id': {'type': 'string'},
    'npi': {'type': 'string'},
    'ccn': {'type': 'string'},
    'community_id': {'type': 'integer'},
    'year': {'type': 'integer', 'minimum': 2009, 'maximum': 2026},
    'n': {'type': 'integer', 'minimum': 1, 'maximum': 50},
    'brand': {'type': 'string', 'enum': BRANDS},
    'text': {'type': 'string'},
}


# The heading of each query's standard panel. Words in braces are filled from the parameters; when a heading
# needs a parameter that was left out, the next one in the tuple is used. The entity is named under the heading.
TITLES = {
    'community_summary': 'The community at a glance',
    'community_compare': 'The two communities side by side',
    'community_leaders': 'Leaders by PageRank in the co-authorship network',
    'community_members': '{division_group} of the community',
    'community_centers': 'Centers attached to the community',
    'community_coauthor_by_year': 'Papers and co-author pairs by year',
    'hcp_profile': 'Profile',
    'hcp_publications_by_year': 'Papers by year',
    'hcp_top_coauthors': 'Most frequent co-authors',
    'center_profile': 'Center profile',
    'center_core_hcps': 'Core HCPs linked to the center',
    'centers_in_state': 'CAR-T centers in {state}',
    'data_inventory': 'What the database holds',
    'payments_by_year': 'Industry payments by year',
    'payments_by_product': ('Payments naming {brand} by year', 'Payments by product and year'),
    'payments_by_manufacturer': 'Industry payments by company',
    'payments_monthly': ('Payments naming {brand} by month', 'Industry payments by month'),
    'top_paid_hcps': ('Highest-paid HCPs, {nature}, {year}', 'Highest-paid HCPs, {nature}, 2019 to 2025'),
    'research_by_year': 'Research payment records by year',
    'research_studies': 'Largest research studies',
    'ownership': 'Ownership and investment interests',
    'ties_by_year': 'Conflict-of-interest disclosures on papers by year',
    'ties_by_manufacturer': 'CAR-T manufacturers named in disclosures',
    'hcp_ties_papers': 'Papers naming the HCP with a CAR-T manufacturer',
    'ties_vs_payments': 'Disclosed ties beside reported payments',
    'product_facts': ('{brand}: product facts', 'Approved CAR-T products'),
    'product_events': ('{brand}: approvals and later indications', 'CAR-T approvals and coding events'),
    'partd_by_drug_year': 'Medicare Part D claims for oral lymphoma drugs',
    'top_prescribers': 'Top Medicare Part D prescribers, {drug}, {year}',
    'partb_markers_by_year': 'HCPs billing lymphoma infusions to Medicare Part B',
    'partb_payment_by_year': 'Medicare Part B payment: drugs and other services',
    'medicaid_by_year': 'Medicaid paid amounts by year',
    'center_medicare_cart_stays': 'Medicare CAR-T inpatient stays by year',
    'community_medicare_cart_stays': 'Medicare CAR-T inpatient stays at the community\'s centers',
    'top_centers_by_medicare_stays': ('Centers in {state} by Medicare CAR-T stays, {year}', 'Centers by Medicare CAR-T stays, {year}'),
    'national_spending_by_brand_year': 'National {program} spending on CAR-T by product',
    'cart_admin_by_state_year': 'Medicare Part B CAR-T administration by state',
    'prescribing_summary': 'Medicare and Medicaid figures, {year}',
    'center_referrer_base_by_year': 'Shared Medicare patients by year',
    'center_referral_summary': 'Shared-patient base, {year}',
    'center_top_referrers': 'HCPs with the most shared patients, {year}',
    'center_referrers_by_region': 'Referring HCPs by region, {year}',
    'center_referrers_by_division': 'Shared patients by division, {year}',
    'center_core_hcp_senders': 'HCPs sharing patients with the center\'s core HCPs, {year}',
    'hcp_referral_links': 'Centers sharing patients with this HCP',
    'hcp_referral_summary': 'Shared-patient links at a glance',
    'hcp_referral_by_year': 'Shared patients with centers by year',
    'hcp_core_links': 'Shared patients with core HCPs by year',
    'community_referral_flows': 'Shared patients at the community\'s centers, {year}',
    'community_referral_by_year': 'Shared Medicare patients by year',
    'referral_reach_by_group': 'HCPs sharing patients with centers, by division',
    'referral_flows_by_group': 'Shared patients by division and year',
    'top_centers_by_shared_patients': ('Centers in {state} by shared patients, {year}', 'Centers by shared patients, {year}'),
}
TITLE_WORDS = {'core': 'Core members', 'affiliates': 'Affiliates', 'referring': 'Referring HCPs', 'other': 'Other HCPs',
               'services': 'consulting and speaking', 'hospitality_education': 'meals, travel and education', 'all': 'all kinds',
               'medicare_part_b': 'Medicare Part B', 'medicaid': 'Medicaid'}


def panel_title(name, params):
    """The heading of a query's standard panel for these parameters."""
    entry = TITLES.get(name) or name.replace('_', ' ').capitalize()
    words = {k: TITLE_WORDS.get(v, v) for k, v in params.items() if v not in (None, '')}
    if name == 'top_prescribers' and params.get('drug') == 'all':
        words['drug'] = 'all seven drugs'
    for template in entry if isinstance(entry, tuple) else (entry,):
        try:
            return template.format(**words)
        except KeyError:
            continue
    return name.replace('_', ' ').capitalize()


def entity_label(con, values):
    """Who a result is about, for the line under a panel heading; None for national results."""
    if 'scope' in values and 'id' in values:
        return scope_label(con, values['scope'], values['id'])
    if 'community_id' in values:
        ids = [values['community_id']] + ([values['other_community_id']] if 'other_community_id' in values else [])
        names = [r[0] for i in ids for r in con.execute('select short_label from dim_community where community_id = ?', (i,))]
        return ' and '.join(names) or None
    if 'ccn' in values:
        return scope_label(con, 'center', values['ccn'])
    if 'npi' in values:
        return scope_label(con, 'hcp', values['npi'])
    return None


# Queries a profile skill may also run. The model is told to choose the skill by what is asked (payments,
# prescribing, ties, shared patients), but it sometimes opens the profile of the community, hospital or person the
# question is about instead. With these it can still show the right view from there, rather than the wrong one or a
# claim that the data is missing.
TOPIC_BY_SCOPE = ['payments_by_year', 'payments_by_product', 'payments_by_manufacturer', 'payments_monthly', 'top_paid_hcps',
                  'research_by_year', 'research_studies', 'ownership', 'ties_by_year', 'ties_by_manufacturer', 'ties_vs_payments',
                  'partd_by_drug_year', 'top_prescribers', 'partb_markers_by_year', 'partb_payment_by_year', 'medicaid_by_year']
ALSO = {
    'community-view': TOPIC_BY_SCOPE + ['community_referral_by_year'],
    'center-profile': TOPIC_BY_SCOPE + ['center_referral_summary', 'center_referrer_base_by_year', 'center_top_referrers',
                                        'center_referrers_by_region', 'center_referrers_by_division', 'center_core_hcp_senders',
                                        'top_centers_by_shared_patients'],
    'hcp-profile': [q for q in TOPIC_BY_SCOPE if q not in ('top_paid_hcps', 'top_prescribers')]
                   + ['hcp_referral_summary', 'hcp_referral_by_year', 'hcp_core_links'],
}
TOPICS = {'industry-payments': 'Payments', 'disclosed-ties': 'Disclosed ties', 'prescribing-claims': 'Prescribing and claims',
          'referral-flow': 'Shared patients'}


def home(name):
    """The skill a query belongs to first: the topic its view is about."""
    return REGISTRY[name]['skills'][0] if name in REGISTRY else None


def allowed(name, skill):
    """Whether a skill may run a query: it lists the query, or it is a profile skill and the query is one of its extras."""
    return skill in REGISTRY[name]['skills'] or name in ALSO.get(skill, ())


def extras(skill):
    """The queries a profile skill may run beyond its own, as specs without the functions."""
    load_parts()
    return [{k: v for k, v in REGISTRY[name].items() if k != 'function'} for name in ALSO.get(skill, [])
            if name in REGISTRY and skill not in REGISTRY[name]['skills']]


class QueryError(ValueError):
    """A problem the model can act on: an unknown query, a bad parameter, or an id that does not exist."""


def query(name, description, params, columns, skills, default_panel=None, caveats=(), tables=(), years=''):
    """Register a named query.

    params: {name: kind or dict}. A kind is a key of PARAM_KINDS; a dict is a JSON-schema fragment (use it for
      enums such as {'type': 'string', 'enum': ['cart_only', 'lymphoma_only'], 'default': 'lymphoma_only'}).
      Add 'default' to make a parameter optional.
    columns: [(column, label, unit)] in display order; unit is one of UNITS. Rows must carry exactly these keys.
    skills: skill names allowed to call it. default_panel: {'type': ..., 'x': ..., 'y': [...], 'split_by': ...}.
    caveats: caveat codes the interface must print. tables: pack tables read. years: for the source line.
    The function receives (con, **params) and returns a list of dict rows, already ordered.
    """
    def decorate(function):
        assert name not in REGISTRY, f'Duplicate query {name}'
        spec = {}
        for pname, kind in params.items():
            spec[pname] = dict(PARAM_KINDS[kind]) if isinstance(kind, str) else dict(kind)
        assert all(unit in UNITS for _, _, unit in columns), f'{name}: unknown unit'
        REGISTRY[name] = {'name': name, 'description': description, 'params': spec,
                          'columns': [{'name': c, 'label': label, 'unit': unit} for c, label, unit in columns],
                          'skills': list(skills), 'default_panel': default_panel, 'caveats': list(caveats),
                          'tables': list(tables), 'years': years, 'function': function}
        return function
    return decorate


def load_parts():
    """Import every part module that exists, so its queries register."""
    for part in PARTS:
        try:
            importlib.import_module(f'{__package__}.{part}')
        except ModuleNotFoundError as error:
            if part not in str(error):
                raise
    return REGISTRY


def catalog(skill=None):
    """Query descriptions without the functions, optionally for one skill."""
    load_parts()
    return [{k: v for k, v in spec.items() if k != 'function'} for spec in REGISTRY.values()
            if skill is None or skill in spec['skills']]


def clean_params(spec, params):
    """Validated parameters with defaults applied; raises QueryError with a message the model can use."""
    params = dict(params or {})
    unknown = sorted(set(params) - set(spec['params']))
    if unknown:
        raise QueryError(f"Unknown parameter {unknown[0]} for {spec['name']}. Allowed: {', '.join(spec['params']) or 'none'}.")
    out = {}
    for name, rule in spec['params'].items():
        if name not in params or params[name] is None or params[name] == '':
            if 'default' in rule:
                out[name] = rule['default']
                continue
            raise QueryError(f"{spec['name']} needs the parameter {name}.")
        value = params[name]
        if rule['type'] == 'integer':
            try:
                value = int(value)
            except (TypeError, ValueError):
                raise QueryError(f'{name} must be a whole number.') from None
            if value < rule.get('minimum', value) or value > rule.get('maximum', value):
                raise QueryError(f"{name} must be between {rule['minimum']} and {rule['maximum']}.")
        else:
            value = str(value).strip()
        if 'enum' in rule and value not in rule['enum']:
            raise QueryError(f"{name} must be one of: {', '.join(map(str, rule['enum']))}.")
        out[name] = value
    return out


def run(name, params=None, con=None, skill=None):
    """Run one named query and return its rows, labelled columns and a preview."""
    load_parts()
    if name not in REGISTRY:
        raise QueryError(f'There is no query named {name}.')
    spec = REGISTRY[name]
    if skill is not None and not allowed(name, skill):
        raise QueryError(f"{name} is not part of the skill {skill}. Use one of: "
                         + ', '.join(sorted(n for n in REGISTRY if allowed(n, skill))) + '.')
    values = clean_params(spec, params)
    own = con is None
    con = con or connect(readonly=True)
    try:
        if 'scope' in values and 'id' in values and scope_label(con, values['scope'], values['id']) is None:
            raise QueryError(f"No {values['scope']} with id {values['id']}. Call find_entity first.")
        rows = spec['function'](con, **values)
        rows = [dict(row) for row in rows][:MAX_ROWS]
        names = [c['name'] for c in spec['columns']]
        for row in rows[:3]:
            missing = [c for c in names if c not in row]
            assert not missing, f'{name}: rows lack {missing}'
        rows = [{c: row[c] for c in names} for row in rows]
        label = entity_label(con, values)
    finally:
        if own:
            con.close()
    return {'name': name, 'params': values, 'scope_label': label, 'title': panel_title(name, values), 'columns': spec['columns'], 'rows': rows,
            'row_count': len(rows), 'preview': rows[:PREVIEW_ROWS], 'default_panel': spec['default_panel'],
            'caveats': spec['caveats'], 'source': {'tables': spec['tables'], 'years': spec['years']}}


__all__ = ['ALSO', 'BRANDS', 'MAX_ROWS', 'PREVIEW_ROWS', 'QueryError', 'REGISTRY', 'TITLES', 'TOPICS', 'allowed', 'catalog',
           'clean_params', 'entity_label', 'extras', 'home', 'load_parts', 'panel_title', 'query', 'run']
