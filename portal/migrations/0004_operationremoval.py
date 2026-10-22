import uuid
import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations,models


class Migration(migrations.Migration):
    dependencies=[('portal','0003_review_request_returned'),('artists','0008_artist_requires_staff_review'),migrations.swappable_dependency(settings.AUTH_USER_MODEL)]
    operations=[migrations.CreateModel(name='OperationRemoval',fields=[
        ('id',models.UUIDField(default=uuid.uuid4,editable=False,primary_key=True,serialize=False)),
        ('created_at',models.DateTimeField(auto_now_add=True)),('updated_at',models.DateTimeField(auto_now=True)),
        ('model_label',models.CharField(max_length=100)),('object_id',models.CharField(max_length=40)),
        ('removed_at',models.DateTimeField(default=django.utils.timezone.now)),('restored_at',models.DateTimeField(null=True,blank=True)),
        ('organization',models.ForeignKey(to='artists.organization',null=True,blank=True,on_delete=django.db.models.deletion.PROTECT)),
        ('removed_by',models.ForeignKey(to=settings.AUTH_USER_MODEL,null=True,blank=True,on_delete=django.db.models.deletion.SET_NULL,related_name='operation_removals')),
        ('restored_by',models.ForeignKey(to=settings.AUTH_USER_MODEL,null=True,blank=True,on_delete=django.db.models.deletion.SET_NULL,related_name='operation_restorations')),
    ],options={'constraints':[models.UniqueConstraint(fields=('model_label','object_id'),name='operation_removal_unique')]})]
