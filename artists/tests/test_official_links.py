from django.test import SimpleTestCase, TestCase

from artists.models import Artist, ArtistProfileLink, ArtistResourceGrant, Organization, Service
from catalog.models import Recording, RecordingPlatformMatch, PlatformObject
from provenance.models import AuditEvent


class OfficialLinkIdentityTests(SimpleTestCase):
    def test_album_song_and_playlist_identities_remain_distinct(self):
        from artists.official_links import canonical_link
        self.assertNotEqual(canonical_link("https://music.apple.com/us/album/name/123?i=1"), canonical_link("https://music.apple.com/us/album/name/123?i=2"))
        self.assertNotEqual(canonical_link("https://www.youtube.com/playlist?list=first"), canonical_link("https://www.youtube.com/playlist?list=second"))
        self.assertNotEqual(canonical_link("https://open.spotify.com/album/shared"), canonical_link("https://open.spotify.com/track/shared"))


class OfficialLinkVerificationTests(TestCase):
    def setUp(self):
        self.org = Organization.objects.get(slug="bounced-studio")
        self.artist = Artist.objects.create(organization=self.org, name="Dezolent", slug="edited-stage-name", bootstrap_key="dezolent", management_status="managed")
        self.other = Artist.objects.create(organization=self.org, name="Guest Artist", slug="guest")
        self.spotify = Service.objects.create(slug="spotify", name="Spotify")
        self.profile = ArtistProfileLink.objects.create(artist=self.artist, platform=self.spotify, url="https://open.spotify.com/artist/owner?si=old")
        self.guest = ArtistProfileLink.objects.create(artist=self.other, platform=self.spotify, url="https://open.spotify.com/artist/guest")
        self.song = Recording.objects.create(organization=self.org, title="An editorial title")
        ArtistResourceGrant.objects.create(artist=self.artist, recording=self.song)
        obj = PlatformObject.objects.create(service=self.spotify, object_type="track", url="https://open.spotify.com/track/track1?si=old")
        self.match = RecordingPlatformMatch.objects.create(recording=self.song, platform_object=obj)
        self.manifest = {"version": 1, "verified_on": "2026-10-22", "artists": [{"key": "dezolent", "website": "https://dezolent.com/",
            "profiles": ["https://open.spotify.com/artist/owner", "https://www.youtube.com/@dezolent"],
            "links": [{"url": "https://open.spotify.com/track/track1", "pages": ["https://dezolent.com/tracks/track1"]}, {"url": "https://open.spotify.com/artist/guest", "pages": ["https://dezolent.com/artists/guest"]}],
            "pages": [], "failures": []}]}

    def test_preview_does_not_write_and_apply_verifies_own_links_without_promoting_guests(self):
        from artists.official_links import verify_links
        verify_links(self.manifest, apply=False)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.verification_status, "unverified")
        self.assertEqual(AuditEvent.objects.count(), 0)
        result = verify_links(self.manifest, apply=True)
        self.profile.refresh_from_db(); self.guest.refresh_from_db(); self.match.refresh_from_db()
        self.assertEqual(self.profile.verification_status, "verified")
        self.assertEqual(self.match.review_status, "accepted")
        self.assertEqual(self.guest.verification_status, "unverified")
        self.assertEqual(self.other.management_status, "unmanaged")
        self.assertEqual(Artist.objects.count(), 2)
        self.assertEqual(ArtistProfileLink.objects.filter(artist=self.artist, platform__slug="youtube", verification_status="verified").count(), 1)
        self.song.refresh_from_db()
        self.assertEqual(self.song.title, "An editorial title")
        self.assertEqual(verify_links(self.manifest, apply=True)["changes"], 0)

    def test_rerun_preserves_later_operator_rejection(self):
        from artists.official_links import verify_links
        verify_links(self.manifest, apply=True)
        self.profile.verification_status = "rejected"; self.profile.save()
        verify_links(self.manifest, apply=True)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.verification_status, "rejected")

    def test_source_website_and_stable_workspace_identity_are_required(self):
        from artists.official_links import verify_links
        from django.core.exceptions import ValidationError
        self.manifest["artists"][0]["website"] = "https://attacker.invalid/"
        with self.assertRaises(ValidationError):
            verify_links(self.manifest, apply=True)
        self.assertEqual(AuditEvent.objects.count(), 0)

    def test_unrelated_http_destinations_do_not_block_verified_website_links(self):
        from artists.official_links import verify_links
        invalid = ArtistProfileLink.objects.create(artist=self.artist, platform=self.spotify, url="http://open.spotify.com/artist/old")
        result = verify_links(self.manifest, apply=True)
        self.profile.refresh_from_db(); invalid.refresh_from_db()
        self.assertEqual(self.profile.verification_status, "verified")
        self.assertEqual(invalid.verification_status, "unverified")
        self.assertEqual(result["artists"][0]["invalid_existing_links"], 1)

    def test_repeat_does_not_restore_a_profile_url_replaced_by_an_operator(self):
        from artists.official_links import verify_links
        verify_links(self.manifest, apply=True)
        self.profile.url = "https://open.spotify.com/artist/new"
        self.profile.verification_status = "unverified"
        self.profile.save()
        verify_links(self.manifest, apply=True)
        self.assertFalse(ArtistProfileLink.objects.filter(artist=self.artist, url="https://open.spotify.com/artist/owner").exists())
        self.assertEqual(ArtistProfileLink.objects.filter(artist=self.artist, platform=self.spotify).count(), 1)
