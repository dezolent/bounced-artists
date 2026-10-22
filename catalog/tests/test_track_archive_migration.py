"""Exercise historical tombstone migration and its safe downgrade guard."""
from datetime import timedelta

from django.db import connection
from django.db.migrations.exceptions import IrreversibleError
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.utils import timezone


class TrackArchiveMigrationTests(TransactionTestCase):
    old_target = ('catalog', '0009_catalog_mvp')
    new_target = ('catalog', '0010_releasetrack_editorial_archive')
    portal_target = ('portal', '0002_catalogreviewrequest')

    def setUp(self):
        super().setUp()
        executor = MigrationExecutor(connection)
        self.leaf_targets = executor.loader.graph.leaf_nodes()
        executor.migrate([self.old_target])
        self.addCleanup(self.restore_schema)
        self.old_apps = executor.loader.project_state([self.old_target, self.portal_target]).apps
        organization = self.old_apps.get_model('artists', 'Organization').objects.create(name='Archive migration', slug='archive-migration')
        self.artist = self.old_apps.get_model('artists', 'Artist').objects.create(organization=organization, name='Archive artist', slug='archive-artist')
        self.recording = self.old_apps.get_model('catalog', 'Recording').objects.create(organization=organization, title='Retained recording')
        self.release = self.old_apps.get_model('catalog', 'Release').objects.create(organization=organization, title='Retained release')
        self.deleted_at = timezone.now() - timedelta(days=2)
        self.restored_at = self.deleted_at + timedelta(days=1)

    def restore_schema(self):
        MigrationExecutor(connection).migrate(self.leaf_targets)

    def track(self, number):
        return self.old_apps.get_model('catalog', 'ReleaseTrack').objects.create(release=self.release, recording=self.recording, disc_number=1, track_number=number, display_title=f'Original {number}')

    def change(self, track, action, when, model_label='catalog.releasetrack'):
        model = self.old_apps.get_model('portal', 'EditorialChange')
        row = model.objects.create(organization_id=self.artist.organization_id, artist_id=self.artist.pk, model_label=model_label, object_id=str(track.pk), action=action)
        model.objects.filter(pk=row.pk).update(created_at=when)

    def migrate_forward(self):
        executor = MigrationExecutor(connection)
        executor.migrate([self.new_target])
        return executor.loader.project_state([self.new_target]).apps.get_model('catalog', 'ReleaseTrack')

    def test_backfill_uses_latest_delete_restore_and_preserves_track_positions(self):
        deleted, restored, untouched = [self.track(number) for number in (1, 2, 3)]
        self.change(deleted, 'delete', self.deleted_at)
        self.change(deleted, 'update', self.restored_at)
        self.change(restored, 'delete', self.deleted_at)
        self.change(restored, 'restore', self.restored_at)
        self.change(untouched, 'delete', self.deleted_at, model_label='catalog.recordingidentifier')
        model = self.migrate_forward()
        self.assertEqual(model.objects.get(pk=deleted.pk).archived_at, self.deleted_at)
        self.assertIsNone(model.objects.get(pk=restored.pk).archived_at)
        self.assertIsNone(model.objects.get(pk=untouched.pk).archived_at)
        self.assertEqual(list(model.objects.order_by('track_number').values_list('id', 'disc_number', 'track_number', 'display_title')), [
            (deleted.pk, 1, 1, 'Original 1'), (restored.pk, 1, 2, 'Original 2'), (untouched.pk, 1, 3, 'Original 3'),
        ])
        self.assertEqual(set(model.objects.values_list('revision', flat=True)), {1})
        # Before any slot reuse the downgrade and subsequent upgrade are safe.
        MigrationExecutor(connection).migrate([self.old_target])
        model = self.migrate_forward()
        self.assertEqual(model.objects.get(pk=deleted.pk).archived_at, self.deleted_at)
        self.assertEqual(model.objects.count(), 3)

    def test_reverse_guard_blocks_reused_positions_before_changing_schema_or_rows(self):
        deleted = self.track(1)
        self.change(deleted, 'delete', self.deleted_at)
        model = self.migrate_forward()
        replacement = model.objects.create(release_id=self.release.pk, recording_id=self.recording.pk, disc_number=1, track_number=1, display_title='New active track')
        with self.assertRaisesMessage(IrreversibleError, 'position has been reused'):
            MigrationExecutor(connection).migrate([self.old_target])
        self.assertEqual(model.objects.get(pk=deleted.pk).archived_at, self.deleted_at)
        self.assertEqual(model.objects.get(pk=replacement.pk).track_number, 1)
        self.assertEqual(model.objects.count(), 2)
        self.assertIn(self.new_target, MigrationExecutor(connection).loader.applied_migrations)
