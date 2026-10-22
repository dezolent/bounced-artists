from django.apps import apps
from django.contrib.auth import get_user_model
from django.contrib.admin.models import LogEntry
from django.core.exceptions import PermissionDenied
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone

from artists.models import Artist, Organization, OrganizationMembership, ArtistResourceGrant, Service
from catalog.models import Recording, RecordingArtist
from events.models import Event, EventArtist
from provenance.models import AuditEvent, SourceRecord
from portal.operation_registry import DOMAIN_APPS, REGISTRY
from portal.operations import make_operation_form, operation, retire_operation, save_operation, version_token
from portal.services import StaleEdit


class OperationContractsTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        User=get_user_model()
        cls.staff=User.objects.create_user(username='operator',is_staff=True)
        cls.admin=User.objects.create_user(username='administrator',is_staff=True,is_superuser=True)
        cls.artist_user=User.objects.create_user(username='artist-account')
        cls.org=Organization.objects.create(name='Studio',slug='ops-studio')
        cls.other_org=Organization.objects.create(name='Other',slug='ops-other')
        cls.artist=Artist.objects.create(organization=cls.org,name='Artist',slug='ops-artist')
        cls.other_artist=Artist.objects.create(organization=cls.other_org,name='Other Artist',slug='ops-other-artist')
        cls.song=Recording.objects.create(organization=cls.org,title='Song')

    def payload(self,key,obj,actor=None,**values):
        spec=REGISTRY[key]
        form=make_operation_form(spec,actor or self.staff,obj)
        payload={}
        for name,field in form.fields.items():
            if name=='version':
                continue
            value=getattr(obj,name,None) if obj else None
            if hasattr(value,'pk'):
                value=str(value.pk)
            if value is not None:
                payload[name]=value
        payload.update(values)
        payload['version']=version_token(spec,obj,actor or self.staff)
        return payload

    def test_registry_covers_every_domain_model_explicitly(self):
        expected={model._meta.label_lower for app in DOMAIN_APPS for model in apps.get_app_config(app).get_models()}
        self.assertEqual(expected,{spec.label for spec in REGISTRY.values() if spec.model._meta.app_label in DOMAIN_APPS})
        self.assertGreater(len(expected),80)

    def test_credentials_payloads_and_private_storage_are_excluded(self):
        forbidden={'password','secret_reference','payload_json','payload','session_key','session_data','storage_key','file','preview_file','theme_config','content_json','structured_data_overrides','key_hash'}
        for spec in REGISTRY.values():
            self.assertFalse(forbidden & set(spec.fields),spec.label)
            self.assertFalse(forbidden & set(spec.edit_fields),spec.label)

    def test_generated_records_cannot_be_authored(self):
        for label in ('provenance-sourcerecord','provenance-syncrun','provenance-auditevent','catalog-assetversion','analytics-metricobservation','publishing-publicationoutbox','publishing-approvedcatalogrevision','portal-editorialchange'):
            self.assertFalse(REGISTRY[label].edit_fields,label)
            with self.assertRaises(PermissionDenied):
                save_operation(self.admin,label,{})

    def test_access_requires_active_staff_and_security_requires_superuser(self):
        with self.assertRaises(PermissionDenied):
            operation('catalog-recording',self.artist_user)
        with self.assertRaises(PermissionDenied):
            operation('artists-organization',self.staff)
        self.assertEqual(operation('artists-organization',self.admin).label,'artists.organization')

    def test_save_audits_and_checks_stale_form(self):
        payload=self.payload('catalog-recording',self.song,title='Edited song')
        saved,form=save_operation(self.staff,'catalog-recording',payload,str(self.song.pk))
        self.assertIsNotNone(saved,form.errors)
        self.assertEqual(saved.title,'Edited song')
        self.assertTrue(AuditEvent.objects.filter(action='portal.operations.update',organization=self.org).exists())
        self.assertTrue(LogEntry.objects.filter(user=self.staff).exists())
        with self.assertRaises(StaleEdit):
            save_operation(self.staff,'catalog-recording',payload,str(self.song.pk))

    def test_forged_privileges_and_hidden_fields_rejected(self):
        payload=self.payload('catalog-recording',self.song,is_staff='on')
        saved,form=save_operation(self.staff,'catalog-recording',payload,str(self.song.pk))
        self.assertIsNone(saved)
        self.assertIn('Unexpected fields',str(form.errors))

    def test_actor_privileges_rechecked_inside_transaction(self):
        payload=self.payload('catalog-recording',self.song,title='Forbidden')
        get_user_model().objects.filter(pk=self.staff.pk).update(is_active=False)
        with self.assertRaises(PermissionDenied):
            save_operation(self.staff,'catalog-recording',payload,str(self.song.pk))
        self.song.refresh_from_db()
        self.assertEqual(self.song.title,'Song')

    def test_cross_organization_event_artist_without_model_clean_is_rejected(self):
        event=Event.objects.create(organization=self.org,title='Show',local_date='2026-10-22')
        key='events-eventartist'
        spec=REGISTRY[key]
        payload={'event':str(event.pk),'artist':str(self.other_artist.pk),'billing_order':1,'performance_role':'performer','version':version_token(spec,None,self.staff)}
        saved,form=save_operation(self.staff,key,payload)
        self.assertIsNone(saved)
        self.assertIn('same organization',str(form.errors))
        self.assertFalse(EventArtist.objects.exists())

    def test_relationship_identity_cannot_be_moved(self):
        credit=RecordingArtist.objects.create(recording=self.song,artist=self.artist,role='primary')
        form=make_operation_form(REGISTRY['catalog-recordingartist'],self.staff,credit)
        self.assertNotIn('artist',form.fields)
        self.assertNotIn('recording',form.fields)

    def test_safe_url_validation(self):
        service=Service.objects.create(name='Platform',slug='ops-platform')
        key='catalog-platformobject'
        payload={'service':service.pk,'object_type':'recording','external_id':'','url':'https://127.0.0.1/private','title':'Unsafe','version':version_token(REGISTRY[key],None,self.staff)}
        saved,form=save_operation(self.staff,key,payload)
        self.assertIsNone(saved)
        self.assertIn('url',form.errors)

    def test_archive_preserves_identity_and_audit(self):
        spec=REGISTRY['catalog-recording']
        retire_operation(self.staff,spec.key,str(self.song.pk),version_token(spec,self.song,self.staff))
        self.song.refresh_from_db()
        self.assertIsNotNone(self.song.archived_at)
        self.assertTrue(AuditEvent.objects.filter(action='portal.operations.archive').exists())

    def test_superuser_can_revoke_explicit_grant_and_audit_is_preserved(self):
        grant=ArtistResourceGrant.objects.create(artist=self.artist,recording=self.song)
        spec=REGISTRY['artists-artistresourcegrant']
        token=version_token(spec,grant,self.admin)
        with self.assertRaises(PermissionDenied):
            retire_operation(self.staff,spec.key,str(grant.pk),token,revoke=True)
        retire_operation(self.admin,spec.key,str(grant.pk),token,revoke=True)
        self.assertFalse(ArtistResourceGrant.objects.filter(pk=grant.pk).exists())
        self.assertTrue(AuditEvent.objects.filter(action='portal.operations.revoke').exists())

    def test_hub_tables_details_and_create_forms_render(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse('portal:operations')).status_code,200)
        for key,spec in REGISTRY.items():
            with self.subTest(key=key):
                self.assertEqual(self.client.get(reverse('portal:operation-table',args=[key])).status_code,200)
                if spec.edit_fields and key!='catalog-asset':
                    self.assertEqual(self.client.get(reverse('portal:operation-create',args=[key])).status_code,200)
        self.assertContains(self.client.get(reverse('portal:operation-detail',args=['catalog-recording',self.song.pk])),'Song')

    def test_source_detail_never_displays_raw_payload(self):
        source=SourceRecord.objects.create(organization=self.org,collector='Fixture',collection_method='manual',object_type='test',payload_hash='a'*64,payload_json={'private':'SOURCE_PRIVATE_SENTINEL'},observed_at=timezone.now())
        self.client.force_login(self.staff)
        response=self.client.get(reverse('portal:operation-detail',args=['provenance-sourcerecord',source.pk]))
        self.assertEqual(response.status_code,200)
        self.assertNotContains(response,'SOURCE_PRIVATE_SENTINEL')

    def test_artist_denied_staff_routes_and_mutations_need_csrf(self):
        self.client.force_login(self.artist_user)
        self.assertEqual(self.client.get(reverse('portal:operations')).status_code,403)
        self.assertEqual(self.client.get(reverse('portal:operation-table',args=['catalog-recording'])).status_code,403)
        client=Client(enforce_csrf_checks=True)
        client.force_login(self.admin)
        response=client.post(reverse('portal:operation-edit',args=['catalog-recording',self.song.pk]),{'title':'Forged'})
        self.assertEqual(response.status_code,403)

    def test_related_table_links_filter_to_the_selected_parent(self):
        first=RecordingArtist.objects.create(recording=self.song,artist=self.artist,role='primary')
        other=Recording.objects.create(organization=self.org,title='Other song')
        RecordingArtist.objects.create(recording=other,artist=self.artist,role='primary')
        self.client.force_login(self.staff)
        response=self.client.get(reverse('portal:operation-table',args=['catalog-recordingartist']),{'related_model':'catalog-recording','related_id':str(self.song.pk),'related_field':'recording'})
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.context['page_obj'].paginator.count,1)
        self.assertEqual(response.context['page_obj'].object_list[0].pk,first.pk)

    def test_page_authoring_preserves_imported_content_and_edits_text(self):
        from publishing.models import Website
        website=Website.objects.create(organization=self.org,artist=self.artist,domain='ops.example.com')
        key='publishing-page'
        payload={'website':str(website.pk),'path':'/about/','page_type':'about','title':'About','editorial_text':'New biography','version':version_token(REGISTRY[key],None,self.staff)}
        page,form=save_operation(self.staff,key,payload)
        self.assertIsNotNone(page,form.errors)
        self.assertEqual(page.content_json['editorial']['text'],'New biography')
        page.content_json['source']='Preserved imported value'
        page.save()
        payload=self.payload(key,page,editorial_text='Updated biography')
        updated,form=save_operation(self.staff,key,payload,str(page.pk))
        self.assertIsNotNone(updated,form.errors)
        self.assertEqual(updated.content_json['source'],'Preserved imported value')
        self.assertEqual(updated.content_json['editorial']['text'],'Updated biography')

    def test_missing_required_relationships_produce_form_errors(self):
        for key in ('publishing-fanlinkpage','rights-rightsclaim','catalog-recordingartist','publishing-seorecord'):
            saved,form=save_operation(self.staff,key,{'version':version_token(REGISTRY[key],None,self.staff)})
            self.assertIsNone(saved)
            self.assertTrue(form.errors)

    def test_creating_reference_and_artist_records_is_audited(self):
        payload={'name':'Created genre','slug':'ops-created-genre','version':version_token(REGISTRY['catalog-genre'],None,self.staff)}
        genre,form=save_operation(self.staff,'catalog-genre',payload)
        self.assertIsNotNone(genre,form.errors)
        key='artists-artist'
        payload={'organization':str(self.org.pk),'name':'Created artist','slug':'ops-created-artist','management_status':'managed','bio_short':'','bio_long':'','location':'','timezone':'America/New_York','version':version_token(REGISTRY[key],None,self.staff)}
        artist,form=save_operation(self.staff,key,payload)
        self.assertIsNotNone(artist,form.errors)
        self.assertFalse(artist.memberships.exists())
        self.assertFalse(artist.resource_grants.exists())
        self.assertTrue(AuditEvent.objects.filter(action='portal.operations.create',target_label='artists.artist').exists())

    def test_secondary_types_are_list_values_without_raw_json_forms(self):
        from catalog.models import ReleaseGroup
        group=ReleaseGroup.objects.create(organization=self.org,title='Group')
        key='catalog-releasegroup'
        payload=self.payload(key,group,secondary_types='compilation, live')
        saved,form=save_operation(self.staff,key,payload,str(group.pk))
        self.assertIsNotNone(saved,form.errors)
        self.assertEqual(saved.secondary_types,['compilation','live'])
        payload=self.payload(key,saved,secondary_types=','.join(['x']*11))
        invalid,form=save_operation(self.staff,key,payload,str(group.pk))
        self.assertIsNone(invalid)
        self.assertIn('secondary_types',form.errors)

    def test_assigned_child_links_include_guarded_workspace_remove(self):
        Artist.objects.filter(pk=self.artist.pk).update(management_status='managed')
        ArtistResourceGrant.objects.create(artist=self.artist,recording=self.song,capability='manage')
        credit=RecordingArtist.objects.create(recording=self.song,artist=self.artist,role='primary')
        self.client.force_login(self.staff)
        response=self.client.get(reverse('portal:operation-detail',args=['catalog-recordingartist',credit.pk]))
        self.assertContains(response,reverse('portal:delete',args=[self.artist.pk,'recording-credits',credit.pk]))

    def approved_fixture(self,artwork=None):
        from publishing import projection
        from publishing.models import ApprovedCatalogRevision, Website, WebsiteApprovedState
        Artist.objects.filter(pk=self.artist.pk).update(management_status='managed')
        self.artist.refresh_from_db()
        website=Website.objects.create(organization=self.org,artist=self.artist,domain='public-ops.example.com')
        payload={'artist':projection.preview_catalog(self.staff,website,{}).payload['artist'],'recordings':[],'releases':[],'fanlinks':[]}
        selection={'recordings':[str(self.song.pk)],'releases':[],'fanlinks':[],'artwork':[str(artwork.pk)] if artwork else []}
        revision=ApprovedCatalogRevision.objects.create(website=website,schema_version=1,content_version=1,approver=self.staff,payload=payload,selection=selection,checksum=projection.digest(payload),source_checksum='a'*64)
        return WebsiteApprovedState.objects.create(website=website,active_revision=revision,approved_slug=self.artist.slug)

    def test_editorial_edits_preserve_snapshot_and_archive_withdraws_it(self):
        state=self.approved_fixture()
        previous=state.active_revision_id
        key='catalog-recording'
        saved,form=save_operation(self.staff,key,self.payload(key,self.song,title='Private revision'),str(self.song.pk))
        self.assertIsNotNone(saved,form.errors)
        state.refresh_from_db()
        self.assertEqual(state.active_revision_id,previous)
        retire_operation(self.staff,key,str(saved.pk),version_token(REGISTRY[key],saved,self.staff))
        state.refresh_from_db()
        self.assertIsNone(state.active_revision_id)

    def test_asset_rights_withdrawal_revokes_snapshot(self):
        from catalog.models import Asset, AssetVersion
        asset=Asset.objects.create(organization=self.org,storage_key='private/fixture',display_name='Cover',visibility='public',rights_basis='Owned',rights_approved=True)
        version=AssetVersion.objects.create(asset=asset,file='private/fixture.jpg',original_name='fixture.jpg',byte_size=10,mime_type='image/jpeg',sha256='a'*64)
        asset.current_version=version
        asset.save()
        state=self.approved_fixture(artwork=asset)
        key='catalog-asset'
        payload=self.payload(key,asset,rights_approved=False)
        saved,form=save_operation(self.staff,key,payload,str(asset.pk))
        self.assertIsNotNone(saved,form.errors)
        state.refresh_from_db()
        self.assertIsNone(state.active_revision_id)

    def test_existing_signed_urls_and_session_keys_remain_redacted(self):
        from catalog.models import PlatformObject
        from django.contrib.sessions.models import Session
        service=Service.objects.create(name='Imported platform',slug='ops-imported')
        target=PlatformObject.objects.create(service=service,object_type='recording',url='https://example.com/song?token=URL_SECRET_SENTINEL')
        Session.objects.create(session_key='SESSION_SECRET_SENTINEL',session_data='PRIVATE_SESSION_PAYLOAD',expire_date=timezone.now())
        self.client.force_login(self.admin)
        response=self.client.get(reverse('portal:operation-detail',args=['catalog-platformobject',target.pk]))
        self.assertNotContains(response,'URL_SECRET_SENTINEL')
        self.assertContains(response,'URL withheld')
        response=self.client.get(reverse('portal:operation-table',args=['sessions-session']))
        self.assertNotContains(response,'SESSION_SECRET_SENTINEL')
        self.assertNotContains(response,'PRIVATE_SESSION_PAYLOAD')

    def test_url_edit_forms_redact_initial_and_invalid_post_values(self):
        from catalog.models import PlatformObject
        service=Service.objects.create(name='Private imported URL',slug='ops-private-url')
        target=PlatformObject.objects.create(service=service,object_type='recording',title='Private destination',url='https://example.com/song?token=INITIAL_URL_SECRET')
        key='catalog-platformobject'
        self.client.force_login(self.staff)
        url=reverse('portal:operation-edit',args=[key,target.pk])
        response=self.client.get(url)
        self.assertEqual(response.status_code,200)
        self.assertNotContains(response,'INITIAL_URL_SECRET')
        payload=self.payload(key,target,url='https://example.com/song?token=POSTED_URL_SECRET')
        response=self.client.post(url,payload)
        self.assertEqual(response.status_code,400)
        self.assertNotContains(response,'POSTED_URL_SECRET',status_code=400)
        self.assertNotContains(response,'INITIAL_URL_SECRET',status_code=400)
        target.refresh_from_db()
        self.assertIn('INITIAL_URL_SECRET',target.url)
        payload=self.payload(key,target,url='',title='Changed title')
        response=self.client.post(url,payload)
        self.assertEqual(response.status_code,302)
        target.refresh_from_db()
        self.assertIn('INITIAL_URL_SECRET',target.url)
        self.assertEqual(target.title,'Changed title')
        payload=self.payload(key,target,url='https://example.com/safe')
        response=self.client.post(url,payload)
        self.assertEqual(response.status_code,302)
        target.refresh_from_db()
        self.assertEqual(target.url,'https://example.com/safe')

    def source_claim_fixture(self):
        from rights.models import Party, MusicalWork, WorkRegistration, RightsSociety, RightsClaim
        source=SourceRecord.objects.create(organization=self.org,collector='Fixture',collection_method='manual',object_type='claim',payload_hash='c'*64,payload_json={'assertion':'fixture'},observed_at=timezone.now())
        work=MusicalWork.objects.create(organization=self.org,title='Asserted work')
        party=Party.objects.create(organization=self.org,display_name='Claimant',party_type='person')
        society=RightsSociety.objects.create(name='Society',code='OPS',society_type='performance')
        registration=WorkRegistration.objects.create(work=work,society=society)
        return RightsClaim.objects.create(registration=registration,party=party,right_type='performance',share_value=50,source_record=source)

    def test_source_bound_rights_claims_cannot_be_edited_removed_or_detached(self):
        from portal.operations import change_removal
        claim=self.source_claim_fixture()
        key='rights-rightsclaim'
        token=version_token(REGISTRY[key],claim,self.staff)
        with self.assertRaises(PermissionDenied):
            save_operation(self.staff,key,{'version':token,'share_value':'100','source_record':''},str(claim.pk))
        with self.assertRaises(PermissionDenied):
            change_removal(self.staff,key,str(claim.pk),token)
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(reverse('portal:operation-edit',args=[key,claim.pk])).status_code,403)
        self.assertEqual(self.client.post(reverse('portal:operation-edit',args=[key,claim.pk]),{'version':token,'share_value':'100'}).status_code,403)
        response=self.client.get(reverse('portal:operation-detail',args=[key,claim.pk]))
        self.assertEqual(response.status_code,200)
        self.assertNotContains(response,reverse('portal:operation-edit',args=[key,claim.pk]))
        self.assertNotContains(response,reverse('portal:operation-remove',args=[key,claim.pk]))
        claim.refresh_from_db()
        self.assertEqual(claim.share_value,50)
        self.assertIsNotNone(claim.source_record_id)

    def test_global_unassigned_child_can_be_removed_and_restored_with_identity(self):
        from portal.models import OperationRemoval
        from portal.operations import change_removal
        from portal.querysets import active_rows
        credit=RecordingArtist.objects.create(recording=self.song,artist=self.artist,role='primary')
        key='catalog-recordingartist'
        token=version_token(REGISTRY[key],credit,self.staff)
        self.assertFalse(ArtistResourceGrant.objects.filter(recording=self.song).exists())
        change_removal(self.staff,key,str(credit.pk),token)
        self.assertTrue(RecordingArtist.objects.filter(pk=credit.pk).exists())
        self.assertFalse(active_rows(RecordingArtist.objects.all()).filter(pk=credit.pk).exists())
        marker=OperationRemoval.objects.get(model_label='catalog.recordingartist',object_id=str(credit.pk))
        self.assertEqual(marker.organization_id,self.org.pk)
        self.assertEqual(marker.removed_by_id,self.staff.pk)
        self.assertIsNone(marker.restored_at)
        from portal.models import EditorialChange
        self.assertFalse(EditorialChange.objects.filter(model_label='catalog.recordingartist',object_id=str(credit.pk)).exists())
        self.assertTrue(AuditEvent.objects.filter(action='portal.operations.remove').exists())
        with self.assertRaises(StaleEdit):
            change_removal(self.staff,key,str(credit.pk),token,restore=True)
        self.client.force_login(self.staff)
        response=self.client.get(reverse('portal:operation-detail',args=[key,credit.pk]))
        self.assertContains(response,reverse('portal:operation-restore',args=[key,credit.pk]))
        self.assertEqual(self.client.get(reverse('portal:operation-edit',args=[key,credit.pk])).status_code,403)
        change_removal(self.staff,key,str(credit.pk),version_token(REGISTRY[key],credit,self.staff),restore=True)
        marker.refresh_from_db()
        self.assertEqual(marker.restored_by_id,self.staff.pk)
        self.assertIsNotNone(marker.restored_at)
        self.assertTrue(active_rows(RecordingArtist.objects.all()).filter(pk=credit.pk).exists())
        self.assertTrue(AuditEvent.objects.filter(action='portal.operations.restore').exists())

    def test_operations_restore_existing_workspace_removal(self):
        from portal.models import EditorialChange, OperationRemoval
        from portal.operations import change_removal
        from portal.querysets import active_rows
        credit=RecordingArtist.objects.create(recording=self.song,artist=self.artist,role='primary')
        EditorialChange.objects.create(organization=self.org,artist=self.artist,user=self.staff,model_label='catalog.recordingartist',object_id=str(credit.pk),action='delete')
        self.assertFalse(active_rows(RecordingArtist.objects.all()).filter(pk=credit.pk).exists())
        key='catalog-recordingartist'
        self.client.force_login(self.staff)
        response=self.client.get(reverse('portal:operation-detail',args=[key,credit.pk]))
        self.assertContains(response,reverse('portal:operation-restore',args=[key,credit.pk]))
        change_removal(self.staff,key,str(credit.pk),version_token(REGISTRY[key],credit,self.staff),restore=True)
        self.assertTrue(active_rows(RecordingArtist.objects.all()).filter(pk=credit.pk).exists())
        self.assertTrue(OperationRemoval.objects.filter(object_id=str(credit.pk),restored_at__isnull=False).exists())

    def test_global_relationship_removal_cannot_target_history_or_grant_access(self):
        from portal.operations import change_removal
        credit=RecordingArtist.objects.create(recording=self.song,artist=self.artist,role='primary')
        key='catalog-recordingartist'
        token=version_token(REGISTRY[key],credit,self.staff)
        with self.assertRaises(PermissionDenied):
            change_removal(self.artist_user,key,str(credit.pk),token)
        for key in ('artists-service','provenance-sourcerecord','portal-editorialchange','artists-artistresourcegrant'):
            with self.assertRaises(PermissionDenied):
                change_removal(self.admin,key,str(credit.pk),token)
        client=Client(enforce_csrf_checks=True)
        client.force_login(self.staff)
        self.assertEqual(client.post(reverse('portal:operation-remove',args=['catalog-recordingartist',credit.pk]),{'version':token}).status_code,403)

    def test_workspace_recreation_restores_globally_removed_relationship_uuid(self):
        from catalog.models import Genre, RecordingGenre
        from portal.models import OperationRemoval
        from portal.operations import change_removal
        from portal.querysets import active_rows
        from portal.services import mutate
        Artist.objects.filter(pk=self.artist.pk).update(management_status='managed')
        self.artist.refresh_from_db()
        ArtistResourceGrant.objects.create(artist=self.artist,recording=self.song,capability='manage')
        genre=Genre.objects.create(name='Electronic',slug='ops-electronic')
        link=RecordingGenre.objects.create(recording=self.song,genre=genre)
        key='catalog-recordinggenre'
        change_removal(self.staff,key,str(link.pk),version_token(REGISTRY[key],link,self.staff))
        self.assertFalse(active_rows(RecordingGenre.objects.all()).filter(pk=link.pk).exists())
        restored,form=mutate(self.staff,self.artist,'recording-genres',{'recording':str(self.song.pk),'genre':str(genre.pk)})
        self.assertIsNotNone(restored,form.errors)
        self.assertEqual(restored.pk,link.pk)
        self.assertTrue(active_rows(RecordingGenre.objects.all()).filter(pk=link.pk).exists())
        marker=OperationRemoval.objects.get(model_label='catalog.recordinggenre',object_id=str(link.pk))
        self.assertIsNotNone(marker.restored_at)
        self.assertEqual(marker.restored_by_id,self.staff.pk)
        self.assertEqual(RecordingGenre.objects.filter(recording=self.song,genre=genre).count(),1)
