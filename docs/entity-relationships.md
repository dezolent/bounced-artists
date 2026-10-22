# Entity relationships

Physical tables use Django app prefixes and snake case model names. Each view includes all models in its domain; edges to another domain show foreign keys without implying shared access.

## artists

```mermaid
erDiagram
    ORGANIZATION {
        uuid id PK
    }
    ORGANIZATIONMEMBERSHIP {
        uuid id PK
    }
    ARTIST {
        uuid id PK
    }
    ARTISTMEMBERSHIP {
        bigint id PK
    }
    ARTISTALIAS {
        bigint id PK
    }
    ARTISTCONTACT {
        bigint id PK
    }
    SERVICE {
        bigint id PK
    }
    ARTISTPROFILELINK {
        bigint id PK
    }
    ARTISTRESOURCEGRANT {
        uuid id PK
    }
    ORGANIZATION ||--o{ ORGANIZATIONMEMBERSHIP : "organization"
    USER ||--o{ ORGANIZATIONMEMBERSHIP : "user"
    ORGANIZATION ||--o{ ARTIST : "organization"
    USER ||--o{ ARTISTMEMBERSHIP : "user"
    ARTIST ||--o{ ARTISTMEMBERSHIP : "artist"
    ARTIST ||--o{ ARTISTALIAS : "artist"
    ARTIST ||--o{ ARTISTCONTACT : "artist"
    ARTIST ||--o{ ARTISTPROFILELINK : "artist"
    SERVICE ||--o{ ARTISTPROFILELINK : "platform"
    ARTIST ||--o{ ARTISTRESOURCEGRANT : "artist"
    RECORDING ||--o{ ARTISTRESOURCEGRANT : "recording"
    RELEASE ||--o{ ARTISTRESOURCEGRANT : "release"
    ASSET ||--o{ ARTISTRESOURCEGRANT : "asset"
    MUSICALWORK ||--o{ ARTISTRESOURCEGRANT : "work"
    PARTY ||--o{ ARTISTRESOURCEGRANT : "party"
    EVENT ||--o{ ARTISTRESOURCEGRANT : "event"
    LABEL ||--o{ ARTISTRESOURCEGRANT : "label"
    RELEASEGROUP ||--o{ ARTISTRESOURCEGRANT : "release_group"
```

## catalog

```mermaid
erDiagram
    LABEL {
        uuid id PK
    }
    RELEASEGROUP {
        uuid id PK
    }
    RECORDING {
        uuid id PK
    }
    RECORDINGARTIST {
        uuid id PK
    }
    RECORDINGIDENTIFIER {
        uuid id PK
    }
    RECORDINGRELATION {
        uuid id PK
    }
    RELEASE {
        uuid id PK
    }
    RELEASEARTIST {
        uuid id PK
    }
    RELEASEIDENTIFIER {
        uuid id PK
    }
    MEDIUM {
        uuid id PK
    }
    RELEASETRACK {
        uuid id PK
    }
    GENRE {
        uuid id PK
    }
    ARTISTGENRE {
        uuid id PK
    }
    RECORDINGGENRE {
        uuid id PK
    }
    RELEASEGENRE {
        uuid id PK
    }
    RELEASEGROUPGENRE {
        uuid id PK
    }
    RECORDINGLABEL {
        uuid id PK
    }
    RELEASELABEL {
        uuid id PK
    }
    ASSET {
        uuid id PK
    }
    ASSETVERSION {
        uuid id PK
    }
    ARTISTASSET {
        uuid id PK
    }
    RELEASEASSET {
        uuid id PK
    }
    RECORDINGASSET {
        uuid id PK
    }
    PLATFORMOBJECT {
        uuid id PK
    }
    RECORDINGPLATFORMMATCH {
        uuid id PK
    }
    RELEASEPLATFORMMATCH {
        uuid id PK
    }
    ARTISTPLATFORMMATCH {
        uuid id PK
    }
    PLATFORMAVAILABILITY {
        uuid id PK
    }
    ORGANIZATION ||--o{ LABEL : "organization"
    PARTY ||--o{ LABEL : "party"
    ORGANIZATION ||--o{ RELEASEGROUP : "organization"
    ORGANIZATION ||--o{ RECORDING : "organization"
    RECORDING ||--o{ RECORDINGARTIST : "recording"
    ARTIST ||--o{ RECORDINGARTIST : "artist"
    RECORDING ||--o{ RECORDINGIDENTIFIER : "recording"
    SOURCERECORD ||--o{ RECORDINGIDENTIFIER : "source_record"
    RECORDING ||--o{ RECORDINGRELATION : "from_recording"
    RECORDING ||--o{ RECORDINGRELATION : "to_recording"
    ORGANIZATION ||--o{ RELEASE : "organization"
    RELEASEGROUP ||--o{ RELEASE : "release_group"
    PARTY ||--o{ RELEASE : "label_party"
    RELEASE ||--o{ RELEASEARTIST : "release"
    ARTIST ||--o{ RELEASEARTIST : "artist"
    RELEASE ||--o{ RELEASEIDENTIFIER : "release"
    SOURCERECORD ||--o{ RELEASEIDENTIFIER : "source_record"
    RELEASE ||--o{ MEDIUM : "release"
    RELEASE ||--o{ RELEASETRACK : "release"
    RECORDING ||--o{ RELEASETRACK : "recording"
    MEDIUM ||--o{ RELEASETRACK : "medium"
    ARTIST ||--o{ ARTISTGENRE : "artist"
    GENRE ||--o{ ARTISTGENRE : "genre"
    RECORDING ||--o{ RECORDINGGENRE : "recording"
    GENRE ||--o{ RECORDINGGENRE : "genre"
    RELEASE ||--o{ RELEASEGENRE : "release"
    GENRE ||--o{ RELEASEGENRE : "genre"
    RELEASEGROUP ||--o{ RELEASEGROUPGENRE : "release_group"
    GENRE ||--o{ RELEASEGROUPGENRE : "genre"
    RECORDING ||--o{ RECORDINGLABEL : "recording"
    LABEL ||--o{ RECORDINGLABEL : "label"
    RELEASE ||--o{ RELEASELABEL : "release"
    LABEL ||--o{ RELEASELABEL : "label"
    ORGANIZATION ||--o{ ASSET : "organization"
    ASSETVERSION ||--o{ ASSET : "current_version"
    ASSET ||--o{ ASSETVERSION : "asset"
    USER ||--o{ ASSETVERSION : "creator"
    ARTIST ||--o{ ARTISTASSET : "artist"
    ASSET ||--o{ ARTISTASSET : "asset"
    RELEASE ||--o{ RELEASEASSET : "release"
    ASSET ||--o{ RELEASEASSET : "asset"
    RECORDING ||--o{ RECORDINGASSET : "recording"
    ASSET ||--o{ RECORDINGASSET : "asset"
    SERVICE ||--o{ PLATFORMOBJECT : "service"
    RECORDING ||--o{ RECORDINGPLATFORMMATCH : "recording"
    PLATFORMOBJECT ||--o{ RECORDINGPLATFORMMATCH : "platform_object"
    RELEASE ||--o{ RELEASEPLATFORMMATCH : "release"
    PLATFORMOBJECT ||--o{ RELEASEPLATFORMMATCH : "platform_object"
    ARTIST ||--o{ ARTISTPLATFORMMATCH : "artist"
    PLATFORMOBJECT ||--o{ ARTISTPLATFORMMATCH : "platform_object"
    PLATFORMOBJECT ||--o{ PLATFORMAVAILABILITY : "platform_object"
```

## rights

```mermaid
erDiagram
    PARTY {
        uuid id PK
    }
    PARTYIDENTIFIER {
        uuid id PK
    }
    ARTISTPARTY {
        uuid id PK
    }
    RIGHTSSOCIETY {
        uuid id PK
    }
    PARTYAFFILIATION {
        uuid id PK
    }
    MUSICALWORK {
        uuid id PK
    }
    WORKIDENTIFIER {
        uuid id PK
    }
    WORKALIAS {
        uuid id PK
    }
    WORKCONTRIBUTOR {
        uuid id PK
    }
    RECORDINGWORK {
        uuid id PK
    }
    WORKREGISTRATION {
        uuid id PK
    }
    RIGHTSCLAIM {
        uuid id PK
    }
    ORGANIZATION ||--o{ PARTY : "organization"
    PARTY ||--o{ PARTYIDENTIFIER : "party"
    ARTIST ||--o{ ARTISTPARTY : "artist"
    PARTY ||--o{ ARTISTPARTY : "party"
    PARTY ||--o{ PARTYAFFILIATION : "party"
    RIGHTSSOCIETY ||--o{ PARTYAFFILIATION : "society"
    ORGANIZATION ||--o{ MUSICALWORK : "organization"
    MUSICALWORK ||--o{ WORKIDENTIFIER : "work"
    SOURCERECORD ||--o{ WORKIDENTIFIER : "source_record"
    MUSICALWORK ||--o{ WORKALIAS : "work"
    MUSICALWORK ||--o{ WORKCONTRIBUTOR : "work"
    PARTY ||--o{ WORKCONTRIBUTOR : "party"
    RECORDING ||--o{ RECORDINGWORK : "recording"
    MUSICALWORK ||--o{ RECORDINGWORK : "work"
    MUSICALWORK ||--o{ WORKREGISTRATION : "work"
    RIGHTSSOCIETY ||--o{ WORKREGISTRATION : "society"
    WORKREGISTRATION ||--o{ RIGHTSCLAIM : "registration"
    PARTY ||--o{ RIGHTSCLAIM : "party"
    SOURCERECORD ||--o{ RIGHTSCLAIM : "source_record"
```

## events

```mermaid
erDiagram
    VENUE {
        uuid id PK
    }
    EVENT {
        uuid id PK
    }
    EVENTARTIST {
        uuid id PK
    }
    EVENTSOURCE {
        uuid id PK
    }
    TICKETOFFER {
        uuid id PK
    }
    ORGANIZATION ||--o{ EVENT : "organization"
    VENUE ||--o{ EVENT : "venue"
    EVENT ||--o{ EVENTARTIST : "event"
    ARTIST ||--o{ EVENTARTIST : "artist"
    EVENT ||--o{ EVENTSOURCE : "event"
    SERVICE ||--o{ EVENTSOURCE : "service"
    EVENT ||--o{ TICKETOFFER : "event"
    EVENTSOURCE ||--o{ TICKETOFFER : "event_source"
```

## provenance

```mermaid
erDiagram
    CONNECTION {
        uuid id PK
    }
    SYNCJOB {
        uuid id PK
    }
    SYNCRUN {
        uuid id PK
    }
    SOURCERECORD {
        uuid id PK
    }
    ENTITYTARGET {
        uuid id PK
    }
    FIELDOBSERVATION {
        uuid id PK
    }
    EDITORIALOVERRIDE {
        uuid id PK
    }
    REVIEWITEM {
        uuid id PK
    }
    AUDITEVENT {
        uuid id PK
    }
    REVIEWCANDIDATE {
        uuid id PK
    }
    ORGANIZATION ||--o{ CONNECTION : "organization"
    ARTIST ||--o{ CONNECTION : "artist"
    SERVICE ||--o{ CONNECTION : "service"
    CONNECTION ||--o{ SYNCJOB : "connection"
    SYNCJOB ||--o{ SYNCRUN : "sync_job"
    ORGANIZATION ||--o{ SOURCERECORD : "organization"
    SYNCRUN ||--o{ SOURCERECORD : "sync_run"
    SERVICE ||--o{ SOURCERECORD : "service"
    ASSET ||--o{ SOURCERECORD : "private_asset"
    ORGANIZATION ||--o{ ENTITYTARGET : "organization"
    ARTIST ||--o{ ENTITYTARGET : "artist"
    RECORDING ||--o{ ENTITYTARGET : "recording"
    RELEASE ||--o{ ENTITYTARGET : "release"
    MUSICALWORK ||--o{ ENTITYTARGET : "work"
    EVENT ||--o{ ENTITYTARGET : "event"
    WEBSITE ||--o{ ENTITYTARGET : "website"
    SOURCERECORD ||--o{ FIELDOBSERVATION : "source_record"
    ENTITYTARGET ||--o{ FIELDOBSERVATION : "target"
    ENTITYTARGET ||--o{ EDITORIALOVERRIDE : "target"
    USER ||--o{ EDITORIALOVERRIDE : "user"
    SOURCERECORD ||--o{ REVIEWITEM : "source_record"
    USER ||--o{ REVIEWITEM : "resolved_by"
    ORGANIZATION ||--o{ AUDITEVENT : "organization"
    USER ||--o{ AUDITEVENT : "user"
    ENTITYTARGET ||--o{ AUDITEVENT : "target"
    REVIEWITEM ||--o{ REVIEWCANDIDATE : "review_item"
    ENTITYTARGET ||--o{ REVIEWCANDIDATE : "target"
```

## publishing

```mermaid
erDiagram
    WEBSITE {
        uuid id PK
    }
    PAGE {
        uuid id PK
    }
    SEORECORD {
        uuid id PK
    }
    FANLINKPAGE {
        uuid id PK
    }
    FANLINKDESTINATION {
        uuid id PK
    }
    PUBLICATIONOUTBOX {
        uuid id PK
    }
    WEBSITEPUBLICATION {
        uuid id PK
    }
    APPROVEDCATALOGREVISION {
        uuid id PK
    }
    WEBSITEAPPROVEDSTATE {
        onetoonefield website_id PK
    }
    APPROVEDARTWORK {
        bigint id PK
    }
    ORGANIZATION ||--o{ WEBSITE : "organization"
    ARTIST ||--o{ WEBSITE : "artist"
    WEBSITE ||--o{ PAGE : "website"
    PAGE ||--o{ SEORECORD : "page"
    ASSET ||--o{ SEORECORD : "og_asset"
    WEBSITE ||--o{ FANLINKPAGE : "website"
    RECORDING ||--o{ FANLINKPAGE : "recording"
    RELEASE ||--o{ FANLINKPAGE : "release"
    FANLINKPAGE ||--o{ FANLINKDESTINATION : "fanlink_page"
    PLATFORMOBJECT ||--o{ FANLINKDESTINATION : "platform_object"
    WEBSITE ||--o{ PUBLICATIONOUTBOX : "website"
    WEBSITE ||--o{ WEBSITEPUBLICATION : "website"
    ASSET ||--o{ WEBSITEPUBLICATION : "snapshot_asset"
    WEBSITE ||--o{ APPROVEDCATALOGREVISION : "website"
    USER ||--o{ APPROVEDCATALOGREVISION : "approver"
    WEBSITE ||--o{ WEBSITEAPPROVEDSTATE : "website"
    APPROVEDCATALOGREVISION ||--o{ WEBSITEAPPROVEDSTATE : "active_revision"
    APPROVEDCATALOGREVISION ||--o{ APPROVEDARTWORK : "revision"
    ASSETVERSION ||--o{ APPROVEDARTWORK : "version"
```

## analytics

```mermaid
erDiagram
    METRICDEFINITION {
        uuid id PK
    }
    METRICSERIES {
        uuid id PK
    }
    METRICOBSERVATION {
        uuid id PK
    }
    REPORTIMPORT {
        uuid id PK
    }
    AUDIOANALYSIS {
        uuid id PK
    }
    PLAYLISTAPPEARANCE {
        uuid id PK
    }
    SERVICE ||--o{ METRICDEFINITION : "service"
    ORGANIZATION ||--o{ METRICSERIES : "organization"
    METRICDEFINITION ||--o{ METRICSERIES : "metric_definition"
    PLATFORMOBJECT ||--o{ METRICSERIES : "platform_object"
    METRICSERIES ||--o{ METRICOBSERVATION : "series"
    SOURCERECORD ||--o{ METRICOBSERVATION : "source_record"
    ARTIST ||--o{ REPORTIMPORT : "artist"
    SERVICE ||--o{ REPORTIMPORT : "service"
    ASSET ||--o{ REPORTIMPORT : "asset"
    RECORDING ||--o{ AUDIOANALYSIS : "recording"
    ASSET ||--o{ AUDIOANALYSIS : "input_asset"
    RECORDING ||--o{ PLAYLISTAPPEARANCE : "recording"
    PLATFORMOBJECT ||--o{ PLAYLISTAPPEARANCE : "playlist_object"
    SOURCERECORD ||--o{ PLAYLISTAPPEARANCE : "source_record"
```
