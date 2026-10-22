"""Preserve removed track positions while freeing their active tracklist slots.

Rollouts proceed forward. A downgrade is safe only before physical positions are
reused; the reverse guard explains this before changing any schema or data.
"""
from django.db import migrations, models
from django.db.models.functions import Cast
from django.db.migrations.exceptions import IrreversibleError


def backfill_track_archives(apps, schema_editor):
    track_model = apps.get_model('catalog', 'ReleaseTrack')
    change_model = apps.get_model('portal', 'EditorialChange')
    database = schema_editor.connection.alias
    latest = change_model.objects.using(database).filter(
        model_label='catalog.releasetrack',
        object_id=Cast(models.OuterRef('pk'), models.CharField()),
        action__in=['delete', 'restore'],
    ).order_by('-created_at', '-pk')
    track_model.objects.using(database).filter(archived_at__isnull=True).annotate(
        latest_track_action=models.Subquery(latest.values('action')[:1]),
    ).filter(latest_track_action='delete').update(
        archived_at=models.Subquery(latest.values('created_at')[:1]),
    )


def guard_reverse_slot_reuse(apps, schema_editor):
    track_model = apps.get_model('catalog', 'ReleaseTrack')
    duplicates = track_model.objects.using(schema_editor.connection.alias).values(
        'release_id', 'disc_number', 'track_number',
    ).annotate(row_count=models.Count('pk')).filter(row_count__gt=1)
    if duplicates.exists():
        raise IrreversibleError(
            'Cannot reverse track archival after a release track position has '
            'been reused. Archived tracks retain their original positions and '
            'UUIDs. Keep this forward migration; reversing requires an explicit '
            'operator-reviewed data preservation plan.'
        )


class Migration(migrations.Migration):
    dependencies = [
        ('catalog', '0009_catalog_mvp'),
        ('portal', '0002_catalogreviewrequest'),
    ]

    operations = [
        migrations.AddField(
            model_name='releasetrack', name='archived_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='releasetrack', name='revision',
            field=models.PositiveBigIntegerField(default=1),
        ),
        migrations.RunPython(backfill_track_archives, migrations.RunPython.noop),
        migrations.RemoveConstraint(
            model_name='releasetrack', name='release_track_position_unique',
        ),
        migrations.AddConstraint(
            model_name='releasetrack',
            constraint=models.UniqueConstraint(
                fields=('release', 'disc_number', 'track_number'),
                condition=models.Q(archived_at__isnull=True),
                name='release_track_position_unique',
            ),
        ),
        # Reversal visits this guard before touching either constraint/column.
        migrations.RunPython(migrations.RunPython.noop, guard_reverse_slot_reuse),
    ]
