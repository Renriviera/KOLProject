# Published data

Processed files are copied byte-for-byte from the complete original local release.
Source collection dates, service years, source publication dates, and computed
timestamps are separate fields; directory dates label snapshots, not service years.

## Tables

| File | Grain / role |
|---|---|
| `processed/2026-10-09/hcps.csv` | 500 selected HCPs, one row per verified individual NPI |
| `processed/2026-10-09/publications.csv` | 3,363 corpus publications, keyed by PMID; eligibility flag retained |
| `processed/2026-10-09/authorships.csv` | 4,991 HCP–publication links with identity evidence |
| `processed/2026-10-09/coauthor_edges.csv` | 9,378 observed undirected coauthorship edges |
| `processed/2026-10-09/general_payments_2024.csv` | 6,445 CMS general-payment records; industry transfers, not patient copays |
| `processed/2026-10-09/payment_query_status.csv` | One query-status row per selected HCP |
| `processed/2026-10-09/*review*.csv`, `*screening.csv`, `*aliases.csv`, `*crosswalk.csv`, `*changes.csv`, `qualified_reserve.csv`, `withheld_authorships.csv` | Selection, identity, reserve, and historical linkage audits |
| `processed/2026-10-09/social_profiles.csv` | Four dated public-search observations; no follower/engagement metrics |
| `processed/2026-10-15/cms_provider_service.csv` | 3,318 published NPI × service-year × HCPCS × setting rows |
| `processed/2026-10-15/hcp_utilization_features.csv` | 500 HCP-level utilization feature rows, including unobserved HCPs |

Metadata JSON files accompany both snapshots. Field definitions are in
`manifests/data_dictionary.csv` and
`manifests/2026-10-15/utilization_data_dictionary.json`. See
[`UTILIZATION_README.md`](UTILIZATION_README.md) for code definitions and missingness.

## Join keys and interpretation

Store `npi`, `hcp_id`, `pmid`, `record_id`, and `hcpcs_code` as strings. The stable
HCP key is `NPI` followed by the ten-digit NPI. Join authorships to HCPs on `hcp_id`
and publications on `pmid`. Join HCP utilization features on both `hcp_id` and `npi`
with a one-to-one cardinality check. Detailed service rows have a different grain.

Use `uv run kolproject-evaluate` to check the published tables and record their
hashes. It does not call upstream APIs or modify tables. Raw responses, intermediate
caches, legacy seeds, and source request inventories remain local and ignored.
The full collection pipeline requires those inputs and populated identity review
configuration under ignored `configs/local/`. They remain in the same consolidated
working folder as the tracked code. Third-party source content retains its original terms.
