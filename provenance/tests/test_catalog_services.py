"""Catalog services must follow accepted customer identities, never names or ISRCs."""
from uuid import uuid4

from django.test import SimpleTestCase, TestCase
from django.utils import timezone

from artists.models import Artist, ArtistProfileLink, ArtistResourceGrant, Organization, Service
from catalog.models import PlatformObject, Recording, RecordingPlatformMatch
from portal.models import OperationRemoval
from provenance.models import Connection, SourceRecord, SyncJob, SyncRun


class CatalogServiceTransportTests(SimpleTestCase):
    def seed(self):
        return {"recording_id": str(uuid4()), "platform": "spotify", "external_id": "track123", "url": ""}

    def test_shazam_is_unsupported_without_network(self):
        from provenance.collectors.catalog_services import collect_catalog_service
        from provenance.collectors.http import CollectionError
        with self.assertRaisesRegex(CollectionError, "unsupported_platform"):
            list(collect_catalog_service("shazam", lambda *a, **k: self.fail("network"), [self.seed()]))

    def test_musixmatch_only_requests_lyrics_and_preserves_identity_and_restrictions(self):
        from provenance.collectors.catalog_services import collect_catalog_service
        calls = []
        seed = self.seed()
        def fetch(path, **params):
            calls.append((path, params))
            return {"track": {"id": 77, "external_ids": {"spotify_id": "track123"}},
                    "has_lyrics": True, "lyrics": {"is_restricted": True, "text": None}}
        finished = []
        rows = collect_catalog_service("musixmatch", fetch, [seed], seed_finished=finished.append)
        row = next(rows)
        self.assertEqual(finished, [])
        self.assertEqual(list(rows), [])
        self.assertEqual(finished, [seed])
        self.assertEqual(calls, [("/tracks/lyrics", {"track": "https://open.spotify.com/track/track123"})])
        self.assertEqual((row.endpoint, row.external_id), ("musixmatch-track-lyrics", "77"))
        self.assertTrue(row.payload["lyrics"]["is_restricted"])
        self.assertEqual(row.payload["_catalog_identity"]["external_ids"]["spotify_id"], "track123")

    def test_mismatched_spotify_identity_is_incomplete_and_budgets_abort(self):
        from provenance.collectors.catalog_services import collect_catalog_service
        from provenance.collectors.http import CollectionError
        rows = list(collect_catalog_service("musixmatch", lambda *a, **k: {
            "track": {"id": 77, "external_ids": {"spotify_id": "other"}}}, [self.seed()]))
        self.assertTrue(rows[0].incomplete)
        self.assertEqual(rows[0].payload["error_code"], "catalog_identity_mismatch")
        with self.assertRaisesRegex(CollectionError, "request_limit"):
            list(collect_catalog_service("musixmatch", lambda *a, **k: (_ for _ in ()).throw(CollectionError("request_limit")), [self.seed()]))


class CatalogServiceAuthorizationTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.get(slug="bounced-studio")
        self.artist = Artist.objects.create(organization=self.org, name="Customer", slug="service-customer", bootstrap_key="dezolent", management_status="managed")
        self.spotify, _ = Service.objects.get_or_create(slug="spotify", defaults={"name": "Spotify"})
        self.service, _ = Service.objects.get_or_create(slug="musixmatch", defaults={"name": "Musixmatch"})
        self.profile = ArtistProfileLink.objects.create(artist=self.artist, platform=self.spotify, external_id="artist123", url="https://open.spotify.com/artist/artist123", verification_status="verified")
        self.conn = Connection.objects.create(organization=self.org, artist=self.artist, service=self.service, status="active")
        self.job = SyncJob.objects.create(connection=self.conn, job_type="daily_public_platform", enabled=True, cursor_json={"seed_profile_link_id": str(self.profile.pk)})
        self.run = SyncRun.objects.create(sync_job=self.job)
        self.song = Recording.objects.create(organization=self.org, title="Existing song")
        self.grant = ArtistResourceGrant.objects.create(artist=self.artist, recording=self.song)
        self.obj = PlatformObject.objects.create(service=self.spotify, object_type="track", external_id="track123", url="https://open.spotify.com/track/track123")
        self.match = RecordingPlatformMatch.objects.create(recording=self.song, platform_object=self.obj, review_status="accepted")
        self.receipt = SourceRecord.objects.create(organization=self.org, service=self.spotify, collector="omkar", collection_method="scraper", object_type="track-details", external_id="track123", payload_hash="a" * 64, observed_at=timezone.now(), payload_json={"id": "track123", "artists": [{"id": "artist123"}]})

    def seeds(self):
        from provenance.collectors.catalog_services import catalog_seeds
        return catalog_seeds(self.job)

    def attach(self, payload=None):
        from provenance.collectors.catalog_services import attach_catalog_match
        from provenance.collectors.providers import Collected
        payload = payload or {"track": {"id": 77, "external_ids": {"spotify_id": "track123"}}, "_catalog_seed": {"recording_id": str(self.song.pk), "platform": "spotify", "external_id": "track123"}}
        source = SourceRecord.objects.create(organization=self.org, service=self.service, sync_run=self.run, collector="omkar", collection_method="scraper", object_type="musixmatch-track-details", external_id="77", payload_hash="b" * 64, observed_at=timezone.now(), payload_json=payload)
        obj, _ = PlatformObject.objects.get_or_create(service=self.service, object_type="track", external_id="77")
        return attach_catalog_match(self.conn, obj, source, Collected("musixmatch-track-details", "track", "77", payload))

    def test_accepted_scoped_seed_and_attach_are_idempotent(self):
        self.assertEqual(len(self.seeds()), 1)
        count = Recording.objects.count()
        self.assertTrue(self.attach())
        self.assertFalse(self.attach())
        self.assertEqual(Recording.objects.count(), count)
        match = RecordingPlatformMatch.objects.get(platform_object__service=self.service)
        self.assertEqual((match.recording, match.review_status, match.match_method), (self.song, "accepted", "verified_catalog_seed"))

    def test_stale_job_profile_and_grant_revocations_are_rechecked(self):
        for model, pk, values in [(SyncJob, self.job.pk, {"enabled": False}), (ArtistProfileLink, self.profile.pk, {"verification_status": "rejected"})]:
            before = model.objects.filter(pk=pk).values(*values).get()
            model.objects.filter(pk=pk).update(**values)
            self.assertEqual(self.seeds(), [])
            self.assertFalse(self.attach())
            model.objects.filter(pk=pk).update(**before)
        OperationRemoval.objects.create(organization=self.org, model_label="artists.artistresourcegrant", object_id=str(self.grant.pk))
        self.assertEqual(self.seeds(), [])
        self.assertFalse(self.attach())

    def test_rejected_removed_ambiguous_or_wrong_profile_seeds_are_excluded(self):
        self.match.review_status = "rejected"
        self.match.save()
        self.assertEqual(self.seeds(), [])
        self.match.review_status = "accepted"
        self.match.save()
        self.receipt.payload_json["artists"] = [{"id": "other"}]
        self.receipt.save()
        self.assertEqual(self.seeds(), [])
        self.receipt.payload_json["artists"] = [{"id": "artist123"}]
        self.receipt.save()
        other = Recording.objects.create(organization=self.org, title="Duplicate identity")
        RecordingPlatformMatch.objects.create(recording=other, platform_object=self.obj, review_status="accepted")
        self.assertEqual(self.seeds(), [])

    def test_provider_claim_cannot_redirect_seed_and_existing_decisions_survive(self):
        payload = {"track": {"id": 77, "external_ids": {"spotify_id": "elsewhere"}}, "_catalog_seed": {"recording_id": str(self.song.pk), "platform": "spotify", "external_id": "track123"}}
        self.assertFalse(self.attach(payload))
        self.assertTrue(self.attach())
        match = RecordingPlatformMatch.objects.get(platform_object__service=self.service)
        match.review_status = "rejected"
        match.save()
        self.assertFalse(self.attach())
        match.refresh_from_db()
        self.assertEqual(match.review_status, "rejected")

    def test_removed_native_alias_and_preview_revalidation_preserve_decisions(self):
        from provenance.collectors.catalog_services import validated_recording
        alias = PlatformObject.objects.create(service=self.service, object_type="recording", external_id="77")
        prior = RecordingPlatformMatch.objects.create(recording=self.song, platform_object=alias, review_status="rejected")
        self.assertFalse(self.attach())
        prior.delete()
        self.assertTrue(self.attach())
        obj = PlatformObject.objects.get(service=self.service, object_type="track", external_id="77")
        source = SourceRecord.objects.filter(service=self.service).latest("created_at")
        self.assertEqual(validated_recording(self.conn, obj, source), self.song)
        SyncJob.objects.filter(pk=self.job.pk).update(enabled=False)
        self.assertIsNone(validated_recording(self.conn, obj, source))

    def test_malformed_profile_cursor_and_collaborator_are_not_targets(self):
        from provenance.collectors.catalog_services import seed_profile
        SyncJob.objects.filter(pk=self.job.pk).update(cursor_json={"seed_profile_link_id": "not-a-pk"})
        self.assertIsNone(seed_profile(self.job))
        SyncJob.objects.filter(pk=self.job.pk).update(cursor_json={"seed_profile_link_id": str(self.profile.pk)})
        Artist.objects.filter(pk=self.artist.pk).update(bootstrap_key="guest")
        self.assertEqual(self.seeds(), [])

    def test_validation_rechecks_only_requested_seed_attribution(self):
        from unittest.mock import patch
        from provenance.collectors.catalog_services import _attributed
        for index in range(12):
            other = Recording.objects.create(organization=self.org, title=f"Other {index}")
            ArtistResourceGrant.objects.create(artist=self.artist, recording=other)
            obj = PlatformObject.objects.create(service=self.spotify, object_type="track", external_id=f"other{index}")
            RecordingPlatformMatch.objects.create(recording=other, platform_object=obj, review_status="accepted")
        with patch("provenance.collectors.catalog_services._attributed", wraps=_attributed) as attributed:
            self.assertTrue(self.attach())
        self.assertEqual(attributed.call_count, 1)
        self.assertEqual(attributed.call_args.args[1], "track123")

    def test_targeted_seed_includes_rejected_url_only_alias(self):
        from provenance.collectors.catalog_services import catalog_seeds
        alias = PlatformObject.objects.create(service=self.spotify, object_type="recording", url="https://open.spotify.com/track/track123?si=share")
        RecordingPlatformMatch.objects.create(recording=self.song, platform_object=alias, review_status="rejected")
        self.assertEqual(catalog_seeds(self.job, recording_id=str(self.song.pk), external_id="track123"), [])
        self.assertFalse(self.attach())

    def test_paused_preview_mode_retains_all_other_authorization_gates(self):
        from provenance.collectors.catalog_services import validated_recording
        self.assertTrue(self.attach())
        obj = PlatformObject.objects.get(service=self.service, object_type="track", external_id="77")
        source = SourceRecord.objects.filter(service=self.service).latest("created_at")
        SyncJob.objects.filter(pk=self.job.pk).update(enabled=False)
        self.assertIsNone(validated_recording(self.conn, obj, source))
        self.assertEqual(validated_recording(self.conn, obj, source, allow_paused=True), self.song)
        native_match = RecordingPlatformMatch.objects.get(platform_object=obj)
        cases = [
            (RecordingPlatformMatch, native_match.pk, {"review_status": "rejected"}),
            (ArtistProfileLink, self.profile.pk, {"verification_status": "rejected"}),
            (Connection, self.conn.pk, {"status": "inactive"}),
        ]
        for model, pk, values in cases:
            previous = model.objects.filter(pk=pk).values(*values).get()
            model.objects.filter(pk=pk).update(**values)
            self.assertIsNone(validated_recording(self.conn, obj, source, allow_paused=True))
            model.objects.filter(pk=pk).update(**previous)
        OperationRemoval.objects.create(organization=self.org, model_label="artists.artistresourcegrant", object_id=str(self.grant.pk))
        self.assertIsNone(validated_recording(self.conn, obj, source, allow_paused=True))
