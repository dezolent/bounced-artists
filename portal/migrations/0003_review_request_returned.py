from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('portal', '0002_catalogreviewrequest'), ('publishing', '0001_initial')]
    operations = [
        migrations.AlterField(model_name='catalogreviewrequest',name='requested_by',field=models.ForeignKey(blank=True,null=True,on_delete=django.db.models.deletion.SET_NULL,to=settings.AUTH_USER_MODEL)),
        migrations.AlterField(model_name='catalogreviewrequest',name='status',field=models.CharField(choices=[('pending','Pending'),('resolved','Resolved'),('returned','Changes requested')],default='pending',max_length=10)),
        migrations.AddField(model_name='catalogreviewrequest',name='website',field=models.ForeignKey(blank=True,null=True,on_delete=django.db.models.deletion.PROTECT,to='publishing.website')),
        migrations.AddField(model_name='catalogreviewrequest',name='publication_selection',field=models.JSONField(blank=True,default=dict)),
    ]
