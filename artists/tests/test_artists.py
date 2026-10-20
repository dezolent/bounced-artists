from io import StringIO
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.test import TestCase

from artists.models import Artist, ArtistAlias, ArtistContact, ArtistMembership, ArtistProfileLink, Service


class ArtistModelTests(TestCase):
    def setUp(self):
        self.artist = Artist.objects.create(name="Dezolent", slug="dezolent")
        self.user = get_user_model().objects.create_user(username="member")

    def test_membership_is_unique_and_roles_are_constrained(self):
        ArtistMembership.objects.create(user=self.user, artist=self.artist, role="owner")
        with self.assertRaises(IntegrityError), transaction.atomic():
            ArtistMembership.objects.create(user=self.user, artist=self.artist, role="viewer")
        with self.assertRaises(IntegrityError), transaction.atomic():
            ArtistMembership.objects.filter(user=self.user).update(role="invalid")

    def test_names_and_slugs_cannot_be_empty_or_duplicate(self):
        for name, slug in (("", "empty"), ("Other", "Dezolent"), ("Other", "")):
            with self.subTest(slug=slug), self.assertRaises(IntegrityError), transaction.atomic():
                Artist.objects.create(name=name, slug=slug)

    def test_invalid_timezone_and_non_http_profile_are_rejected(self):
        self.artist.timezone = "Invented/Timezone"
        with self.assertRaises(ValidationError):
            self.artist.full_clean()
        platform = Service.objects.create(name="Website", slug="website")
        link = ArtistProfileLink(artist=self.artist, platform=platform, url="ftp://example.com/profile")
        with self.assertRaises(ValidationError):
            link.full_clean()

    def test_alias_and_profile_constraints(self):
        ArtistAlias.objects.create(artist=self.artist, name="Alias")
        with self.assertRaises(IntegrityError), transaction.atomic():
            ArtistAlias.objects.create(artist=self.artist, name="alias")
        platform = Service.objects.create(name="Website", slug="website")
        ArtistProfileLink.objects.create(artist=self.artist, platform=platform, url="https://example.com")
        with self.assertRaises(IntegrityError), transaction.atomic():
            ArtistProfileLink.objects.create(artist=self.artist, platform=platform, url="https://example.com")

    def test_private_contacts_are_default_and_collaborators_are_explicit(self):
        contact = ArtistContact.objects.create(artist=self.artist, purpose="booking", value="test@example.com")
        self.assertEqual(contact.visibility, "private")
        self.assertEqual(self.artist.management_status, "unmanaged")

    def test_seed_is_idempotent_and_preserves_edits(self):
        call_command("seed_artists", stdout=StringIO())
        seeded = Artist.objects.get(slug="sry4u")
        seeded.name = "Edited artist name"
        seeded.bio_short = "Staff-written biography"
        seeded.management_status = "unmanaged"
        seeded.save()
        call_command("seed_artists", stdout=StringIO())
        seeded.refresh_from_db()
        self.assertEqual(Artist.objects.count(), 3)
        self.assertEqual(seeded.name, "Edited artist name")
        self.assertEqual(seeded.bio_short, "Staff-written biography")
        self.assertEqual(seeded.management_status, "unmanaged")
        self.assertEqual(Artist.objects.get(slug="morbeato").name, "Morbeato")
        self.assertEqual(Artist.objects.get(slug="morbeato").management_status, "managed")

    def test_seed_preserves_identity_after_slug_edit(self):
        call_command("seed_artists", stdout=StringIO())
        self.artist.refresh_from_db()
        self.artist.slug = "edited-dezolent"
        self.artist.save()
        call_command("seed_artists", stdout=StringIO())
        self.assertEqual(Artist.objects.count(), 3)
        self.assertEqual(Artist.objects.get(pk=self.artist.pk).slug, "edited-dezolent")
