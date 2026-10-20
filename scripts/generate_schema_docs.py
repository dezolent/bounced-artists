"""Generate a reproducible schema dictionary and domain ER views from Django models."""
import os
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.local")
import django
django.setup()
from django.apps import apps
from django.db.models import ForeignKey, OneToOneField

ROOT = Path(__file__).resolve().parents[1]
DOMAINS = ("artists", "catalog", "rights", "events", "provenance", "publishing", "analytics")
SOURCES = {
    "artists": "Staff entry; artist supplied identities and website profiles",
    "catalog": "Verified Dezolent website bundle import and staff reconciliation; external catalog connectors are future",
    "rights": "Unverified website assertions, artist supplied documentation and future authorized society evidence",
    "events": "Future Bandsintown/EDMTrain connectors and staff entry",
    "provenance": "Verified website export snapshots and staff evidence; API, scraper and report connectors are future",
    "publishing": "Imported draft website content, staff approval and future publication worker",
    "analytics": "Future authorized reports and platform observations",
}
OWNERSHIP = {
    "artists.Artist": "organization FK; artist memberships grant private artist scope",
    "artists.Organization": "organization root; staff governed",
    "artists.OrganizationMembership": "organization FK; superuser administration",
    "artists.ArtistMembership": "artist FK; superuser administration",
    "artists.Service": "global reference",
    "catalog.Genre": "global reference",
    "catalog.PlatformObject": "global service listing; access through owned match/series",
    "rights.RightsSociety": "global reference",
    "rights.Party": "organization FK or unassigned public credit; legal name private",
}

lines = ["# Schema dictionary", "", "Generated from the Django models with `python scripts/generate_schema_docs.py`.", "New domain entities use UUID primary keys; previously deployed artist-related tables retain their original key types. `PK` and `FK` identify keys; Django indexes foreign keys by default. Logical ownership of a child follows its parent unless stated otherwise.", ""]
for domain in DOMAINS:
    lines += [f"## {domain}", "", f"Intended sources: {SOURCES[domain]}. See [status](status.md) for populated data and implemented integrations; table existence does not imply a running connector.", ""]
    for model in apps.get_app_config(domain).get_models():
        label = model._meta.label
        cols = []
        for field in model._meta.local_fields:
            kind = field.get_internal_type()
            detail = f"{field.column} {kind}"
            if field.primary_key: detail += " PK"
            if isinstance(field, (ForeignKey, OneToOneField)): detail += f" FK→{field.remote_field.model._meta.label} ({field.remote_field.on_delete.__name__})"
            if field.unique: detail += " UNIQUE"
            if field.null: detail += " NULL"
            cols.append(detail)
        constraints = "; ".join(c.name for c in model._meta.constraints) or "model field constraints only"
        indexes = "; ".join(i.name or ",".join(i.fields) for i in model._meta.indexes) or "FK/PK/unique indexes"
        owner = OWNERSHIP.get(label)
        if not owner:
            parent = next((f for f in model._meta.local_fields if isinstance(f, ForeignKey) and f.name in ("organization", "artist", "recording", "release", "work", "event", "website", "source_record", "target", "series", "page", "fanlink_page", "registration", "sync_job", "connection", "review_item")), None)
            owner = f"inherits from {parent.remote_field.model._meta.label} via {parent.name}" if parent else "staff governed; see architecture for access path"
        lines += [f"### {model.__name__} (`{model._meta.db_table}`)", "", f"Ownership: {owner}.", "", "| Column and type |", "| --- |"]
        lines += [f"| `{c}` |" for c in cols]
        lines += ["", f"Constraints: {constraints}. Indexes: {indexes}.", ""]
(ROOT / "docs/schema-dictionary.md").write_text("\n".join(lines))

out = ["# Entity relationships", "", "Physical tables use Django app prefixes and snake case model names. Each view includes all models in its domain; edges to another domain show foreign keys without implying shared access.", ""]
for domain in DOMAINS:
    out += [f"## {domain}", "", "```mermaid", "erDiagram"]
    models = list(apps.get_app_config(domain).get_models())
    for model in models:
        out.append(f"    {model.__name__.upper()} {{")
        pk = model._meta.pk
        pk_type = "uuid" if pk.get_internal_type() == "UUIDField" else "bigint" if pk.get_internal_type() == "BigAutoField" else pk.get_internal_type().lower()
        out.append(f"        {pk_type} {pk.column} PK")
        out.append("    }")
    for model in models:
        for field in model._meta.local_fields:
            if isinstance(field, ForeignKey):
                parent = field.remote_field.model.__name__.upper()
                child = model.__name__.upper()
                out.append(f'    {parent} ||--o{{ {child} : "{field.name}"')
    out += ["```", ""]
(ROOT / "docs/entity-relationships.md").write_text("\n".join(out))
print(f"Documented {sum(1 for d in DOMAINS for _ in apps.get_app_config(d).get_models())} domain models")
