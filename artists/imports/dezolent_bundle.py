"""Read a Dezolent export without trusting filenames or unverified contents."""

import hashlib
import json
from collections import Counter
from pathlib import Path

from django.core.exceptions import ValidationError


ROW_FILES = {
    "artists", "songs", "albums", "genres", "platforms", "links", "pages",
    "schema-nodes", "blog-posts", "assets", "source-archive",
}
SINGLE_FILES = {"site.json", "sanity-snapshot.json", "validation.json"}
EXPECTED_FILES = {f"{name}.jsonl" for name in ROW_FILES} | SINGLE_FILES


def canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def payload_hash(value):
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


class Bundle:
    def __init__(self, path):
        self.path = Path(path).expanduser().resolve(strict=True)
        if not self.path.is_dir():
            raise ValidationError("Bundle path must be a directory.")
        manifest_bytes = (self.path / "manifest.json").read_bytes()
        self.manifest = json.loads(manifest_bytes)
        if self.manifest.get("format") != "dezolent-bounced-import" or self.manifest.get("version") != 1:
            raise ValidationError("Unsupported Dezolent bundle manifest.")
        entries = self.manifest.get("files", [])
        if {entry.get("file") for entry in entries} != EXPECTED_FILES or len(entries) != len(EXPECTED_FILES):
            raise ValidationError("Manifest file inventory is incomplete or duplicated.")
        self.rows = {}
        self.singles = {}
        for entry in entries:
            name = entry["file"]
            file_path = self.path / name
            if not file_path.is_file() or file_path.is_symlink() or file_path.resolve().parent != self.path:
                raise ValidationError(f"Bundle file is missing or unsafe: {name}")
            data = file_path.read_bytes()
            if len(data) != entry["bytes"] or hashlib.sha256(data).hexdigest() != entry["sha256"]:
                raise ValidationError(f"Bundle checksum mismatch: {name}")
            if name.endswith(".jsonl"):
                records = [json.loads(line) for line in data.decode("utf-8").splitlines() if line]
                if len(records) != entry["rows"]:
                    raise ValidationError(f"Bundle row count mismatch: {name}")
                self.rows[name.removesuffix(".jsonl")] = records
            else:
                self.singles[name] = json.loads(data)
        if self.singles["validation.json"].get("passed") is not True:
            raise ValidationError("Export validation did not pass.")
        counts = self.manifest["counts"]
        for name in ROW_FILES:
            count_key = {"source-archive": "source_files", "schema-nodes": "schema_nodes", "blog-posts": "blog_posts"}.get(name, name)
            if len(self.rows[name]) != counts[count_key]:
                raise ValidationError(f"Manifest count mismatch: {name}")
        for name in ("artists", "songs", "albums", "genres", "platforms", "links", "pages", "blog-posts", "assets"):
            keys = [row["key"] for row in self.rows[name]]
            if len(keys) != len(set(keys)):
                raise ValidationError(f"Duplicate source keys: {name}")
        if Counter(row["kind"] for row in self.rows["links"])["streaming"] != counts["streaming_links"]:
            raise ValidationError("Streaming-link count mismatch.")
        if Counter(row["kind"] for row in self.rows["links"])["profile"] != counts["profile_links"]:
            raise ValidationError("Profile-link count mismatch.")
        if sum(len(row["source"]["songIds"]) for row in self.rows["albums"]) != counts["album_tracks"]:
            raise ValidationError("EP-track count mismatch.")
        if sum(row["lyrics"] is not None for row in self.rows["songs"]) != counts["lyrics"]:
            raise ValidationError("Lyrics count mismatch.")
        if sum("fanlink" in row["page_type"] for row in self.rows["pages"]) != counts["fanlink_pages"]:
            raise ValidationError("Fanlink-page count mismatch.")
        self.schema_nodes = {row["key"]: row["value"] for row in self.rows["schema-nodes"]}
        if len(self.schema_nodes) != counts["schema_nodes"]:
            raise ValidationError("Duplicate schema node hashes.")
        for row in self.rows["schema-nodes"]:
            source_json = json.dumps(row["value"], separators=(",", ":"), ensure_ascii=False)
            if hashlib.sha256(source_json.encode("utf-8")).hexdigest() != row["key"]:
                raise ValidationError("Schema node content hash mismatch.")
        song_keys = {row["key"] for row in self.rows["songs"]}
        for album in self.rows["albums"]:
            if any(f"song:{song_id}" not in song_keys for song_id in album["source"]["songIds"]):
                raise ValidationError("EP references an unknown song.")
        self.manifest_hash = hashlib.sha256(manifest_bytes).hexdigest()

    def schema_documents(self, page):
        documents = []
        refs = ([page["jsonld"]] if page.get("jsonld") else []) + (page.get("jsonld_documents") or [])
        for ref in refs:
            nodes = [self.schema_nodes[key] for key in ref["node_keys"]]
            if ref["shape"] == "single":
                if len(nodes) != 1:
                    raise ValidationError("Single schema document must have one node.")
                documents.append(nodes[0])
            elif ref["shape"] == "graph":
                documents.append({"@context": ref["context"], "@graph": nodes})
            else:
                raise ValidationError("Unknown schema document shape.")
        return documents
