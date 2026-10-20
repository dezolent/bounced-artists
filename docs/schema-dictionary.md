# Schema dictionary

Generated from the Django models with `python scripts/generate_schema_docs.py`.
New domain entities use UUID primary keys; previously deployed artist-related tables retain their original key types. `PK` and `FK` identify keys; Django indexes foreign keys by default. Logical ownership of a child follows its parent unless stated otherwise.

## artists

Intended sources: Staff entry; artist supplied identities and website profiles. Empty domain tables do not imply any connector has run.

### Organization (`artists_organization`)

Ownership: organization root; staff governed.

| Column and type |
| --- |
| `id UUIDField PK UNIQUE` |
| `created_at DateTimeField` |
| `updated_at DateTimeField` |
| `name CharField` |
| `slug SlugField UNIQUE` |

Constraints: model field constraints only. Indexes: FK/PK/unique indexes.

### OrganizationMembership (`artists_organizationmembership`)

Ownership: organization FK; superuser administration.

| Column and type |
| --- |
| `id UUIDField PK UNIQUE` |
| `created_at DateTimeField` |
| `updated_at DateTimeField` |
| `organization_id ForeignKey FK→artists.Organization (CASCADE)` |
| `user_id ForeignKey FK→core.User (CASCADE)` |
| `role CharField` |

Constraints: org_member_unique. Indexes: FK/PK/unique indexes.

### Artist (`artists_artist`)

Ownership: organization FK; artist memberships grant private artist scope.

| Column and type |
| --- |
| `created_at DateTimeField` |
| `updated_at DateTimeField` |
| `id UUIDField PK UNIQUE` |
| `organization_id ForeignKey FK→artists.Organization (PROTECT)` |
| `name CharField` |
| `slug SlugField UNIQUE` |
| `public_metadata JSONField` |
| `bootstrap_key CharField UNIQUE NULL` |
| `management_status CharField` |
| `bio_short TextField` |
| `bio_long TextField` |
| `location CharField` |
| `timezone CharField` |

Constraints: artist_slug_ci_unique; artist_name_nonblank; artist_slug_nonempty; artist_management_status_valid. Indexes: FK/PK/unique indexes.

### ArtistMembership (`artists_artistmembership`)

Ownership: artist FK; superuser administration.

| Column and type |
| --- |
| `id BigAutoField PK UNIQUE` |
| `created_at DateTimeField` |
| `updated_at DateTimeField` |
| `user_id ForeignKey FK→core.User (CASCADE)` |
| `artist_id ForeignKey FK→artists.Artist (CASCADE)` |
| `role CharField` |

Constraints: membership_user_artist_unique; membership_role_valid. Indexes: artists_art_artist__7fa919_idx.

### ArtistAlias (`artists_artistalias`)

Ownership: inherits from artists.Artist via artist.

| Column and type |
| --- |
| `id BigAutoField PK UNIQUE` |
| `created_at DateTimeField` |
| `updated_at DateTimeField` |
| `artist_id ForeignKey FK→artists.Artist (CASCADE)` |
| `name CharField` |
| `alias_type CharField` |

Constraints: artist_alias_ci_unique; artist_alias_nonblank. Indexes: FK/PK/unique indexes.

### ArtistContact (`artists_artistcontact`)

Ownership: inherits from artists.Artist via artist.

| Column and type |
| --- |
| `id BigAutoField PK UNIQUE` |
| `created_at DateTimeField` |
| `updated_at DateTimeField` |
| `artist_id ForeignKey FK→artists.Artist (CASCADE)` |
| `purpose CharField` |
| `label CharField` |
| `value CharField` |
| `name CharField` |
| `email CharField` |
| `visibility CharField` |

Constraints: contact_visibility_valid; contact_purpose_valid; contact_value_nonblank. Indexes: artists_art_artist__dfc1e7_idx.

### Service (`artists_service`)

Ownership: global reference.

| Column and type |
| --- |
| `id BigAutoField PK UNIQUE` |
| `created_at DateTimeField` |
| `updated_at DateTimeField` |
| `name CharField` |
| `slug SlugField UNIQUE` |
| `service_type CharField` |
| `capabilities JSONField` |

Constraints: service_slug_ci_unique; service_name_nonblank. Indexes: FK/PK/unique indexes.

### ArtistProfileLink (`artists_artistprofilelink`)

Ownership: inherits from artists.Artist via artist.

| Column and type |
| --- |
| `id BigAutoField PK UNIQUE` |
| `created_at DateTimeField` |
| `updated_at DateTimeField` |
| `artist_id ForeignKey FK→artists.Artist (CASCADE)` |
| `platform_id ForeignKey FK→artists.Service (PROTECT)` |
| `external_id CharField` |
| `url CharField` |
| `verification_status CharField` |
| `verified_at DateTimeField NULL` |

Constraints: artist_profile_url_unique; artist_profile_external_id_unique; profile_verification_valid. Indexes: FK/PK/unique indexes.

## catalog

Intended sources: Future catalog imports and staff reconciliation; Astro site data is reference only. Empty domain tables do not imply any connector has run.

### Recording (`catalog_recording`)

Ownership: inherits from artists.Organization via organization.

| Column and type |
| --- |
| `id UUIDField PK UNIQUE` |
| `created_at DateTimeField` |
| `updated_at DateTimeField` |
| `organization_id ForeignKey FK→artists.Organization (PROTECT)` |
| `title CharField` |
| `version_title CharField` |
| `recording_type CharField` |
| `duration_ms PositiveIntegerField NULL` |
| `explicit BooleanField NULL` |
| `original_release_date DateField NULL` |
| `date_precision CharField` |
| `status CharField` |

Constraints: model field constraints only. Indexes: FK/PK/unique indexes.

### RecordingArtist (`catalog_recordingartist`)

Ownership: inherits from catalog.Recording via recording.

| Column and type |
| --- |
| `id UUIDField PK UNIQUE` |
| `created_at DateTimeField` |
| `updated_at DateTimeField` |
| `recording_id ForeignKey FK→catalog.Recording (CASCADE)` |
| `artist_id ForeignKey FK→artists.Artist (PROTECT)` |
| `role CharField` |
| `billing_order PositiveSmallIntegerField` |

Constraints: recording_credit_unique. Indexes: FK/PK/unique indexes.

### RecordingIdentifier (`catalog_recordingidentifier`)

Ownership: inherits from catalog.Recording via recording.

| Column and type |
| --- |
| `id UUIDField PK UNIQUE` |
| `created_at DateTimeField` |
| `updated_at DateTimeField` |
| `recording_id ForeignKey FK→catalog.Recording (CASCADE)` |
| `scheme CharField` |
| `value CharField` |
| `verification_status CharField` |
| `source_record_id ForeignKey FK→provenance.SourceRecord (SET_NULL) NULL` |

Constraints: recording_identifier_unique. Indexes: FK/PK/unique indexes.

### RecordingRelation (`catalog_recordingrelation`)

Ownership: staff governed; see architecture for access path.

| Column and type |
| --- |
| `id UUIDField PK UNIQUE` |
| `created_at DateTimeField` |
| `updated_at DateTimeField` |
| `from_recording_id ForeignKey FK→catalog.Recording (CASCADE)` |
| `to_recording_id ForeignKey FK→catalog.Recording (CASCADE)` |
| `relation_type CharField` |

Constraints: recording_relation_unique; recording_no_self_relation. Indexes: FK/PK/unique indexes.

### Release (`catalog_release`)

Ownership: inherits from artists.Organization via organization.

| Column and type |
| --- |
| `id UUIDField PK UNIQUE` |
| `created_at DateTimeField` |
| `updated_at DateTimeField` |
| `organization_id ForeignKey FK→artists.Organization (PROTECT)` |
| `title CharField` |
| `release_type CharField` |
| `release_date DateField NULL` |
| `date_precision CharField` |
| `label_party_id ForeignKey FK→rights.Party (PROTECT) NULL` |
| `status CharField` |

Constraints: model field constraints only. Indexes: FK/PK/unique indexes.

### ReleaseArtist (`catalog_releaseartist`)

Ownership: inherits from catalog.Release via release.

| Column and type |
| --- |
| `id UUIDField PK UNIQUE` |
| `created_at DateTimeField` |
| `updated_at DateTimeField` |
| `release_id ForeignKey FK→catalog.Release (CASCADE)` |
| `artist_id ForeignKey FK→artists.Artist (PROTECT)` |
| `role CharField` |
| `billing_order PositiveSmallIntegerField` |

Constraints: release_credit_unique. Indexes: FK/PK/unique indexes.

### ReleaseIdentifier (`catalog_releaseidentifier`)

Ownership: inherits from catalog.Release via release.

| Column and type |
| --- |
| `id UUIDField PK UNIQUE` |
| `created_at DateTimeField` |
| `updated_at DateTimeField` |
| `release_id ForeignKey FK→catalog.Release (CASCADE)` |
| `scheme CharField` |
| `value CharField` |
| `source_record_id ForeignKey FK→provenance.SourceRecord (SET_NULL) NULL` |

Constraints: release_identifier_unique. Indexes: FK/PK/unique indexes.

### ReleaseTrack (`catalog_releasetrack`)

Ownership: inherits from catalog.Release via release.

| Column and type |
| --- |
| `id UUIDField PK UNIQUE` |
| `created_at DateTimeField` |
| `updated_at DateTimeField` |
| `release_id ForeignKey FK→catalog.Release (CASCADE)` |
| `recording_id ForeignKey FK→catalog.Recording (PROTECT)` |
| `disc_number PositiveSmallIntegerField` |
| `track_number PositiveSmallIntegerField` |
| `display_title CharField` |

Constraints: release_track_position_unique; release_track_positive_position. Indexes: FK/PK/unique indexes.

### Genre (`catalog_genre`)

Ownership: global reference.

| Column and type |
| --- |
| `id UUIDField PK UNIQUE` |
| `created_at DateTimeField` |
| `updated_at DateTimeField` |
| `name CharField` |
| `slug SlugField UNIQUE` |

Constraints: model field constraints only. Indexes: FK/PK/unique indexes.

### ArtistGenre (`catalog_artistgenre`)

Ownership: inherits from artists.Artist via artist.

| Column and type |
| --- |
| `id UUIDField PK UNIQUE` |
| `created_at DateTimeField` |
| `updated_at DateTimeField` |
| `artist_id ForeignKey FK→artists.Artist (CASCADE)` |
| `genre_id ForeignKey FK→catalog.Genre (PROTECT)` |

Constraints: artist_genre_unique. Indexes: FK/PK/unique indexes.

### RecordingGenre (`catalog_recordinggenre`)

Ownership: inherits from catalog.Recording via recording.

| Column and type |
| --- |
| `id UUIDField PK UNIQUE` |
| `created_at DateTimeField` |
| `updated_at DateTimeField` |
| `recording_id ForeignKey FK→catalog.Recording (CASCADE)` |
| `genre_id ForeignKey FK→catalog.Genre (PROTECT)` |

Constraints: recording_genre_unique. Indexes: FK/PK/unique indexes.

### Asset (`catalog_asset`)

Ownership: inherits from artists.Organization via organization.

| Column and type |
| --- |
| `id UUIDField PK UNIQUE` |
| `created_at DateTimeField` |
| `updated_at DateTimeField` |
| `organization_id ForeignKey FK→artists.Organization (PROTECT) NULL` |
| `storage_key CharField` |
| `source_url CharField` |
| `source_repository CharField` |
| `source_path CharField` |
| `mime_type CharField` |
| `sha256 CharField` |
| `width PositiveIntegerField NULL` |
| `height PositiveIntegerField NULL` |
| `rights_basis CharField` |
| `visibility CharField` |

Constraints: asset_has_location; asset_repo_path_pair; private_asset_has_owner. Indexes: FK/PK/unique indexes.

### ArtistAsset (`catalog_artistasset`)

Ownership: inherits from artists.Artist via artist.

| Column and type |
| --- |
| `id UUIDField PK UNIQUE` |
| `created_at DateTimeField` |
| `updated_at DateTimeField` |
| `artist_id ForeignKey FK→artists.Artist (CASCADE)` |
| `asset_id ForeignKey FK→catalog.Asset (PROTECT)` |
| `purpose CharField` |
| `sort_order PositiveSmallIntegerField` |

Constraints: artist_asset_unique. Indexes: FK/PK/unique indexes.

### ReleaseAsset (`catalog_releaseasset`)

Ownership: inherits from catalog.Release via release.

| Column and type |
| --- |
| `id UUIDField PK UNIQUE` |
| `created_at DateTimeField` |
| `updated_at DateTimeField` |
| `release_id ForeignKey FK→catalog.Release (CASCADE)` |
| `asset_id ForeignKey FK→catalog.Asset (PROTECT)` |
| `purpose CharField` |
| `is_primary BooleanField` |

Constraints: release_asset_unique; release_primary_asset_unique. Indexes: FK/PK/unique indexes.

### RecordingAsset (`catalog_recordingasset`)

Ownership: inherits from catalog.Recording via recording.

| Column and type |
| --- |
| `id UUIDField PK UNIQUE` |
| `created_at DateTimeField` |
| `updated_at DateTimeField` |
| `recording_id ForeignKey FK→catalog.Recording (CASCADE)` |
| `asset_id ForeignKey FK→catalog.Asset (PROTECT)` |
| `purpose CharField` |
| `is_primary BooleanField` |

Constraints: recording_asset_unique; recording_primary_asset_unique. Indexes: FK/PK/unique indexes.

### PlatformObject (`catalog_platformobject`)

Ownership: global service listing; access through owned match/series.

| Column and type |
| --- |
| `id UUIDField PK UNIQUE` |
| `created_at DateTimeField` |
| `updated_at DateTimeField` |
| `service_id ForeignKey FK→artists.Service (PROTECT)` |
| `object_type CharField` |
| `external_id CharField` |
| `url CharField` |
| `title CharField` |
| `availability_status CharField` |
| `uploaded_at DateTimeField NULL` |
| `first_seen_at DateTimeField NULL` |
| `last_seen_at DateTimeField NULL` |

Constraints: platform_object_has_locator; platform_id_unique; platform_url_only_unique. Indexes: FK/PK/unique indexes.

### RecordingPlatformMatch (`catalog_recordingplatformmatch`)

Ownership: inherits from catalog.Recording via recording.

| Column and type |
| --- |
| `id UUIDField PK UNIQUE` |
| `created_at DateTimeField` |
| `updated_at DateTimeField` |
| `recording_id ForeignKey FK→catalog.Recording (CASCADE)` |
| `platform_object_id ForeignKey FK→catalog.PlatformObject (PROTECT)` |
| `relationship_type CharField` |
| `confidence DecimalField NULL` |
| `match_method CharField` |
| `review_status CharField` |

Constraints: recording_platform_match_unique. Indexes: FK/PK/unique indexes.

### ReleasePlatformMatch (`catalog_releaseplatformmatch`)

Ownership: inherits from catalog.Release via release.

| Column and type |
| --- |
| `id UUIDField PK UNIQUE` |
| `created_at DateTimeField` |
| `updated_at DateTimeField` |
| `release_id ForeignKey FK→catalog.Release (CASCADE)` |
| `platform_object_id ForeignKey FK→catalog.PlatformObject (PROTECT)` |
| `confidence DecimalField NULL` |
| `review_status CharField` |

Constraints: release_platform_match_unique. Indexes: FK/PK/unique indexes.

### ArtistPlatformMatch (`catalog_artistplatformmatch`)

Ownership: inherits from artists.Artist via artist.

| Column and type |
| --- |
| `id UUIDField PK UNIQUE` |
| `created_at DateTimeField` |
| `updated_at DateTimeField` |
| `artist_id ForeignKey FK→artists.Artist (CASCADE)` |
| `platform_object_id ForeignKey FK→catalog.PlatformObject (PROTECT)` |
| `review_status CharField` |

Constraints: artist_platform_match_unique. Indexes: FK/PK/unique indexes.

### PlatformAvailability (`catalog_platformavailability`)

Ownership: staff governed; see architecture for access path.

| Column and type |
| --- |
| `id UUIDField PK UNIQUE` |
| `created_at DateTimeField` |
| `updated_at DateTimeField` |
| `platform_object_id ForeignKey FK→catalog.PlatformObject (CASCADE)` |
| `territory CharField` |
| `status CharField` |
| `checked_at DateTimeField` |

Constraints: model field constraints only. Indexes: catalog_pla_platfor_ffd47f_idx.

## rights

Intended sources: Artist supplied documentation and authorized society evidence. Empty domain tables do not imply any connector has run.

### Party (`rights_party`)

Ownership: organization FK or unassigned public credit; legal name private.

| Column and type |
| --- |
| `id UUIDField PK UNIQUE` |
| `created_at DateTimeField` |
| `updated_at DateTimeField` |
| `organization_id ForeignKey FK→artists.Organization (PROTECT) NULL` |
| `party_type CharField` |
| `legal_name CharField` |
| `display_name CharField` |

Constraints: model field constraints only. Indexes: FK/PK/unique indexes.

### PartyIdentifier (`rights_partyidentifier`)

Ownership: staff governed; see architecture for access path.

| Column and type |
| --- |
| `id UUIDField PK UNIQUE` |
| `created_at DateTimeField` |
| `updated_at DateTimeField` |
| `party_id ForeignKey FK→rights.Party (CASCADE)` |
| `scheme CharField` |
| `value CharField` |

Constraints: party_identifier_unique. Indexes: FK/PK/unique indexes.

### ArtistParty (`rights_artistparty`)

Ownership: inherits from artists.Artist via artist.

| Column and type |
| --- |
| `id UUIDField PK UNIQUE` |
| `created_at DateTimeField` |
| `updated_at DateTimeField` |
| `artist_id ForeignKey FK→artists.Artist (CASCADE)` |
| `party_id ForeignKey FK→rights.Party (PROTECT)` |
| `relationship_type CharField` |

Constraints: artist_party_unique. Indexes: FK/PK/unique indexes.

### RightsSociety (`rights_rightssociety`)

Ownership: global reference.

| Column and type |
| --- |
| `id UUIDField PK UNIQUE` |
| `created_at DateTimeField` |
| `updated_at DateTimeField` |
| `name CharField` |
| `code CharField UNIQUE` |
| `society_type CharField` |
| `territory CharField` |

Constraints: model field constraints only. Indexes: FK/PK/unique indexes.

### PartyAffiliation (`rights_partyaffiliation`)

Ownership: staff governed; see architecture for access path.

| Column and type |
| --- |
| `id UUIDField PK UNIQUE` |
| `created_at DateTimeField` |
| `updated_at DateTimeField` |
| `party_id ForeignKey FK→rights.Party (CASCADE)` |
| `society_id ForeignKey FK→rights.RightsSociety (PROTECT)` |
| `membership_reference CharField` |
| `valid_from DateField NULL` |
| `valid_to DateField NULL` |

Constraints: party_affiliation_dates_valid. Indexes: FK/PK/unique indexes.

### MusicalWork (`rights_musicalwork`)

Ownership: inherits from artists.Organization via organization.

| Column and type |
| --- |
| `id UUIDField PK UNIQUE` |
| `created_at DateTimeField` |
| `updated_at DateTimeField` |
| `organization_id ForeignKey FK→artists.Organization (PROTECT)` |
| `title CharField` |
| `iswc CharField` |
| `status CharField` |

Constraints: model field constraints only. Indexes: FK/PK/unique indexes.

### WorkAlias (`rights_workalias`)

Ownership: inherits from rights.MusicalWork via work.

| Column and type |
| --- |
| `id UUIDField PK UNIQUE` |
| `created_at DateTimeField` |
| `updated_at DateTimeField` |
| `work_id ForeignKey FK→rights.MusicalWork (CASCADE)` |
| `title CharField` |
| `alias_type CharField` |

Constraints: work_alias_unique. Indexes: FK/PK/unique indexes.

### WorkContributor (`rights_workcontributor`)

Ownership: inherits from rights.MusicalWork via work.

| Column and type |
| --- |
| `id UUIDField PK UNIQUE` |
| `created_at DateTimeField` |
| `updated_at DateTimeField` |
| `work_id ForeignKey FK→rights.MusicalWork (CASCADE)` |
| `party_id ForeignKey FK→rights.Party (PROTECT)` |
| `role CharField` |

Constraints: work_contributor_unique. Indexes: FK/PK/unique indexes.

### RecordingWork (`rights_recordingwork`)

Ownership: inherits from catalog.Recording via recording.

| Column and type |
| --- |
| `id UUIDField PK UNIQUE` |
| `created_at DateTimeField` |
| `updated_at DateTimeField` |
| `recording_id ForeignKey FK→catalog.Recording (CASCADE)` |
| `work_id ForeignKey FK→rights.MusicalWork (PROTECT)` |
| `relationship_type CharField` |
| `verification_status CharField` |

Constraints: recording_work_unique. Indexes: FK/PK/unique indexes.

### WorkRegistration (`rights_workregistration`)

Ownership: inherits from rights.MusicalWork via work.

| Column and type |
| --- |
| `id UUIDField PK UNIQUE` |
| `created_at DateTimeField` |
| `updated_at DateTimeField` |
| `work_id ForeignKey FK→rights.MusicalWork (CASCADE)` |
| `society_id ForeignKey FK→rights.RightsSociety (PROTECT)` |
| `external_work_id CharField` |
| `registration_status CharField` |
| `last_verified_at DateTimeField NULL` |

Constraints: work_registration_id_unique. Indexes: FK/PK/unique indexes.

### RightsClaim (`rights_rightsclaim`)

Ownership: inherits from rights.WorkRegistration via registration.

| Column and type |
| --- |
| `id UUIDField PK UNIQUE` |
| `created_at DateTimeField` |
| `updated_at DateTimeField` |
| `registration_id ForeignKey FK→rights.WorkRegistration (CASCADE)` |
| `party_id ForeignKey FK→rights.Party (PROTECT)` |
| `right_type CharField` |
| `share_value DecimalField NULL` |
| `share_basis CharField` |
| `territory CharField` |
| `valid_from DateField NULL` |
| `valid_to DateField NULL` |
| `source_record_id ForeignKey FK→provenance.SourceRecord (PROTECT) NULL` |

Constraints: rights_share_nonnegative; rights_claim_dates_valid. Indexes: FK/PK/unique indexes.

## events

Intended sources: Future Bandsintown/EDMTrain connectors and staff entry. Empty domain tables do not imply any connector has run.

### Venue (`events_venue`)

Ownership: staff governed; see architecture for access path.

| Column and type |
| --- |
| `id UUIDField PK UNIQUE` |
| `created_at DateTimeField` |
| `updated_at DateTimeField` |
| `name CharField` |
| `address CharField` |
| `city CharField` |
| `region CharField` |
| `country CharField` |
| `timezone CharField` |
| `latitude DecimalField NULL` |
| `longitude DecimalField NULL` |

Constraints: model field constraints only. Indexes: FK/PK/unique indexes.

### Event (`events_event`)

Ownership: inherits from artists.Organization via organization.

| Column and type |
| --- |
| `id UUIDField PK UNIQUE` |
| `created_at DateTimeField` |
| `updated_at DateTimeField` |
| `organization_id ForeignKey FK→artists.Organization (PROTECT)` |
| `venue_id ForeignKey FK→events.Venue (PROTECT) NULL` |
| `title CharField` |
| `local_date DateField` |
| `starts_at DateTimeField NULL` |
| `ends_at DateTimeField NULL` |
| `time_precision CharField` |
| `status CharField` |

Constraints: event_times_valid. Indexes: FK/PK/unique indexes.

### EventArtist (`events_eventartist`)

Ownership: inherits from events.Event via event.

| Column and type |
| --- |
| `id UUIDField PK UNIQUE` |
| `created_at DateTimeField` |
| `updated_at DateTimeField` |
| `event_id ForeignKey FK→events.Event (CASCADE)` |
| `artist_id ForeignKey FK→artists.Artist (PROTECT)` |
| `billing_order PositiveSmallIntegerField` |
| `performance_role CharField` |

Constraints: event_artist_unique. Indexes: FK/PK/unique indexes.

### EventSource (`events_eventsource`)

Ownership: inherits from events.Event via event.

| Column and type |
| --- |
| `id UUIDField PK UNIQUE` |
| `created_at DateTimeField` |
| `updated_at DateTimeField` |
| `event_id ForeignKey FK→events.Event (CASCADE)` |
| `service_id ForeignKey FK→artists.Service (PROTECT)` |
| `external_id CharField` |
| `source_url CharField` |
| `last_seen_at DateTimeField NULL` |
| `source_status CharField` |

Constraints: event_source_has_locator; event_source_id_unique. Indexes: FK/PK/unique indexes.

### TicketOffer (`events_ticketoffer`)

Ownership: inherits from events.Event via event.

| Column and type |
| --- |
| `id UUIDField PK UNIQUE` |
| `created_at DateTimeField` |
| `updated_at DateTimeField` |
| `event_id ForeignKey FK→events.Event (CASCADE)` |
| `event_source_id ForeignKey FK→events.EventSource (PROTECT) NULL` |
| `url CharField` |
| `offer_type CharField` |
| `status CharField` |

Constraints: ticket_offer_unique. Indexes: FK/PK/unique indexes.

## provenance

Intended sources: Future API, scraper, manual, and report import evidence. Empty domain tables do not imply any connector has run.

### Connection (`provenance_connection`)

Ownership: inherits from artists.Organization via organization.

| Column and type |
| --- |
| `id UUIDField PK UNIQUE` |
| `created_at DateTimeField` |
| `updated_at DateTimeField` |
| `organization_id ForeignKey FK→artists.Organization (PROTECT)` |
| `artist_id ForeignKey FK→artists.Artist (PROTECT) NULL` |
| `service_id ForeignKey FK→artists.Service (PROTECT)` |
| `auth_type CharField` |
| `secret_reference CharField` |
| `scopes JSONField` |
| `status CharField` |
| `expires_at DateTimeField NULL` |

Constraints: model field constraints only. Indexes: FK/PK/unique indexes.

### SyncJob (`provenance_syncjob`)

Ownership: inherits from provenance.Connection via connection.

| Column and type |
| --- |
| `id UUIDField PK UNIQUE` |
| `created_at DateTimeField` |
| `updated_at DateTimeField` |
| `connection_id ForeignKey FK→provenance.Connection (CASCADE)` |
| `job_type CharField` |
| `schedule CharField` |
| `cursor_json JSONField` |
| `next_run_at DateTimeField NULL` |
| `enabled BooleanField` |

Constraints: model field constraints only. Indexes: FK/PK/unique indexes.

### SyncRun (`provenance_syncrun`)

Ownership: inherits from provenance.SyncJob via sync_job.

| Column and type |
| --- |
| `id UUIDField PK UNIQUE` |
| `created_at DateTimeField` |
| `updated_at DateTimeField` |
| `sync_job_id ForeignKey FK→provenance.SyncJob (CASCADE)` |
| `status CharField` |
| `started_at DateTimeField NULL` |
| `finished_at DateTimeField NULL` |
| `counts_json JSONField` |
| `error_summary TextField` |

Constraints: model field constraints only. Indexes: FK/PK/unique indexes.

### SourceRecord (`provenance_sourcerecord`)

Ownership: inherits from artists.Organization via organization.

| Column and type |
| --- |
| `id UUIDField PK UNIQUE` |
| `created_at DateTimeField` |
| `updated_at DateTimeField` |
| `organization_id ForeignKey FK→artists.Organization (PROTECT)` |
| `sync_run_id ForeignKey FK→provenance.SyncRun (SET_NULL) NULL` |
| `service_id ForeignKey FK→artists.Service (PROTECT) NULL` |
| `collector CharField` |
| `collection_method CharField` |
| `parser_version CharField` |
| `object_type CharField` |
| `external_id CharField` |
| `payload_hash CharField` |
| `payload_json JSONField NULL` |
| `private_asset_id ForeignKey FK→catalog.Asset (PROTECT) NULL` |
| `observed_at DateTimeField` |
| `expires_at DateTimeField NULL` |

Constraints: source_has_payload. Indexes: provenance__organiz_f8adee_idx.

### EntityTarget (`provenance_entitytarget`)

Ownership: inherits from artists.Organization via organization.

| Column and type |
| --- |
| `id UUIDField PK UNIQUE` |
| `created_at DateTimeField` |
| `updated_at DateTimeField` |
| `organization_id ForeignKey FK→artists.Organization (PROTECT)` |
| `artist_id ForeignKey FK→artists.Artist (CASCADE) NULL` |
| `recording_id ForeignKey FK→catalog.Recording (CASCADE) NULL` |
| `release_id ForeignKey FK→catalog.Release (CASCADE) NULL` |
| `work_id ForeignKey FK→rights.MusicalWork (CASCADE) NULL` |
| `event_id ForeignKey FK→events.Event (CASCADE) NULL` |
| `website_id ForeignKey FK→publishing.Website (CASCADE) NULL` |

Constraints: entity_target_exactly_one. Indexes: FK/PK/unique indexes.

### FieldObservation (`provenance_fieldobservation`)

Ownership: inherits from provenance.SourceRecord via source_record.

| Column and type |
| --- |
| `id UUIDField PK UNIQUE` |
| `created_at DateTimeField` |
| `updated_at DateTimeField` |
| `source_record_id ForeignKey FK→provenance.SourceRecord (PROTECT)` |
| `target_id ForeignKey FK→provenance.EntityTarget (CASCADE)` |
| `field_name CharField` |
| `value_json JSONField` |
| `confidence DecimalField NULL` |

Constraints: model field constraints only. Indexes: FK/PK/unique indexes.

### EditorialOverride (`provenance_editorialoverride`)

Ownership: inherits from provenance.EntityTarget via target.

| Column and type |
| --- |
| `id UUIDField PK UNIQUE` |
| `created_at DateTimeField` |
| `updated_at DateTimeField` |
| `target_id ForeignKey FK→provenance.EntityTarget (CASCADE)` |
| `field_name CharField` |
| `value_json JSONField` |
| `user_id ForeignKey FK→core.User (PROTECT)` |
| `reason TextField` |

Constraints: model field constraints only. Indexes: FK/PK/unique indexes.

### ReviewItem (`provenance_reviewitem`)

Ownership: inherits from provenance.SourceRecord via source_record.

| Column and type |
| --- |
| `id UUIDField PK UNIQUE` |
| `created_at DateTimeField` |
| `updated_at DateTimeField` |
| `source_record_id ForeignKey FK→provenance.SourceRecord (PROTECT)` |
| `issue_type CharField` |
| `status CharField` |
| `resolved_by_id ForeignKey FK→core.User (PROTECT) NULL` |

Constraints: model field constraints only. Indexes: FK/PK/unique indexes.

### AuditEvent (`provenance_auditevent`)

Ownership: inherits from artists.Organization via organization.

| Column and type |
| --- |
| `id UUIDField PK UNIQUE` |
| `created_at DateTimeField` |
| `updated_at DateTimeField` |
| `organization_id ForeignKey FK→artists.Organization (PROTECT)` |
| `user_id ForeignKey FK→core.User (SET_NULL) NULL` |
| `action CharField` |
| `target_id ForeignKey FK→provenance.EntityTarget (SET_NULL) NULL` |
| `target_label CharField` |
| `change_json JSONField` |

Constraints: model field constraints only. Indexes: FK/PK/unique indexes.

### ReviewCandidate (`provenance_reviewcandidate`)

Ownership: inherits from provenance.ReviewItem via review_item.

| Column and type |
| --- |
| `id UUIDField PK UNIQUE` |
| `created_at DateTimeField` |
| `updated_at DateTimeField` |
| `review_item_id ForeignKey FK→provenance.ReviewItem (CASCADE)` |
| `target_id ForeignKey FK→provenance.EntityTarget (CASCADE)` |

Constraints: review_candidate_unique. Indexes: FK/PK/unique indexes.

## publishing

Intended sources: Staff approved website content and future publication worker. Empty domain tables do not imply any connector has run.

### Website (`publishing_website`)

Ownership: inherits from artists.Organization via organization.

| Column and type |
| --- |
| `id UUIDField PK UNIQUE` |
| `created_at DateTimeField` |
| `updated_at DateTimeField` |
| `organization_id ForeignKey FK→artists.Organization (PROTECT)` |
| `artist_id ForeignKey FK→artists.Artist (PROTECT)` |
| `domain CharField UNIQUE` |
| `repository CharField` |
| `hosting_provider CharField` |
| `locale CharField` |
| `theme_config JSONField` |

Constraints: model field constraints only. Indexes: FK/PK/unique indexes.

### Page (`publishing_page`)

Ownership: inherits from publishing.Website via website.

| Column and type |
| --- |
| `id UUIDField PK UNIQUE` |
| `created_at DateTimeField` |
| `updated_at DateTimeField` |
| `website_id ForeignKey FK→publishing.Website (CASCADE)` |
| `path CharField` |
| `page_type CharField` |
| `title CharField` |
| `content_json JSONField` |
| `publication_status CharField` |

Constraints: website_page_path_unique. Indexes: FK/PK/unique indexes.

### SEORecord (`publishing_seorecord`)

Ownership: inherits from publishing.Page via page.

| Column and type |
| --- |
| `id UUIDField PK UNIQUE` |
| `created_at DateTimeField` |
| `updated_at DateTimeField` |
| `page_id OneToOneField FK→publishing.Page (CASCADE) UNIQUE` |
| `title CharField` |
| `description TextField` |
| `canonical_url CharField` |
| `og_asset_id ForeignKey FK→catalog.Asset (PROTECT) NULL` |
| `robots CharField` |
| `structured_data_overrides JSONField` |

Constraints: model field constraints only. Indexes: FK/PK/unique indexes.

### FanlinkPage (`publishing_fanlinkpage`)

Ownership: inherits from publishing.Website via website.

| Column and type |
| --- |
| `id UUIDField PK UNIQUE` |
| `created_at DateTimeField` |
| `updated_at DateTimeField` |
| `website_id ForeignKey FK→publishing.Website (CASCADE)` |
| `recording_id ForeignKey FK→catalog.Recording (PROTECT) NULL` |
| `release_id ForeignKey FK→catalog.Release (PROTECT) NULL` |
| `slug SlugField` |
| `title CharField` |
| `status CharField` |

Constraints: fanlink_exactly_one_target; fanlink_website_slug_unique. Indexes: FK/PK/unique indexes.

### FanlinkDestination (`publishing_fanlinkdestination`)

Ownership: inherits from publishing.FanlinkPage via fanlink_page.

| Column and type |
| --- |
| `id UUIDField PK UNIQUE` |
| `created_at DateTimeField` |
| `updated_at DateTimeField` |
| `fanlink_page_id ForeignKey FK→publishing.FanlinkPage (CASCADE)` |
| `platform_object_id ForeignKey FK→catalog.PlatformObject (PROTECT)` |
| `label CharField` |
| `sort_order PositiveSmallIntegerField` |
| `enabled BooleanField` |

Constraints: fanlink_destination_unique. Indexes: FK/PK/unique indexes.

### PublicationOutbox (`publishing_publicationoutbox`)

Ownership: inherits from publishing.Website via website.

| Column and type |
| --- |
| `id UUIDField PK UNIQUE` |
| `created_at DateTimeField` |
| `updated_at DateTimeField` |
| `website_id ForeignKey FK→publishing.Website (CASCADE)` |
| `content_version PositiveBigIntegerField` |
| `idempotency_key CharField` |
| `status CharField` |
| `attempts PositiveSmallIntegerField` |
| `next_attempt_at DateTimeField NULL` |

Constraints: outbox_version_unique; outbox_idempotency_unique. Indexes: FK/PK/unique indexes.

### WebsitePublication (`publishing_websitepublication`)

Ownership: inherits from publishing.Website via website.

| Column and type |
| --- |
| `id UUIDField PK UNIQUE` |
| `created_at DateTimeField` |
| `updated_at DateTimeField` |
| `website_id ForeignKey FK→publishing.Website (CASCADE)` |
| `content_version PositiveBigIntegerField` |
| `snapshot_asset_id ForeignKey FK→catalog.Asset (PROTECT)` |
| `deployment_reference CharField` |
| `status CharField` |
| `published_at DateTimeField NULL` |

Constraints: publication_version_unique. Indexes: FK/PK/unique indexes.

## analytics

Intended sources: Future authorized reports and platform observations. Empty domain tables do not imply any connector has run.

### MetricDefinition (`analytics_metricdefinition`)

Ownership: staff governed; see architecture for access path.

| Column and type |
| --- |
| `id UUIDField PK UNIQUE` |
| `created_at DateTimeField` |
| `updated_at DateTimeField` |
| `service_id ForeignKey FK→artists.Service (PROTECT)` |
| `code CharField` |
| `unit CharField` |
| `aggregation_rule CharField` |
| `period_type CharField` |
| `definition_version PositiveSmallIntegerField` |

Constraints: metric_definition_version_unique. Indexes: FK/PK/unique indexes.

### MetricSeries (`analytics_metricseries`)

Ownership: inherits from artists.Organization via organization.

| Column and type |
| --- |
| `id UUIDField PK UNIQUE` |
| `created_at DateTimeField` |
| `updated_at DateTimeField` |
| `organization_id ForeignKey FK→artists.Organization (PROTECT)` |
| `metric_definition_id ForeignKey FK→analytics.MetricDefinition (PROTECT)` |
| `platform_object_id ForeignKey FK→catalog.PlatformObject (PROTECT)` |
| `dimensions_json JSONField` |
| `dimensions_hash CharField` |
| `source_method CharField` |

Constraints: metric_series_dimensions_unique. Indexes: FK/PK/unique indexes.

### MetricObservation (`analytics_metricobservation`)

Ownership: inherits from analytics.MetricSeries via series.

| Column and type |
| --- |
| `id UUIDField PK UNIQUE` |
| `created_at DateTimeField` |
| `updated_at DateTimeField` |
| `series_id ForeignKey FK→analytics.MetricSeries (CASCADE)` |
| `period_start DateTimeField` |
| `period_end DateTimeField` |
| `observed_at DateTimeField` |
| `value DecimalField NULL` |
| `source_record_id ForeignKey FK→provenance.SourceRecord (PROTECT) NULL` |
| `revision PositiveIntegerField` |
| `quality_status CharField` |

Constraints: metric_observation_revision_unique; metric_period_valid. Indexes: analytics_m_series__641a34_idx.

### ReportImport (`analytics_reportimport`)

Ownership: inherits from artists.Artist via artist.

| Column and type |
| --- |
| `id UUIDField PK UNIQUE` |
| `created_at DateTimeField` |
| `updated_at DateTimeField` |
| `artist_id ForeignKey FK→artists.Artist (PROTECT)` |
| `service_id ForeignKey FK→artists.Service (PROTECT)` |
| `asset_id ForeignKey FK→catalog.Asset (PROTECT)` |
| `file_hash CharField` |
| `report_type CharField` |
| `coverage_start DateField NULL` |
| `coverage_end DateField NULL` |
| `status CharField` |

Constraints: report_file_unique. Indexes: FK/PK/unique indexes.

### AudioAnalysis (`analytics_audioanalysis`)

Ownership: inherits from catalog.Recording via recording.

| Column and type |
| --- |
| `id UUIDField PK UNIQUE` |
| `created_at DateTimeField` |
| `updated_at DateTimeField` |
| `recording_id ForeignKey FK→catalog.Recording (CASCADE)` |
| `input_asset_id ForeignKey FK→catalog.Asset (PROTECT)` |
| `engine CharField` |
| `engine_version CharField` |
| `tempo DecimalField NULL` |
| `key CharField` |
| `loudness DecimalField NULL` |
| `features_json JSONField` |
| `analyzed_at DateTimeField` |

Constraints: model field constraints only. Indexes: FK/PK/unique indexes.

### PlaylistAppearance (`analytics_playlistappearance`)

Ownership: inherits from catalog.Recording via recording.

| Column and type |
| --- |
| `id UUIDField PK UNIQUE` |
| `created_at DateTimeField` |
| `updated_at DateTimeField` |
| `recording_id ForeignKey FK→catalog.Recording (CASCADE)` |
| `playlist_object_id ForeignKey FK→catalog.PlatformObject (PROTECT)` |
| `first_observed_at DateTimeField` |
| `last_observed_at DateTimeField` |
| `position PositiveIntegerField NULL` |
| `source_record_id ForeignKey FK→provenance.SourceRecord (PROTECT) NULL` |

Constraints: playlist_observation_dates_valid. Indexes: FK/PK/unique indexes.
