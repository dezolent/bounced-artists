"""Explicit private editing contracts; no model-wide serializer or unscoped FK input."""
from dataclasses import dataclass
from artists import models as a
from catalog import models as c
from rights import models as r
from events import models as e
from publishing import models as p


@dataclass(frozen=True)
class Resource:
    model: type
    fields: tuple
    title: str
    grant: str = ''
    parent: str = ''
    artist_field: str = ''


RESOURCES = {
    'artists': Resource(a.Artist, ('name','slug','bio_short','bio_long','location','timezone'), 'Artist profile'),
    'recordings': Resource(c.Recording, ('title','version_title','recording_type','duration_ms','explicit','original_release_date','date_precision','language','disambiguation','notes'), 'Songs', 'recording'),
    'releases': Resource(c.Release, ('title','release_type','release_date','date_precision','release_group','edition','country','language','script','packaging','barcode','catalog_number','disambiguation','notes'), 'Releases', 'release'),
    'labels': Resource(c.Label, ('name','disambiguation','mbid','party'), 'Record labels', 'label'),
    'release-groups': Resource(c.ReleaseGroup, ('title','primary_type','secondary_types','mbid','first_release_date','date_precision','disambiguation'), 'Release groups', 'release_group'),
    'recording-labels': Resource(c.RecordingLabel, ('recording','label','position','is_primary'), 'Song labels', parent='recording'),
    'release-labels': Resource(c.ReleaseLabel, ('release','label','catalog_number','position','is_primary'), 'Release labels', parent='release'),
    'release-genres': Resource(c.ReleaseGenre, ('release','genre'), 'Release genres', parent='release'),
    'release-group-genres': Resource(c.ReleaseGroupGenre, ('release_group','genre'), 'Release group genres', parent='release_group'),
    'media': Resource(c.Medium, ('release','position','title','format'), 'Release media', parent='release'),
    'assets': Resource(c.Asset, ('display_name','category','alt_text','description','rights_basis','visibility'), 'Assets', 'asset'),
    'works': Resource(r.MusicalWork, ('title','iswc','work_type','language','disambiguation','notes'), 'Works', 'work'),
    'work-identifiers': Resource(r.WorkIdentifier, ('work','scheme','value'), 'Work identifiers', parent='work'),
    'parties': Resource(r.Party, ('party_type','display_name','legal_name'), 'Rights parties', 'party'),
    'party-identifiers': Resource(r.PartyIdentifier, ('party','scheme','value'), 'Party identifiers', parent='party'),
    'affiliations': Resource(r.PartyAffiliation, ('party','society','membership_reference','valid_from','valid_to'), 'Affiliation assertions', parent='party'),
    'artist-parties': Resource(r.ArtistParty, ('party','relationship_type'), 'Artist parties', artist_field='artist'),
    'events': Resource(e.Event, ('title','local_date','starts_at','ends_at','time_precision','status'), 'Events', 'event'),
    'aliases': Resource(a.ArtistAlias, ('name','alias_type'), 'Aliases', artist_field='artist'),
    'contacts': Resource(a.ArtistContact, ('purpose','label','value','name','email','visibility'), 'Contacts', artist_field='artist'),
    'profile-links': Resource(a.ArtistProfileLink, ('platform','external_id','url'), 'Profile links', artist_field='artist'),
    'artist-genres': Resource(c.ArtistGenre, ('genre',), 'Artist genres', artist_field='artist'),
    'artist-assets': Resource(c.ArtistAsset, ('asset','purpose','sort_order'), 'Artist artwork', artist_field='artist'),
    'recording-identifiers': Resource(c.RecordingIdentifier, ('recording','scheme','value'), 'Song identifiers', parent='recording'),
    'release-identifiers': Resource(c.ReleaseIdentifier, ('release','scheme','value'), 'Release identifiers', parent='release'),
    'recording-credits': Resource(c.RecordingArtist, ('recording','artist','role','billing_order','credited_as','join_phrase'), 'Song credits', parent='recording'),
    'release-credits': Resource(c.ReleaseArtist, ('release','artist','role','billing_order','credited_as','join_phrase'), 'Release credits', parent='release'),
    'recording-genres': Resource(c.RecordingGenre, ('recording','genre'), 'Song genres', parent='recording'),
    'recording-relations': Resource(c.RecordingRelation, ('from_recording','to_recording','relation_type'), 'Related songs', parent='from_recording'),
    'tracks': Resource(c.ReleaseTrack, ('release','recording','disc_number','track_number','display_title','medium','duration_ms','mbid','credit_text'), 'Release tracks', parent='release'),
    'recording-assets': Resource(c.RecordingAsset, ('recording','asset','purpose','is_primary'), 'Song artwork & files', parent='recording'),
    'release-assets': Resource(c.ReleaseAsset, ('release','asset','purpose','is_primary'), 'Release artwork & files', parent='release'),
    'recording-links': Resource(c.RecordingPlatformMatch, ('recording','relationship_type'), 'Song destinations', parent='recording'),
    'release-links': Resource(c.ReleasePlatformMatch, ('release',), 'Release destinations', parent='release'),
    'fanlinks': Resource(p.FanlinkPage, ('website','recording','release','slug','title'), 'Fanlinks'),
    'destinations': Resource(p.FanlinkDestination, ('fanlink_page','platform_object','label','sort_order','enabled'), 'Fanlink destinations', parent='fanlink_page'),
    'pages': Resource(p.Page, ('website','path','page_type','title'), 'Pages'),
    'seo': Resource(p.SEORecord, ('page','title','description','canonical_url','og_asset','robots'), 'SEO', parent='page'),
    'work-aliases': Resource(r.WorkAlias, ('work','title','alias_type'), 'Work aliases', parent='work'),
    'recording-works': Resource(r.RecordingWork, ('recording','work','relationship_type'), 'Song works', parent='recording'),
    'contributors': Resource(r.WorkContributor, ('work','party','role'), 'Contributors', parent='work'),
    'registrations': Resource(r.WorkRegistration, ('work','society','external_work_id'), 'Registration assertions', parent='work'),
    'claims': Resource(r.RightsClaim, ('registration','party','right_type','share_value','share_basis','territory','valid_from','valid_to'), 'Rights assertions', parent='registration'),
    'event-artists': Resource(e.EventArtist, ('event','artist','billing_order','performance_role'), 'Event artists', parent='event'),
    'tickets': Resource(e.TicketOffer, ('event','url','offer_type','status'), 'Ticket offers', parent='event'),
}


def resource(kind):
    from django.http import Http404
    try:
        return RESOURCES[kind]
    except KeyError:
        raise Http404
