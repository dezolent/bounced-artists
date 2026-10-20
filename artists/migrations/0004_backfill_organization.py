from django.db import migrations


def backfill(apps, schema_editor):
    Organization = apps.get_model("artists", "Organization")
    Artist = apps.get_model("artists", "Artist")
    organization, _ = Organization.objects.using(schema_editor.connection.alias).get_or_create(slug="bounced-studio", defaults={"name": "Bounced Studio"})
    Artist.objects.using(schema_editor.connection.alias).filter(organization__isnull=True).update(organization_id=organization.pk)


class Migration(migrations.Migration):
    dependencies = [("artists", "0003_organization_artist_public_metadata_and_more")]
    operations = [migrations.RunPython(backfill, migrations.RunPython.noop)]
