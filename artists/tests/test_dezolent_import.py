"""PostgreSQL integration coverage for the verified website bundle."""

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from collections import defaultdict

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase

from artists.imports.dezolent import DezolentImporter
from artists.imports.dezolent_bundle import Bundle
from artists.models import Artist, ArtistMembership, ArtistProfileLink
from catalog.models import Asset, Recording, RecordingIdentifier, ReleaseTrack
from provenance.models import ReviewItem, SourceRecord
from publishing.models import FanlinkPage, Page, PublicationOutbox, Website


BUNDLE_PATH = Path(os.environ.get("DEZOLENT_IMPORT_BUNDLE", "/Users/josephhill/GitHub/dezolent.com/exports/bounced-artists"))


@unittest.skipUnless(BUNDLE_PATH.is_dir(), "Set DEZOLENT_IMPORT_BUNDLE for full import tests")
class DezolentImportTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        call_command("seed_artists", verbosity=0)

    def test_complete_import_idempotency_and_preservation(self):
        bundle = Bundle(BUNDLE_PATH)
        dezolent = Artist.objects.get(bootstrap_key="dezolent")
        morbeato = Artist.objects.get(bootstrap_key="morbeato")
        baseline = (dezolent.pk, morbeato.pk, morbeato.management_status)
        user = get_user_model().objects.create_user(username="import-member", password="test-only-password")
        ArtistMembership.objects.create(artist=morbeato, user=user, role="viewer")

        dry = DezolentImporter(bundle).run()
        self.assertEqual(dry["conflicts"], [])
        self.assertEqual(dry["creates"]["catalog.Recording"], 38)
        self.assertEqual(SourceRecord.objects.count(), 0)
        self.assertFalse(Website.objects.filter(domain="dezolent.com").exists())

        applied = DezolentImporter(bundle).run(apply=True)
        self.assertEqual(applied["conflicts"], [])
        self.assertEqual(applied["source_snapshots"], 1156)
        self.assertEqual(Recording.objects.count(), 38)
        self.assertEqual(Page.objects.filter(website__domain="dezolent.com").count(), 89)
        self.assertEqual(FanlinkPage.objects.filter(website__domain="dezolent.com").count(), 40)
        self.assertEqual(ArtistProfileLink.objects.count(), 131)
        self.assertGreaterEqual(Asset.objects.filter(source_repository="https://github.com/dezolent/dezolent.com").count(), 104)
        self.assertEqual(SourceRecord.objects.filter(object_type="links").count(), 477)
        self.assertEqual(SourceRecord.objects.filter(object_type="source-archive").count(), 85)
        self.assertEqual(SourceRecord.objects.filter(object_type="blog-posts").count(), 3)
        self.assertEqual(SourceRecord.objects.filter(object_type="schema-nodes").count(), 303)
        variants = defaultdict(set)
        for row in bundle.rows["schema-nodes"]:
            value = row["value"]
            if isinstance(value, dict) and value.get("@id"):
                variants[value["@id"]].add(row["key"])
        self.assertTrue(any(len(hashes) > 1 for hashes in variants.values()))
        self.assertEqual(ReviewItem.objects.filter(issue_type__startswith="shared_").count(), 2)
        self.assertEqual(PublicationOutbox.objects.count(), 0)
        dezolent.refresh_from_db()
        morbeato.refresh_from_db()
        self.assertEqual((dezolent.pk, morbeato.pk, morbeato.management_status), baseline)
        self.assertEqual(ArtistMembership.objects.filter(artist=morbeato, user=user).count(), 1)

        website = Website.objects.get(domain="dezolent.com")
        ids = website.theme_config["dezolent_import"]["entity_ids"]
        gone = Recording.objects.get(pk=ids["recording:song:gone"])
        chasing = Recording.objects.get(pk=ids["recording:song:chasing-ghosts-remix"])
        self.assertNotEqual(gone.pk, chasing.pk)
        self.assertEqual(RecordingIdentifier.objects.filter(scheme="isrc", value="GBKQU1591272").count(), 2)
        empty_identifier_song = next(row for row in bundle.rows["songs"] if row["source"].get("isrc") == "")
        source_snapshot = SourceRecord.objects.get(object_type="songs", external_id=empty_identifier_song["key"])
        self.assertEqual(source_snapshot.payload_json["source"]["isrc"], "")
        self.assertFalse(RecordingIdentifier.objects.filter(recording_id=ids[f"recording:{empty_identifier_song['key']}"], scheme="isrc").exists())
        lyric_song = next(row for row in bundle.rows["songs"] if row["lyrics"] is not None)
        lyric_page = Page.objects.get(website=website, path=f"/tracks/{lyric_song['source']['id']}")
        self.assertEqual(lyric_page.content_json["dezolent_import"]["owner"]["lyrics"], lyric_song["lyrics"])
        blog = bundle.rows["blog-posts"][0]
        blog_page = Page.objects.get(website=website, path=f"/blog/{blog['source']['slug']['current']}")
        self.assertEqual(blog_page.content_json["dezolent_import"]["owner"]["source"]["body"], blog["source"]["body"])
        desolation = website.theme_config["dezolent_import"]["entity_ids"]["release:album:desolation-ep"]
        positions = list(ReleaseTrack.objects.filter(release_id=desolation).order_by("track_number").values_list("recording_id", flat=True))
        album = next(row for row in bundle.rows["albums"] if row["key"] == "album:desolation-ep")
        self.assertEqual(positions, [Recording.objects.get(pk=ids[f"recording:{key}"]).pk for key in (f"song:{song_id}" for song_id in album["source"]["songIds"])])
        self.assertEqual(DezolentImporter(bundle).run()["creates"], {})
        self.assertEqual(DezolentImporter(bundle).run()["updates"], {})

        # A changed editorial value is reported and never overwritten.
        gone.title = "Editorial title"
        gone.save()
        conflict = DezolentImporter(bundle).run()
        self.assertIn("recording:song:gone.title", conflict["conflicts"])
        with self.assertRaises(ValidationError):
            DezolentImporter(bundle).run(apply=True)
        gone.refresh_from_db()
        self.assertEqual(gone.title, "Editorial title")

    def test_checksum_failure_precedes_database_write(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            for file in BUNDLE_PATH.iterdir():
                if file.name == "manifest.json" or file.name in {entry["file"] for entry in json.loads((BUNDLE_PATH / "manifest.json").read_text())["files"]}:
                    shutil.copyfile(file, target / file.name)
            with (target / "songs.jsonl").open("ab") as handle:
                handle.write(b"\n")
            with self.assertRaises(ValidationError):
                Bundle(target)
        self.assertEqual(SourceRecord.objects.count(), 0)
