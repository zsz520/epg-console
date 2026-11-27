import base64
import binascii
from django.db import migrations,models


def backfill(apps,schema_editor):
    Catalog=apps.get_model('web','Channel_list')
    manager=Catalog.objects.using(schema_editor.connection.alias)
    batch=[]
    for row in manager.only('pk','source','out_channel_id').iterator(chunk_size=500):
        value=row.out_channel_id
        if row.source=='xmltv':
            try:
                _,encoded=value.split(':',1)
                value=base64.b64decode(encoded+'='*(-len(encoded)%4),altchars=b'-_',validate=True).decode('utf-8')
            except (ValueError,UnicodeError,binascii.Error):pass
        row.search_identifier=value
        batch.append(row)
        if len(batch)==500:
            manager.bulk_update(batch,['search_identifier'],batch_size=500);batch=[]
    if batch:manager.bulk_update(batch,['search_identifier'],batch_size=500)


class Migration(migrations.Migration):
    dependencies=[('web','0021_runtimeconfig_auto_xmltv_and_more')]
    operations=[
        migrations.AddField(model_name='channel_list',name='search_identifier',field=models.CharField(blank=True,default='',editable=False,max_length=300,verbose_name='来源原始频道 ID')),
        migrations.RunPython(backfill,migrations.RunPython.noop),
    ]
