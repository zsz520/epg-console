import django.db.models.deletion
from django.conf import settings
from django.db import migrations,models


class Migration(migrations.Migration):
    dependencies=[
        ('web','0022_channel_list_search_identifier'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
    operations=[
        migrations.CreateModel(
            name='SourceEnvironment',
            fields=[
                ('id',models.BigAutoField(auto_created=True,primary_key=True,serialize=False,verbose_name='ID')),
                ('source_id',models.CharField(max_length=20,unique=True,verbose_name='来源 ID')),
                ('proxies',models.TextField(blank=True,default='',verbose_name='代理列表')),
                ('extra_env',models.JSONField(blank=True,default=dict,verbose_name='其他环境变量')),
                ('updated_at',models.DateTimeField(auto_now=True,verbose_name='更新时间')),
                ('updated_by',models.ForeignKey(blank=True,null=True,on_delete=django.db.models.deletion.SET_NULL,related_name='+',to=settings.AUTH_USER_MODEL)),
            ],
            options={'verbose_name':'来源运行环境','verbose_name_plural':'来源运行环境'},
        ),
    ]
