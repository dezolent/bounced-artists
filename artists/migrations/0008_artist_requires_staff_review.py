from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('artists', '0007_catalog_mvp')]
    operations = [migrations.AddField(model_name='artist', name='requires_staff_review', field=models.BooleanField(default=False, db_default=False, help_text='Require staff to publish this artist’s website updates.'))]
