from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies=[('web','0018_alter_channel_recrawl')]
    operations=[migrations.AddField(model_name='runtimeconfig',name='last_daily_full_refresh',
                                  field=models.DateField(null=True,blank=True,editable=False))]
