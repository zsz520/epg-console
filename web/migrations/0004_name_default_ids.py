import re
from django.db import migrations, models


def use_name_for_default_ids(apps, schema_editor):
    Channel = apps.get_model('web', 'Channel')
    for channel in Channel.objects.all().iterator():
        old_default = str(channel.pk) if channel.pk != 9999 else 'ch-9999'
        if channel.tvg_id not in (None, '', old_default):
            continue
        candidate = (channel.tvg_name or '').strip()
        if not candidate or candidate == '9999' or re.search(r'''[\s<>&"']''', candidate):
            continue
        if not Channel.objects.exclude(pk=channel.pk).filter(tvg_id=candidate).exists():
            Channel.objects.filter(pk=channel.pk).update(tvg_id=candidate)


class Migration(migrations.Migration):
    dependencies = [('web', '0003_channel_tvg_id')]
    operations = [
        migrations.AlterField(
            model_name='channel', name='tvg_id',
            field=models.CharField(blank=True, help_text='XMLTV 对外频道 ID，可修改；留空时默认使用 tvg-name。同名频道请指定不同的 tvg-id。',
                                   max_length=100, null=True, unique=True, verbose_name='tvg-id')),
        migrations.RunPython(use_name_for_default_ids, migrations.RunPython.noop),
    ]
