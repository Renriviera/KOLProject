"""Where the agent tool's tracked design files and its local, rebuilt data pack live."""
from ..common import ROOT

AGENT = ROOT / 'agent'
PROMPTS = AGENT / 'prompts'
SKILLS = AGENT / 'skills'
SCHEMAS = AGENT / 'schemas'
REFERENCE = AGENT / 'reference'
WEB = AGENT / 'web'
# The pack is rebuilt from published snapshot tables and is not in Git: it holds address-level coordinates.
PACK = AGENT / 'pack'
DB = PACK / 'kol.sqlite'
CARDS = PACK / 'cards.jsonl'
MANIFEST = PACK / 'manifest.json'
GEO = PACK / 'geo'
PROCESSED = ROOT / 'Data' / 'processed'
SITE_RUN = ROOT / 'docs' / 'data' / 'site.json'
RAW_BULK = ROOT / 'Data' / 'raw' / '2026-10-24' / 'bulk'
