"""TikTok sounds attach only through scoped, accepted streaming identities."""
from uuid import uuid4

from django.test import TestCase
from django.utils import timezone

from artists.models import Artist, ArtistProfileLink, ArtistResourceGrant, Organization, Service
from catalog.models import PlatformObject, Recording, RecordingPlatformMatch
from portal.models import OperationRemoval
from provenance.collectors.providers import Collected
from provenance.models import Connection, SourceRecord, SyncJob, SyncRun


class TikTokCatalogTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.get(slug="bounced-studio")
        self.artist = Artist.objects.create(organization=self.org, name="Customer", slug="tiktok-customer", bootstrap_key="dezolent", management_status="managed")
        self.service, _ = Service.objects.get_or_create(slug="tiktok", defaults={"name": "TikTok"})
        self.spotify, _ = Service.objects.get_or_create(slug="spotify", defaults={"name": "Spotify"})
        self.profile = ArtistProfileLink.objects.create(artist=self.artist, platform=self.service, external_id="12345", url="https://www.tiktok.com/@customer", verification_status="verified")
        self.conn = Connection.objects.create(organization=self.org, artist=self.artist, service=self.service, status="active")
        self.job = SyncJob.objects.create(connection=self.conn, job_type="daily_public_platform", enabled=True, cursor_json={"profile_link_id": str(self.profile.pk)})
        self.run = SyncRun.objects.create(sync_job=self.job)
        self.song = self.recording("Existing song")
        self.spotify_object = PlatformObject.objects.create(service=self.spotify, object_type="track", external_id="track123", url="https://open.spotify.com/track/track123")
        self.match = RecordingPlatformMatch.objects.create(recording=self.song, platform_object=self.spotify_object, review_status="accepted")
        self.music = PlatformObject.objects.create(service=self.service, object_type="music", external_id="777777", url="https://www.tiktok.com/music/song-777777")
        self.payload = {"id": "777777", "title": "Different sound title", "streaming_links": [{"platform": "spotify", "song_id": "track123", "link": "https://open.spotify.com/track/track123"}]}

    def recording(self, title):
        recording = Recording.objects.create(organization=self.org, title=title)
        ArtistResourceGrant.objects.create(artist=self.artist, recording=recording)
        return recording

    def context(self):
        from provenance.collectors.tiktok_catalog import music_context
        return music_context(self.job)

    def attach(self, payload=None, *, endpoint="tiktok-music", kind="music", external_id="777777"):
        from provenance.collectors.tiktok_catalog import attach_music_matches
        payload = self.payload if payload is None else payload
        source = SourceRecord.objects.create(organization=self.org, service=self.service, sync_run=self.run,
            collector="omkar", collection_method="scraper", object_type=endpoint, external_id=external_id,
            payload_json=payload, payload_hash="a" * 64, observed_at=timezone.now())
        row = Collected(endpoint, kind, external_id, payload)
        return attach_music_matches(self.conn, self.music, source, row)

    def test_exact_streaming_identity_adds_one_sound_match_and_is_idempotent(self):
        self.assertTrue(self.context()[1](self.payload))
        before = Recording.objects.count()
        self.assertEqual(self.attach(), 1)
        self.assertEqual(self.attach(), 0)
        self.assertEqual(Recording.objects.count(), before)
        match = RecordingPlatformMatch.objects.get(platform_object=self.music)
        self.assertEqual((match.recording_id, match.relationship_type, match.match_method, match.review_status),
            (self.song.pk, "sound", "verified_streaming_link", "accepted"))
        self.assertEqual(str(match.confidence), "1.000")
        self.assertEqual(self.context()[0], {"777777"})

    def test_legacy_spotify_and_apple_song_urls_resolve(self):
        self.spotify_object.object_type = "recording"
        self.spotify_object.external_id = ""
        self.spotify_object.url += "?si=share"
        self.spotify_object.save()
        self.assertTrue(self.context()[1](self.payload))
        apple = Service.objects.create(slug="apple-music", name="Apple Music")
        obj = PlatformObject.objects.create(service=apple, object_type="recording", url="https://music.apple.com/us/album/song/111?i=222")
        RecordingPlatformMatch.objects.create(recording=self.song, platform_object=obj, review_status="accepted")
        payload = {"id": "777777", "streaming_links": [{"platform": "apple_music", "song_id": "222", "link": "https://music.apple.com/song/222"}]}
        self.assertTrue(self.context()[1](payload))
        self.assertEqual(self.attach(payload), 1)

    def test_conflicting_declared_id_and_url_is_not_trusted(self):
        self.payload["streaming_links"][0]["link"] = "https://open.spotify.com/track/someoneelse"
        self.assertFalse(self.context()[1](self.payload))
        self.assertEqual(self.attach(), 0)

    def test_links_to_multiple_recordings_are_ambiguous(self):
        other = self.recording("Other song")
        obj = PlatformObject.objects.create(service=self.spotify, object_type="track", external_id="othertrack")
        RecordingPlatformMatch.objects.create(recording=other, platform_object=obj, review_status="accepted")
        self.payload["streaming_links"].append({"platform": "spotify", "song_id": "othertrack"})
        self.assertFalse(self.context()[1](self.payload))
        self.assertEqual(self.attach(), 0)

    def test_duplicate_accepted_identity_across_recordings_is_ambiguous(self):
        RecordingPlatformMatch.objects.create(recording=self.recording("Duplicate song"), platform_object=self.spotify_object, review_status="accepted")
        self.assertFalse(self.context()[1](self.payload))
        self.assertEqual(self.attach(), 0)

    def test_unverified_removed_or_ungranted_streaming_matches_cannot_join(self):
        for status in ("pending", "rejected"):
            self.match.review_status = status
            self.match.save()
            self.assertFalse(self.context()[1](self.payload))
            self.assertEqual(self.attach(), 0)
        self.match.review_status = "accepted"
        self.match.save()
        removal = OperationRemoval.objects.create(organization=self.org, model_label="catalog.recordingplatformmatch", object_id=str(self.match.pk))
        self.assertFalse(self.context()[1](self.payload))
        removal.delete()
        ArtistResourceGrant.objects.filter(artist=self.artist, recording=self.song).delete()
        self.assertFalse(self.context()[1](self.payload))
        self.assertEqual(self.attach(), 0)

    def test_archived_or_removed_recordings_cannot_join(self):
        self.song.archived_at = timezone.now()
        self.song.save()
        self.assertFalse(self.context()[1](self.payload))
        self.song.archived_at = None
        self.song.save()
        OperationRemoval.objects.create(organization=self.org, model_label="catalog.recording", object_id=str(self.song.pk))
        self.assertFalse(self.context()[1](self.payload))
        self.assertEqual(self.attach(), 0)

    def test_existing_tiktok_decisions_are_never_overwritten(self):
        for status in ("pending", "rejected", "accepted"):
            match = RecordingPlatformMatch.objects.create(recording=self.song, platform_object=self.music, relationship_type="full_recording", review_status=status)
            self.assertEqual(self.attach(), 0)
            match.refresh_from_db()
            self.assertEqual(match.review_status, status)
            match.delete()
        match = RecordingPlatformMatch.objects.create(recording=self.song, platform_object=self.music, relationship_type="sound", review_status="accepted")
        OperationRemoval.objects.create(organization=self.org, model_label="catalog.recordingplatformmatch", object_id=str(match.pk))
        self.assertEqual(self.context()[0], set())
        self.assertEqual(self.attach(), 0)

    def test_existing_tiktok_alias_seeds_ids_and_preserves_its_recording(self):
        legacy = PlatformObject.objects.create(service=self.service, object_type="track", url="https://www.tiktok.com/music/legacy-title-777777?share=1")
        RecordingPlatformMatch.objects.create(recording=self.song, platform_object=legacy, review_status="accepted")
        self.assertEqual(self.context()[0], {"777777"})
        self.assertTrue(self.context()[1]({"id": "777777", "streaming_links": []}))
        other = self.recording("Other")
        RecordingPlatformMatch.objects.filter(platform_object=legacy).update(recording=other)
        self.assertEqual(self.attach(), 0)
        self.assertFalse(RecordingPlatformMatch.objects.filter(platform_object=self.music).exists())

    def test_only_current_verified_exact_customer_job_profile_is_authorized(self):
        for change in ({"enabled": False}, {"cursor_json": {"profile_link_id": str(uuid4())}}):
            original = {key: getattr(self.job, key) for key in change}
            SyncJob.objects.filter(pk=self.job.pk).update(**change)
            ids, predicate = self.context()
            self.assertEqual(ids, set())
            self.assertFalse(predicate(self.payload))
            self.assertEqual(self.attach(), 0)
            SyncJob.objects.filter(pk=self.job.pk).update(**original)
        ArtistProfileLink.objects.filter(pk=self.profile.pk).update(verification_status="unverified")
        self.assertFalse(self.context()[1](self.payload))
        self.assertEqual(self.attach(), 0)

    def test_noncustomer_and_inactive_connections_are_not_authorized(self):
        Connection.objects.filter(pk=self.conn.pk).update(status="inactive")
        self.assertFalse(self.context()[1](self.payload))
        Connection.objects.filter(pk=self.conn.pk).update(status="active")
        Artist.objects.filter(pk=self.artist.pk).update(bootstrap_key="")
        self.assertFalse(self.context()[1](self.payload))
        self.assertEqual(self.attach(), 0)

    def test_source_and_platform_identity_must_all_agree(self):
        self.assertEqual(self.attach({**self.payload, "id": "999999"}), 0)
        self.assertEqual(self.attach(endpoint="video-details", kind="video"), 0)
        self.assertEqual(self.attach(external_id="999999"), 0)
        self.music.service = self.spotify
        self.music.save()
        self.assertEqual(self.attach(), 0)

    def test_foreign_workspace_match_or_title_alone_cannot_join(self):
        ArtistResourceGrant.objects.filter(artist=self.artist, recording=self.song).delete()
        other = Artist.objects.create(organization=self.org, name="Other", slug="tiktok-other")
        ArtistResourceGrant.objects.create(artist=other, recording=self.song)
        self.assertFalse(self.context()[1](self.payload))
        self.assertFalse(self.context()[1]({"id": "777777", "title": self.song.title, "isrc": "same"}))
        self.assertEqual(self.attach(), 0)

    def test_original_sound_author_identity_does_not_create_catalog_recordings(self):
        before = Recording.objects.count()
        payload = {"id": "777777", "title": "original sound", "author": {"id": self.profile.external_id}, "is_original_sound": True}
        self.assertEqual(self.attach(payload), 0)
        self.assertEqual(Recording.objects.count(), before)

    def test_removed_job_profile_and_grants_do_not_authorize_joins(self):
        for model, obj in (("artists.artistprofilelink", self.profile), ("artists.artistresourcegrant", ArtistResourceGrant.objects.get(artist=self.artist, recording=self.song))):
            removal = OperationRemoval.objects.create(organization=self.org, model_label=model, object_id=str(obj.pk))
            self.assertFalse(self.context()[1](self.payload))
            self.assertEqual(self.attach(), 0)
            removal.delete()

    def test_malformed_streaming_fields_are_not_attribution(self):
        for entry in ({"platform": ["spotify"], "song_id": "track123"}, {"platform": "spotify", "song_id": "track123", "link": "https://example.com/track/track123"}, {"platform": "apple_music", "song_id": "111", "link": "https://music.apple.com/us/album/album/111"}):
            payload = {"id": "777777", "streaming_links": [entry]}
            self.assertFalse(self.context()[1](payload))
            self.assertEqual(self.attach(payload), 0)

    def test_soundcloud_requires_consistent_accepted_numeric_id_and_permalink(self):
        service = Service.objects.create(slug="soundcloud", name="SoundCloud")
        obj = PlatformObject.objects.create(service=service, object_type="track", external_id="888", url="https://soundcloud.com/customer/song")
        RecordingPlatformMatch.objects.create(recording=self.song, platform_object=obj, review_status="accepted")
        payload = {"id": "777777", "streaming_links": [{"platform": "soundcloud", "song_id": "888", "link": obj.url}]}
        self.assertTrue(self.context()[1](payload))
        payload["streaming_links"][0]["song_id"] = "999"
        self.assertFalse(self.context()[1](payload))
        self.assertEqual(self.attach(payload), 0)

    def test_cross_organization_source_cannot_attach(self):
        from provenance.collectors.tiktok_catalog import attach_music_matches
        source = SourceRecord.objects.create(organization=self.org, service=self.service, sync_run=self.run,
            collector="omkar", collection_method="scraper", object_type="tiktok-music", external_id="777777",
            payload_json=self.payload, payload_hash="a" * 64, observed_at=timezone.now())
        foreign = Organization.objects.create(slug="foreign-tiktok", name="Foreign")
        SourceRecord.objects.filter(pk=source.pk).update(organization=foreign)
        source.refresh_from_db()
        self.assertEqual(attach_music_matches(self.conn, self.music, source, Collected("tiktok-music", "music", "777777", self.payload)), 0)
