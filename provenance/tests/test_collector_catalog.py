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

    def test_title_and_isrc_without_duration_hold_distinct_platform_ids_for_review(self):
        self.ensure(self.track(external_ids={"isrc": "US1234567890"}))
        self.assertFalse(self.ensure(self.track(external_id="song2", external_ids={"isrc": "US1234567890"})))
        self.assertEqual(Recording.objects.count(), 1)
        self.assertTrue(ReviewItem.objects.filter(issue_type="recording_identity_ambiguous").exists())

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

    def test_discovery_copies_artwork_dates_language_identifiers_and_genres(self):
        from catalog.models import Asset, RecordingAsset, RecordingIdentifier, RecordingGenre
        self.switch_platform('apple-music', 'https://music.apple.com/us/artist/customer/42')
        values = self.track(endpoint='song-details', artists=[{'id':'42'}], duration_ms=120000,
            release_date='2024-06-15', audio_language='en', isrc='USAAA2400001', genres=['Electronic'],
            artwork={'link':'https://is1-ssl.mzstatic.com/image/cover.jpg','width':1200,'height':1200})
        self.assertTrue(self.ensure(values))
        recording = Recording.objects.get()
        self.assertEqual(str(recording.original_release_date), '2024-06-15')
        self.assertEqual((recording.date_precision, recording.language), ('day','en'))
        self.assertEqual(RecordingIdentifier.objects.get().source_record_id, values[1].pk)
        self.assertEqual(RecordingIdentifier.objects.get().verification_status, 'unverified')
        self.assertEqual(RecordingGenre.objects.get().genre.name, 'Electronic')
        asset = RecordingAsset.objects.get(recording=recording, purpose='cover').asset
        self.assertEqual((asset.source_url, asset.width, asset.visibility, asset.rights_approved),
            ('https://is1-ssl.mzstatic.com/image/cover.jpg',1200,'private',False))
        self.assertTrue(ArtistResourceGrant.objects.filter(artist=self.artist, asset=asset).exists())
        self.assertIn(str(values[1].pk), asset.description)
        self.assertFalse(self.ensure(values))
        self.assertEqual((Asset.objects.count(), RecordingIdentifier.objects.count()), (1,1))

    def test_existing_song_backfills_empty_fields_but_preserves_editorial_values_and_cover(self):
        from catalog.models import Asset, RecordingAsset
        self.ensure(self.track(duration_ms=70000, explicit=False))
        recording = Recording.objects.get()
        recording.title = 'Edited';recording.save()
        asset = Asset.objects.create(organization=self.org, source_url='https://example.com/edited.jpg', mime_type='image/jpeg')
        RecordingAsset.objects.create(recording=recording, asset=asset, purpose='cover', is_primary=True)
        self.ensure(self.track(endpoint='spotify-track', duration_ms=999999, explicit=True,
            album={'release_date':'2020-04','images':[{'url':'https://i.scdn.co/image/cover'}]},
            external_ids={'isrc':'USAAA2400002'}))
        recording.refresh_from_db()
        self.assertEqual((recording.title,recording.duration_ms,recording.explicit),('Edited',70000,False))
        self.assertEqual((str(recording.original_release_date),recording.date_precision),('2020-04-01','month'))
        self.assertEqual(RecordingAsset.objects.get().asset_id,asset.pk)

    def test_explicitly_cleared_field_and_removed_cover_or_identifier_stay_removed(self):
        from catalog.models import Asset, RecordingAsset, RecordingIdentifier
        from portal.models import EditorialChange, OperationRemoval
        self.ensure(self.track())
        recording = Recording.objects.get()
        EditorialChange.objects.create(organization=self.org,artist=self.artist,user=None,
            model_label='catalog.recording',object_id=str(recording.pk),action='update',changed_fields=['duration_ms'])
        asset = Asset.objects.create(organization=self.org,source_url='https://example.com/old.jpg')
        cover = RecordingAsset.objects.create(recording=recording,asset=asset,purpose='cover')
        identifier = RecordingIdentifier.objects.create(recording=recording,scheme='isrc',value='USAAA2400003')
        for obj in (cover,identifier):
            OperationRemoval.objects.create(organization=self.org,model_label=obj._meta.label_lower,object_id=str(obj.pk))
        self.ensure(self.track(duration_ms=1000,isrc='USAAA2400004',images=[{'url':'https://example.com/new.jpg'}]))
        recording.refresh_from_db()
        self.assertIsNone(recording.duration_ms)
        self.assertEqual((RecordingAsset.objects.count(),RecordingIdentifier.objects.count()),(1,1))

    def test_unsafe_artwork_and_malformed_metadata_do_not_abort_song_creation(self):
        from catalog.models import RecordingAsset, RecordingIdentifier
        self.assertTrue(self.ensure(self.track(duration_ms=True, release_date='not-a-date',
            isrc='invalid',images=[{'url':'https://127.0.0.1/secret.jpg'}],genres=[{},False])))
        self.assertFalse(RecordingAsset.objects.exists())
        self.assertFalse(RecordingIdentifier.objects.exists())
        self.assertIsNone(Recording.objects.get().original_release_date)

    def test_removed_recording_grant_prevents_metadata_backfill(self):
        from portal.models import OperationRemoval
        from catalog.models import RecordingAsset
        self.ensure(self.track())
        grant = ArtistResourceGrant.objects.get(recording=Recording.objects.get())
        OperationRemoval.objects.create(organization=self.org,model_label=grant._meta.label_lower,object_id=str(grant.pk))
        self.ensure(self.track(images=[{'url':'https://i.scdn.co/image/cover'}]))
        self.assertFalse(RecordingAsset.objects.exists())

    def test_backfill_command_uses_saved_receipts_and_dry_run_rolls_back(self):
        from io import StringIO
        import json
        from django.core.management import call_command
        from catalog.models import RecordingAsset
        values = self.track(images=[{'url':'https://i.scdn.co/image/cover'}])
        self.job.enabled = True;self.job.save()
        out = StringIO()
        call_command('backfill_platform_catalog',stdout=out)
        self.assertEqual(json.loads(out.getvalue())['recordings_created'],1)
        self.assertFalse(Recording.objects.exists())
        call_command('backfill_platform_catalog',apply=True,stdout=StringIO())
        self.assertEqual((Recording.objects.count(),RecordingAsset.objects.count()),(1,1))
        self.assertEqual(SourceRecord.objects.count(),1)
        call_command('backfill_platform_catalog',apply=True,stdout=StringIO())
        self.assertEqual((Recording.objects.count(),RecordingAsset.objects.count()),(1,1))

    def test_song_page_shows_cover_and_reported_metadata_with_source_link(self):
        from django.contrib.auth import get_user_model
        from django.urls import reverse
        from artists.models import ArtistMembership
        self.switch_platform('apple-music','https://music.apple.com/us/artist/customer/42')
        values = self.track(endpoint='song-details',artists=[{'id':'42','name':'Customer'}],
            isrc='USAAA2400001',album={'name':'Demo album','copyright':'Demo copyright','record_label':'Demo label'},
            composer_name='Demo composer',audio_traits=['lossless'],artwork={'link':'https://is1-ssl.mzstatic.com/image/cover.jpg'})
        self.ensure(values)
        user = get_user_model().objects.create_user(username='metadata-viewer')
        ArtistMembership.objects.create(artist=self.artist,user=user,role='viewer')
        self.client.force_login(user)
        response = self.client.get(reverse('portal:recording',args=[self.artist.pk,Recording.objects.get().pk]))
        for value in ('Platform facts fill missing catalog details','Demo album','Demo copyright','Demo label','Demo composer','lossless','https://is1-ssl.mzstatic.com/image/cover.jpg'):
            self.assertContains(response,value)
        self.assertContains(response,reverse('portal:insight-detail',args=[self.artist.pk,values[1].pk]))

    def test_provider_date_precision_is_preserved_and_unknown_artwork_is_not_guessed(self):
        from catalog.models import RecordingAsset
        self.ensure(self.track(album={'release_date':'2024-01-01','release_date_precision':'year'},
            images=[{'url':'https://example.com/cover.jpg'}]))
        recording = Recording.objects.get()
        self.assertEqual(recording.date_precision,'year')
        self.assertFalse(RecordingAsset.objects.exists())

    def test_operations_and_admin_edits_protect_intentionally_cleared_fields(self):
        from django.contrib.auth import get_user_model
        from django.contrib.admin.models import LogEntry, CHANGE
        from django.contrib.contenttypes.models import ContentType
        from provenance.models import AuditEvent
        self.ensure(self.track())
        recording = Recording.objects.get()
        AuditEvent.objects.create(organization=self.org,action='portal.operations.update',target_label='catalog.recording',
            change_json={'object_id':str(recording.pk),'changed_fields':['duration_ms']})
        staff = get_user_model().objects.create_user(username='metadata-staff',is_staff=True)
        LogEntry.objects.create(user=staff,content_type=ContentType.objects.get_for_model(Recording),object_id=str(recording.pk),
            object_repr='Discovered song',action_flag=CHANGE,change_message='[{"changed":{"fields":["Original release date"]}}]')
        self.ensure(self.track(duration_ms=1000,release_date='2024-04-01'))
        recording.refresh_from_db()
        self.assertIsNone(recording.duration_ms)
        self.assertIsNone(recording.original_release_date)

    def test_cross_platform_corroboration_reuses_canonical_recording(self):
        self.ensure(self.track(duration_ms=180000,external_ids={'isrc':'US1234567890'}))
        original=Recording.objects.get()
        self.assertFalse(self.ensure(self.track(external_id='song2',name='Discovered song (feat. Guest)',
            duration_ms=180500,external_ids={'isrc':'US1234567890'})))
        self.assertEqual(Recording.objects.count(),1)
        self.assertEqual(RecordingPlatformMatch.objects.filter(recording=original).count(),2)

    def test_likely_title_without_corroboration_is_reviewed_once(self):
        self.ensure(self.track(duration_ms=180000))
        self.assertFalse(self.ensure(self.track(external_id='uncertain',duration_ms=180000)))
        self.assertFalse(self.ensure(self.track(external_id='uncertain',duration_ms=180000)))
        self.assertEqual(Recording.objects.count(),1)
        self.assertEqual(ReviewItem.objects.filter(issue_type='recording_identity_ambiguous').count(),1)

    def test_soundcloud_private_share_permalink_reuses_public_track_identity(self):
        self.switch_platform('soundcloud','https://soundcloud.com/customer')
        recording=Recording.objects.create(organization=self.org,title='Discovered song',duration_ms=180000)
        ArtistResourceGrant.objects.create(artist=self.artist,recording=recording)
        obj=PlatformObject.objects.create(service=self.service,object_type='recording',
            url='https://soundcloud.com/customer/song/s-PrivateShare?utm_source=site')
        RecordingPlatformMatch.objects.create(recording=recording,platform_object=obj,review_status='accepted')
        self.assertFalse(self.ensure(self.track(user={'permalink':'customer'},link='https://soundcloud.com/customer/song',duration_ms=180000)))
        self.assertEqual(Recording.objects.count(),1)
        self.assertTrue(RecordingPlatformMatch.objects.filter(recording=recording,platform_object__external_id='song1').exists())
