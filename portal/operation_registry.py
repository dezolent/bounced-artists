"""Explicit staff operations contracts. New database models require review here.

Generated records remain inspectable but cannot be fabricated or completed by a
staff form. JSON payloads, credentials and private storage locators are excluded.
"""
from dataclasses import dataclass
from django.apps import apps

DOMAIN_APPS = ('core','artists','catalog','rights','events','provenance','publishing','analytics','portal')


@dataclass(frozen=True)
class Operation:
    label: str
    fields: tuple
    edit_fields: tuple = ()
    superuser: bool = False
    workflow: str = ''

    @property
    def model(self):
        return apps.get_model(self.label)

    @property
    def key(self):
        return self.label.replace('.', '-')

    @property
    def note(self):
        if self.label.startswith('analytics.') and self.label != 'analytics.metricdefinition':
            return 'Observed analytics are preserved evidence. Collection, report processing and analysis workers are not implemented; this screen does not create observations or report success.'
        if self.label in {'provenance.syncrun','catalog.platformavailability'}:
            return 'Collector-generated evidence is read only. Integration workers are not implemented; no run can be started or marked complete here.'
        if self.label in {'publishing.publicationoutbox','publishing.websitepublication'}:
            return 'Publication delivery records are read only. Preparing or approving content does not confirm deployment; no delivery worker is implemented.'
        if self.label.startswith('publishing.approved') or self.label == 'publishing.websiteapprovedstate':
            return 'Use the publication review workflow to prepare, approve or withdraw an immutable public revision.'
        if not self.edit_fields:
            return 'Preserved evidence and history are read only. Use the linked owning workflow when an action is available. Raw payloads, credentials and storage locations are withheld.'
        return ''

    @property
    def title(self):
        return TITLES.get(self.label, str(self.model._meta.verbose_name_plural).capitalize())


# Safe detail fields are declared per model; omissions are intentional.
DISPLAY = {
    'core.user': ('last_login', 'is_superuser', 'username', 'first_name', 'last_name', 'email', 'is_staff', 'is_active', 'date_joined'),
    'artists.organization': ('created_at', 'updated_at', 'name', 'slug'),
    'artists.organizationmembership': ('created_at', 'updated_at', 'organization', 'user', 'role'),
    'artists.artist': ('created_at', 'updated_at', 'revision', 'archived_at', 'organization', 'name', 'slug', 'management_status', 'requires_staff_review', 'bio_short', 'bio_long', 'location', 'timezone'),
    'artists.artistmembership': ('created_at', 'updated_at', 'user', 'artist', 'role'),
    'artists.artistalias': ('created_at', 'updated_at', 'artist', 'name', 'alias_type'),
    'artists.artistcontact': ('created_at', 'updated_at', 'artist', 'purpose', 'label', 'value', 'name', 'email', 'visibility'),
    'artists.service': ('created_at', 'updated_at', 'name', 'slug', 'service_type'),
    'artists.artistprofilelink': ('created_at', 'updated_at', 'artist', 'platform', 'external_id', 'url', 'verification_status', 'verified_at'),
    'artists.artistresourcegrant': ('created_at', 'updated_at', 'artist', 'capability', 'recording', 'release', 'asset', 'work', 'party', 'event', 'label', 'release_group'),
    'catalog.label': ('created_at', 'updated_at', 'revision', 'archived_at', 'organization', 'name', 'disambiguation', 'mbid', 'party'),
    'catalog.releasegroup': ('created_at', 'updated_at', 'revision', 'archived_at', 'organization', 'title', 'primary_type', 'mbid', 'first_release_date', 'date_precision', 'disambiguation', 'status'),
    'catalog.recording': ('created_at', 'updated_at', 'revision', 'archived_at', 'organization', 'title', 'version_title', 'recording_type', 'language', 'disambiguation', 'notes', 'duration_ms', 'explicit', 'original_release_date', 'date_precision', 'status'),
    'catalog.recordingartist': ('created_at', 'updated_at', 'recording', 'artist', 'role', 'billing_order', 'credited_as', 'join_phrase'),
    'catalog.recordingidentifier': ('created_at', 'updated_at', 'recording', 'scheme', 'value', 'verification_status', 'source_record'),
    'catalog.recordingrelation': ('created_at', 'updated_at', 'from_recording', 'to_recording', 'relation_type'),
    'catalog.release': ('created_at', 'updated_at', 'revision', 'archived_at', 'organization', 'title', 'release_type', 'release_group', 'edition', 'country', 'language', 'script', 'packaging', 'barcode', 'catalog_number', 'disambiguation', 'notes', 'release_date', 'date_precision', 'label_party', 'status'),
    'catalog.releaseartist': ('created_at', 'updated_at', 'release', 'artist', 'role', 'billing_order', 'credited_as', 'join_phrase'),
    'catalog.releaseidentifier': ('created_at', 'updated_at', 'release', 'scheme', 'value', 'source_record'),
    'catalog.medium': ('created_at', 'updated_at', 'release', 'position', 'title', 'format'),
    'catalog.releasetrack': ('created_at', 'updated_at', 'revision', 'archived_at', 'release', 'recording', 'disc_number', 'track_number', 'display_title', 'medium', 'duration_ms', 'mbid', 'credit_text'),
    'catalog.genre': ('created_at', 'updated_at', 'name', 'slug'),
    'catalog.artistgenre': ('created_at', 'updated_at', 'artist', 'genre'),
    'catalog.recordinggenre': ('created_at', 'updated_at', 'recording', 'genre'),
    'catalog.releasegenre': ('created_at', 'updated_at', 'release', 'genre'),
    'catalog.releasegroupgenre': ('created_at', 'updated_at', 'release_group', 'genre'),
    'catalog.recordinglabel': ('created_at', 'updated_at', 'recording', 'label', 'position', 'is_primary'),
    'catalog.releaselabel': ('created_at', 'updated_at', 'release', 'label', 'position', 'is_primary', 'catalog_number'),
    'catalog.asset': ('created_at', 'updated_at', 'revision', 'archived_at', 'category', 'organization', 'mime_type', 'sha256', 'width', 'height', 'rights_basis', 'visibility', 'display_name', 'alt_text', 'description', 'rights_approved', 'current_version'),
    'catalog.assetversion': ('created_at', 'updated_at', 'asset', 'preview_width', 'preview_height', 'original_name', 'byte_size', 'mime_type', 'sha256', 'width', 'height', 'creator'),
    'catalog.artistasset': ('created_at', 'updated_at', 'artist', 'asset', 'purpose', 'sort_order'),
    'catalog.releaseasset': ('created_at', 'updated_at', 'release', 'asset', 'purpose', 'is_primary'),
    'catalog.recordingasset': ('created_at', 'updated_at', 'recording', 'asset', 'purpose', 'is_primary'),
    'catalog.platformobject': ('created_at', 'updated_at', 'service', 'object_type', 'external_id', 'url', 'title', 'availability_status', 'uploaded_at', 'first_seen_at', 'last_seen_at'),
    'catalog.recordingplatformmatch': ('created_at', 'updated_at', 'recording', 'platform_object', 'relationship_type', 'confidence', 'match_method', 'review_status'),
    'catalog.releaseplatformmatch': ('created_at', 'updated_at', 'release', 'platform_object', 'confidence', 'review_status'),
    'catalog.artistplatformmatch': ('created_at', 'updated_at', 'artist', 'platform_object', 'review_status'),
    'catalog.platformavailability': ('created_at', 'updated_at', 'platform_object', 'territory', 'status', 'checked_at'),
    'rights.party': ('created_at', 'updated_at', 'revision', 'archived_at', 'organization', 'party_type', 'legal_name', 'display_name'),
    'rights.partyidentifier': ('created_at', 'updated_at', 'party', 'scheme', 'value'),
    'rights.artistparty': ('created_at', 'updated_at', 'artist', 'party', 'relationship_type'),
    'rights.rightssociety': ('created_at', 'updated_at', 'name', 'code', 'society_type', 'territory'),
    'rights.partyaffiliation': ('created_at', 'updated_at', 'party', 'society', 'membership_reference', 'valid_from', 'valid_to'),
    'rights.musicalwork': ('created_at', 'updated_at', 'revision', 'archived_at', 'organization', 'title', 'iswc', 'work_type', 'language', 'disambiguation', 'notes', 'status'),
    'rights.workidentifier': ('created_at', 'updated_at', 'work', 'scheme', 'value', 'verification_status', 'source_record'),
    'rights.workalias': ('created_at', 'updated_at', 'work', 'title', 'alias_type'),
    'rights.workcontributor': ('created_at', 'updated_at', 'work', 'party', 'role'),
    'rights.recordingwork': ('created_at', 'updated_at', 'recording', 'work', 'relationship_type', 'verification_status'),
    'rights.workregistration': ('created_at', 'updated_at', 'work', 'society', 'external_work_id', 'registration_status', 'last_verified_at'),
    'rights.rightsclaim': ('created_at', 'updated_at', 'registration', 'party', 'right_type', 'share_value', 'share_basis', 'territory', 'valid_from', 'valid_to', 'source_record'),
    'events.venue': ('created_at', 'updated_at', 'name', 'address', 'city', 'region', 'country', 'timezone', 'latitude', 'longitude'),
    'events.event': ('created_at', 'updated_at', 'revision', 'archived_at', 'organization', 'venue', 'title', 'local_date', 'starts_at', 'ends_at', 'time_precision', 'status'),
    'events.eventartist': ('created_at', 'updated_at', 'event', 'artist', 'billing_order', 'performance_role'),
    'events.eventsource': ('created_at', 'updated_at', 'event', 'service', 'external_id', 'last_seen_at', 'source_status'),
    'events.ticketoffer': ('created_at', 'updated_at', 'event', 'event_source', 'url', 'offer_type', 'status'),
    'provenance.connection': ('created_at', 'updated_at', 'organization', 'artist', 'service', 'auth_type', 'status', 'expires_at'),
    'provenance.syncjob': ('created_at', 'updated_at', 'connection', 'job_type', 'schedule', 'next_run_at', 'enabled'),
    'provenance.syncrun': ('created_at', 'updated_at', 'sync_job', 'status', 'started_at', 'finished_at'),
    'provenance.sourcerecord': ('created_at', 'updated_at', 'organization', 'sync_run', 'service', 'collector', 'collection_method', 'parser_version', 'object_type', 'external_id', 'payload_hash', 'private_asset', 'observed_at', 'expires_at'),
    'provenance.entitytarget': ('created_at', 'updated_at', 'organization', 'artist', 'recording', 'release', 'work', 'event', 'website'),
    'provenance.fieldobservation': ('created_at', 'updated_at', 'source_record', 'target', 'field_name', 'confidence'),
    'provenance.editorialoverride': ('created_at', 'updated_at', 'target', 'field_name', 'user', 'reason'),
    'provenance.reviewitem': ('created_at', 'updated_at', 'source_record', 'issue_type', 'status', 'resolved_by'),
    'provenance.auditevent': ('created_at', 'updated_at', 'organization', 'user', 'action', 'target', 'target_label'),
    'provenance.reviewcandidate': ('created_at', 'updated_at', 'review_item', 'target'),
    'publishing.website': ('created_at', 'updated_at', 'revision', 'archived_at', 'organization', 'artist', 'domain', 'repository', 'hosting_provider', 'locale'),
    'publishing.page': ('created_at', 'updated_at', 'revision', 'archived_at', 'website', 'path', 'page_type', 'title', 'publication_status'),
    'publishing.seorecord': ('created_at', 'updated_at', 'revision', 'archived_at', 'page', 'title', 'description', 'canonical_url', 'og_asset', 'robots'),
    'publishing.fanlinkpage': ('created_at', 'updated_at', 'revision', 'archived_at', 'website', 'recording', 'release', 'slug', 'title', 'status'),
    'publishing.fanlinkdestination': ('created_at', 'updated_at', 'fanlink_page', 'platform_object', 'label', 'sort_order', 'enabled'),
    'publishing.publicationoutbox': ('created_at', 'updated_at', 'website', 'content_version', 'status', 'attempts', 'next_attempt_at'),
    'publishing.websitepublication': ('created_at', 'updated_at', 'website', 'content_version', 'snapshot_asset', 'status', 'published_at'),
    'publishing.approvedcatalogrevision': ('website', 'schema_version', 'content_version', 'approver', 'approved_at', 'checksum', 'source_checksum'),
    'publishing.websiteapprovedstate': ('active_revision', 'approved_slug'),
    'publishing.approvedartwork': ('revision', 'version', 'public_id'),
    'analytics.metricdefinition': ('created_at', 'updated_at', 'service', 'code', 'unit', 'aggregation_rule', 'period_type', 'definition_version'),
    'analytics.metricseries': ('created_at', 'updated_at', 'organization', 'metric_definition', 'platform_object', 'dimensions_hash', 'source_method'),
    'analytics.metricobservation': ('created_at', 'updated_at', 'series', 'period_start', 'period_end', 'observed_at', 'value', 'source_record', 'revision', 'quality_status'),
    'analytics.reportimport': ('created_at', 'updated_at', 'artist', 'service', 'asset', 'file_hash', 'report_type', 'coverage_start', 'coverage_end', 'status'),
    'analytics.audioanalysis': ('created_at', 'updated_at', 'recording', 'input_asset', 'engine', 'engine_version', 'tempo', 'key', 'loudness', 'analyzed_at'),
    'analytics.playlistappearance': ('created_at', 'updated_at', 'recording', 'playlist_object', 'first_observed_at', 'last_observed_at', 'position', 'source_record'),
    'portal.operationremoval': ('created_at','updated_at','organization','model_label','object_id','removed_by','removed_at','restored_by','restored_at'),
    'portal.loginthrottle': ('failures', 'window_started'),
    'portal.editorialchange': ('organization', 'artist', 'user', 'model_label', 'object_id', 'action', 'created_at'),
    'portal.catalogreviewrequest': ('artist', 'requested_by', 'website', 'created_at', 'status'),
    'auth.permission': ('name', 'content_type', 'codename'),
    'auth.group': ('name',),
    'admin.logentry': ('action_time', 'user', 'content_type', 'action_flag'),
    'contenttypes.contenttype': ('app_label', 'model'),
    'sessions.session': ('expire_date',),
}

TITLES = {
    'artists.artistalias':'Artist aliases',
    'catalog.recordingidentifier':'Song identifiers',
    'catalog.recordingrelation':'Related songs',
    'catalog.releaseartist':'Release credits',
    'catalog.medium':'Release media',
    'catalog.recordinggenre':'Song genres',
    'catalog.recordinglabel':'Song labels',
    'catalog.artistasset':'Artist artwork',
    'catalog.recordingasset':'Song artwork and files',
    'catalog.releaseasset':'Release artwork and files',
    'catalog.releaseplatformmatch':'Release destination matches',
    'catalog.artistplatformmatch':'Artist destination matches',
    'catalog.platformavailability':'Platform availability checks',
    'rights.party':'Rights parties',
    'rights.artistparty':'Artist rights parties',
    'rights.workalias':'Work aliases',
    'rights.recordingwork':'Song works',
    'provenance.syncjob':'Synchronization configuration',
    'provenance.syncrun':'Synchronization runs',
    'publishing.seorecord':'SEO records',
    'publishing.publicationoutbox':'Publication delivery queue',
    'publishing.websiteapprovedstate':'Current public approvals',
    'publishing.approvedartwork':'Approved artwork versions',
    'analytics.metricseries':'Metric series',
    'analytics.audioanalysis':'Audio analysis results',

    'artists.organizationmembership':'Organization access',
    'artists.artistmembership':'Artist workspace access',
    'artists.artistresourcegrant':'Explicit resource grants',
    'catalog.recording':'Songs', 'catalog.recordingartist':'Song credits',
    'catalog.recordingplatformmatch':'Song destination matches',
    'rights.rightssociety':'Collection societies',
    'provenance.sourcerecord':'Source evidence',
    'provenance.entitytarget':'Evidence targets',
    'portal.operationremoval':'Preserved relationship removals',
    'portal.loginthrottle':'Sign-in protection',
    'portal.catalogreviewrequest':'Catalog review requests',
    'admin.logentry':'System audit log',
    'sessions.session':'Active session metadata',
}

# Relationship identity is fixed after creation. Organization changes cannot
# silently transfer existing editorial records or access into another workspace.
EDIT = {
    'artists.organization':('name','slug'),
    'artists.organizationmembership':('organization','user','role'),
    'artists.artist':('organization','name','slug','management_status','bio_short','bio_long','location','timezone'),
    'artists.artistalias':('artist','name','alias_type'),
    'artists.artistcontact':('artist','purpose','label','value','name','email','visibility'),
    'artists.service':('name','slug','service_type','capabilities'),
    'artists.artistprofilelink':('artist','platform','external_id','url','verification_status'),
    'artists.artistresourcegrant':('artist','capability','recording','release','asset','work','party','event','label','release_group'),
    'catalog.label':('organization','name','disambiguation','mbid','party'),
    'catalog.releasegroup':('organization','title','primary_type','secondary_types','mbid','first_release_date','date_precision','disambiguation','status'),
    'catalog.recording':('organization','title','version_title','recording_type','language','disambiguation','notes','duration_ms','explicit','original_release_date','date_precision','status'),
    'catalog.recordingartist':('recording','artist','role','billing_order','credited_as','join_phrase'),
    'catalog.recordingidentifier':('recording','scheme','value','verification_status','source_record'),
    'catalog.recordingrelation':('from_recording','to_recording','relation_type'),
    'catalog.release':('organization','title','release_type','release_group','edition','country','language','script','packaging','barcode','catalog_number','disambiguation','notes','release_date','date_precision','label_party','status'),
    'catalog.releaseartist':('release','artist','role','billing_order','credited_as','join_phrase'),
    'catalog.releaseidentifier':('release','scheme','value','source_record'),
    'catalog.medium':('release','position','title','format'),
    'catalog.releasetrack':('release','recording','disc_number','track_number','display_title','medium','duration_ms','mbid','credit_text'),
    'catalog.genre':('name','slug'),
    'catalog.artistgenre':('artist','genre'),
    'catalog.recordinggenre':('recording','genre'),
    'catalog.releasegenre':('release','genre'),
    'catalog.releasegroupgenre':('release_group','genre'),
    'catalog.recordinglabel':('recording','label','position','is_primary'),
    'catalog.releaselabel':('release','label','position','is_primary','catalog_number'),
    # File creation is handled by the upload workflow; approval stays explicit.
    'catalog.asset':('display_name','category','alt_text','description','rights_basis','visibility','rights_approved'),
    'catalog.artistasset':('artist','asset','purpose','sort_order'),
    'catalog.releaseasset':('release','asset','purpose','is_primary'),
    'catalog.recordingasset':('recording','asset','purpose','is_primary'),
    'catalog.platformobject':('service','object_type','external_id','url','title'),
    'catalog.recordingplatformmatch':('recording','platform_object','relationship_type','review_status'),
    'catalog.releaseplatformmatch':('release','platform_object','review_status'),
    'catalog.artistplatformmatch':('artist','platform_object','review_status'),
    'rights.party':('organization','party_type','legal_name','display_name'),
    'rights.partyidentifier':('party','scheme','value'),
    'rights.artistparty':('artist','party','relationship_type'),
    'rights.rightssociety':('name','code','society_type','territory'),
    'rights.partyaffiliation':('party','society','membership_reference','valid_from','valid_to'),
    'rights.musicalwork':('organization','title','iswc','work_type','language','disambiguation','notes','status'),
    'rights.workidentifier':('work','scheme','value','verification_status','source_record'),
    'rights.workalias':('work','title','alias_type'),
    'rights.workcontributor':('work','party','role'),
    'rights.recordingwork':('recording','work','relationship_type','verification_status'),
    'rights.workregistration':('work','society','external_work_id','registration_status','last_verified_at'),
    'rights.rightsclaim':('registration','party','right_type','share_value','share_basis','territory','valid_from','valid_to'),
    'events.venue':('name','address','city','region','country','timezone','latitude','longitude'),
    'events.event':('organization','venue','title','local_date','starts_at','ends_at','time_precision','status'),
    'events.eventartist':('event','artist','billing_order','performance_role'),
    'events.eventsource':('event','service','external_id','source_url','source_status'),
    'events.ticketoffer':('event','event_source','url','offer_type','status'),
    'provenance.connection':('organization','artist','service','auth_type'),
    'provenance.syncjob':('connection','job_type','schedule','enabled'),
    'publishing.website':('organization','artist','domain','repository','hosting_provider','locale'),
    'publishing.page':('website','path','page_type','title'),
    'publishing.seorecord':('page','title','description','canonical_url','og_asset','robots'),
    'publishing.fanlinkpage':('website','recording','release','slug','title','status'),
    'publishing.fanlinkdestination':('fanlink_page','platform_object','label','sort_order','enabled'),
    'analytics.metricdefinition':('service','code','unit','aggregation_rule','period_type','definition_version'),
}

SECURITY = {'core.user','artists.organization','artists.organizationmembership',
            'artists.artistmembership','artists.artistresourcegrant','auth.group',
            'auth.permission','admin.logentry','contenttypes.contenttype','sessions.session',
            'portal.loginthrottle'}
WORKFLOWS = {'portal.operationremoval':'removal','core.user':'users','artists.artistmembership':'users',
             'provenance.reviewitem':'review','portal.catalogreviewrequest':'review',
             'catalog.asset':'upload','publishing.website':'publication',
             'publishing.approvedcatalogrevision':'publication',
             'publishing.websiteapprovedstate':'publication',
             'publishing.publicationoutbox':'publication',
             'publishing.websitepublication':'publication'}
NO_CREATE = {'catalog.asset'}
REVOKABLE = {'artists.organizationmembership','artists.artistresourcegrant'}
REGISTRY = {label.replace('.','-'):Operation(label,fields,EDIT.get(label,()),label in SECURITY,WORKFLOWS.get(label,'')) for label,fields in DISPLAY.items()}

# Preserved relationship removal is separate from access revocation and archival.
REMOVABLE = {
    'artists.artistalias','artists.artistcontact','artists.artistprofilelink',
    'catalog.recordingartist','catalog.recordingidentifier','catalog.recordingrelation',
    'catalog.releaseartist','catalog.releaseidentifier','catalog.medium',
    'catalog.artistgenre','catalog.recordinggenre','catalog.releasegenre',
    'catalog.releasegroupgenre','catalog.recordinglabel','catalog.releaselabel',
    'catalog.artistasset','catalog.releaseasset','catalog.recordingasset',
    'catalog.recordingplatformmatch','catalog.releaseplatformmatch','catalog.artistplatformmatch',
    'rights.partyidentifier','rights.artistparty','rights.partyaffiliation',
    'rights.workidentifier','rights.workalias','rights.workcontributor',
    'rights.recordingwork','rights.workregistration','rights.rightsclaim',
    'events.eventartist','events.eventsource','events.ticketoffer',
    'publishing.fanlinkdestination',
}
