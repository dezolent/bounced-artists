from django.test import TestCase
from django.utils import timezone

from artists.models import Artist, ArtistProfileLink, ArtistResourceGrant, Organization, Service
from catalog.models import PlatformObject, Recording, RecordingArtist, RecordingPlatformMatch
from provenance.models import Connection, EntityTarget, FieldObservation, ReviewCandidate, ReviewItem, SourceRecord, SyncJob, SyncRun
from provenance.collectors.providers import Collected


class CollectorCatalogTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.get(slug="bounced-studio")
        self.artist = Artist.objects.create(organization=self.org, name="Customer", slug="catalog-customer",
            bootstrap_key="dezolent", management_status="managed")
        self.service = Service.objects.create(name="Spotify", slug="spotify")
        self.profile = ArtistProfileLink.objects.create(artist=self.artist, platform=self.service,
            url="https://open.spotify.com/artist/customer", verification_status="verified")
        self.conn = Connection.objects.create(organization=self.org, artist=self.artist, service=self.service, status="active")
        self.job = SyncJob.objects.create(connection=self.conn, job_type="daily_public_platform", cursor_json={"profile_link_id": str(self.profile.pk)})
        self.run = SyncRun.objects.create(sync_job=self.job)

    def track(self, *, endpoint="track-details", kind="track", external_id="song1", **body):
        payload = {"id": external_id, "name": "Discovered song", "artists": [{"id": "customer"}], **body}
        row = Collected(endpoint, kind, external_id, payload)
        source = SourceRecord.objects.create(organization=self.org, service=self.service, sync_run=self.run,
            collector="omkar", collection_method="scraper", object_type=endpoint, external_id=external_id,
            payload_json=payload, payload_hash="a" * 64, observed_at=timezone.now())
        obj, _ = PlatformObject.objects.get_or_create(service=self.service, object_type=kind, external_id=external_id)
        return obj, source, row

    def ensure(self, values):
        from provenance.collectors.catalog import ensure_catalog_recording
        return ensure_catalog_recording(self.conn, *values)

    def switch_platform(self, slug, url):
        self.service.slug = slug
        self.service.save()
        self.profile.url = url
        self.profile.save()

    def test_verified_owned_track_creates_draft_granted_recording_with_source(self):
        values = self.track(duration_ms=123000, explicit=False)
        self.assertTrue(self.ensure(values))
        recording = Recording.objects.get()
        self.assertEqual((recording.title, recording.status, recording.duration_ms, recording.explicit),
            ("Discovered song", "draft", 123000, False))
        self.assertEqual(list(RecordingArtist.objects.values_list("artist_id", "role")), [(self.artist.pk, "primary")])
        self.assertEqual(ArtistResourceGrant.objects.get(recording=recording).capability, "manage")
        self.assertEqual(RecordingPlatformMatch.objects.get().review_status, "accepted")
        self.assertTrue(FieldObservation.objects.filter(source_record=values[1], target__recording=recording).exists())

    def test_replay_and_official_alias_preserve_editorial_content(self):
        self.assertTrue(self.ensure(self.track()))
        recording = Recording.objects.get()
        recording.title = "Edited title"
        recording.status = "approved"
        recording.save()
        self.assertFalse(self.ensure(self.track(endpoint="spotify-track", name="New scraped title")))
        self.assertEqual(Recording.objects.count(), 1)
        self.assertEqual(RecordingPlatformMatch.objects.count(), 1)
        recording.refresh_from_db()
        self.assertEqual((recording.title, recording.status), ("Edited title", "approved"))

    def test_title_and_isrc_do_not_merge_distinct_platform_ids(self):
        self.ensure(self.track(external_ids={"isrc": "US1234567890"}))
        self.assertTrue(self.ensure(self.track(external_id="song2", external_ids={"isrc": "US1234567890"})))
        self.assertEqual(Recording.objects.count(), 2)

    def test_accepted_legacy_url_only_match_is_reused(self):
        recording = Recording.objects.create(organization=self.org, title="Legacy title")
        ArtistResourceGrant.objects.create(artist=self.artist, recording=recording, capability="read")
        legacy = PlatformObject.objects.create(service=self.service, object_type="recording", url="https://open.spotify.com/track/song1?si=share")
        RecordingPlatformMatch.objects.create(recording=recording, platform_object=legacy, review_status="accepted")
        self.assertFalse(self.ensure(self.track()))
        self.assertEqual(Recording.objects.count(), 1)
        self.assertEqual(ArtistResourceGrant.objects.get().capability, "read")
        self.assertTrue(RecordingPlatformMatch.objects.filter(recording=recording, platform_object__external_id="song1", review_status="accepted").exists())

    def test_rejected_pending_archived_and_removed_matches_are_not_recreated(self):
        from portal.models import OperationRemoval
        for index, blocked in enumerate(("rejected", "pending", "archived", "removed")):
            with self.subTest(blocked=blocked):
                values = self.track(external_id=f"blocked{index}")
                recording = Recording.objects.create(organization=self.org, title="Existing", archived_at=timezone.now() if blocked == "archived" else None)
                ArtistResourceGrant.objects.create(artist=self.artist, recording=recording)
                match = RecordingPlatformMatch.objects.create(recording=recording, platform_object=values[0], review_status=blocked if blocked in {"rejected", "pending"} else "accepted")
                if blocked == "removed":
                    OperationRemoval.objects.create(organization=self.org, model_label="catalog.recordingplatformmatch", object_id=str(match.pk))
                self.assertFalse(self.ensure(values))
                self.assertEqual(Recording.objects.count(), index + 1)

    def test_existing_ungranted_recording_does_not_gain_access(self):
        values = self.track()
        recording = Recording.objects.create(organization=self.org, title="Private")
        RecordingPlatformMatch.objects.create(recording=recording, platform_object=values[0], review_status="accepted")
        self.assertFalse(self.ensure(values))
        self.assertFalse(ArtistResourceGrant.objects.exists())
        self.assertEqual(Recording.objects.count(), 1)

    def test_missing_or_other_artist_attribution_is_not_a_song(self):
        for artists in (None, [], [{"id": "other"}]):
            self.assertFalse(self.ensure(self.track(artists=artists)))
        self.assertFalse(Recording.objects.exists())

    def test_only_active_verified_customer_profiles_authorize_creation(self):
        values = self.track()
        self.profile.verification_status = "unverified"
        self.profile.save()
        self.assertFalse(self.ensure(values))
        self.profile.verification_status = "verified"
        self.profile.save()
        self.artist.bootstrap_key = None
        self.artist.save()
        self.assertFalse(self.ensure(values))
        self.assertFalse(Recording.objects.exists())

    def test_apple_requires_verified_artist_id_in_payload(self):
        self.switch_platform("apple-music", "https://music.apple.com/us/artist/customer/42")
        self.assertFalse(self.ensure(self.track(endpoint="song-details")))
        self.assertTrue(self.ensure(self.track(endpoint="song-details", artists=[{"id": "42"}])))

    def test_soundcloud_requires_matching_owner_permalink(self):
        self.switch_platform("soundcloud", "https://soundcloud.com/customer")
        self.assertFalse(self.ensure(self.track(user={"permalink": "other"})))
        self.assertTrue(self.ensure(self.track(user={"permalink": "customer"}, title="SoundCloud song")))

    def test_soundcloud_owner_id_requires_connection_profile_evidence(self):
        self.switch_platform("soundcloud", "https://soundcloud.com/customer")
        values = self.track(user={"id": 42})
        self.assertFalse(self.ensure(values))
        SourceRecord.objects.create(organization=self.org, service=self.service, sync_run=self.run,
            collector="omkar", collection_method="scraper", object_type="user-details", external_id="42",
            payload_json={"id": 42}, payload_hash="b" * 64, observed_at=timezone.now())
        self.assertTrue(self.ensure(values))

    def test_social_videos_and_posts_never_create_recordings(self):
        for slug, url, endpoint, kind in (("youtube", "https://www.youtube.com/@customer", "video-details", "video"),
                ("instagram", "https://www.instagram.com/customer/", "post-details", "post")):
            self.switch_platform(slug, url)
            self.assertFalse(self.ensure(self.track(endpoint=endpoint, kind=kind)))
        self.assertFalse(Recording.objects.exists())

    def test_payload_id_mismatch_and_missing_title_do_not_create_recording(self):
        values = self.track()
        values[1].payload_json["id"] = "other"
        values[1].save()
        self.assertFalse(self.ensure(values))
        self.assertFalse(self.ensure(self.track(name="")))
        self.assertFalse(Recording.objects.exists())

    def test_legacy_soundcloud_permalink_reuses_recording(self):
        self.switch_platform("soundcloud", "https://soundcloud.com/customer")
        recording = Recording.objects.create(organization=self.org, title="Legacy title")
        ArtistResourceGrant.objects.create(artist=self.artist, recording=recording)
        legacy = PlatformObject.objects.create(service=self.service, object_type="recording",
            url="https://soundcloud.com/customer/song?utm_source=share")
        RecordingPlatformMatch.objects.create(recording=recording, platform_object=legacy, review_status="accepted")
        self.assertFalse(self.ensure(self.track(user={"permalink": "customer"}, link="https://soundcloud.com/customer/song")))
        self.assertEqual(Recording.objects.count(), 1)
        self.assertEqual(RecordingPlatformMatch.objects.filter(recording=recording).count(), 2)

    def test_recording_and_profile_removal_suppress_discovery(self):
        from portal.models import OperationRemoval
        values = self.track()
        removal = OperationRemoval.objects.create(organization=self.org, model_label="artists.artistprofilelink", object_id=str(self.profile.pk))
        self.assertFalse(self.ensure(values))
        removal.restored_at = timezone.now()
        removal.save()
        self.assertTrue(self.ensure(values))
        recording = Recording.objects.get()
        OperationRemoval.objects.create(organization=self.org, model_label="catalog.recording", object_id=str(recording.pk))
        self.assertFalse(self.ensure(self.track()))
        self.assertEqual(Recording.objects.count(), 1)

    def test_foreign_organization_match_does_not_create_or_grant_recording(self):
        values = self.track()
        foreign_org = Organization.objects.create(name="Other", slug="other")
        foreign_recording = Recording.objects.create(organization=foreign_org, title="Other private song")
        RecordingPlatformMatch.objects.create(recording=foreign_recording, platform_object=values[0], review_status="accepted")
        self.assertFalse(self.ensure(values))
        self.assertEqual(Recording.objects.count(), 1)
        self.assertFalse(ArtistResourceGrant.objects.exists())

    def test_removed_object_and_incomplete_receipt_do_not_create_recording(self):
        from portal.models import OperationRemoval
        values = self.track()
        values[2].incomplete = True
        self.assertFalse(self.ensure(values))
        values[2].incomplete = False
        OperationRemoval.objects.create(organization=self.org, model_label="catalog.platformobject", object_id=str(values[0].pk))
        self.assertFalse(self.ensure(values))
        self.assertFalse(Recording.objects.exists())

    def test_existing_duplicate_entity_targets_do_not_block_replayed_evidence(self):
        values = self.track()
        self.ensure(values)
        recording = Recording.objects.get()
        EntityTarget.objects.create(organization=self.org, recording=recording)
        self.assertFalse(self.ensure(values))
        self.assertEqual(Recording.objects.count(), 1)
        self.assertEqual(FieldObservation.objects.filter(source_record=values[1], field_name="title").count(), 1)

    def test_completed_discovery_review_is_preserved(self):
        for index, status in enumerate(("dismissed", "resolved")):
            values = self.track(external_id=f"reviewed{index}")
            issue = ReviewItem.objects.create(source_record=values[1], issue_type="platform_object_discovered", status=status)
            target = EntityTarget.objects.create(organization=self.org, artist=self.artist)
            ReviewCandidate.objects.create(review_item=issue, target=target)
            self.assertFalse(self.ensure(values))
        self.assertFalse(Recording.objects.exists())

    def test_apple_explicit_flag_populates_only_new_draft(self):
        self.switch_platform("apple-music", "https://music.apple.com/us/artist/customer/42")
        self.assertTrue(self.ensure(self.track(endpoint="song-details", artists=[{"id": "42"}], is_explicit=True)))
        recording = Recording.objects.get()
        self.assertIs(recording.explicit, True)
        recording.explicit = False
        recording.save()
        self.assertFalse(self.ensure(self.track(endpoint="song-details", artists=[{"id": "42"}], is_explicit=True)))
        recording.refresh_from_db()
        self.assertIs(recording.explicit, False)

    def test_another_verified_profile_cannot_authorize_this_jobs_song(self):
        ArtistProfileLink.objects.create(artist=self.artist, platform=self.service,
            url="https://open.spotify.com/artist/second", verification_status="verified")
        self.assertFalse(self.ensure(self.track(artists=[{"id": "second"}])))
        self.assertFalse(Recording.objects.exists())
