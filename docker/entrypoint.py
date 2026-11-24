import os
from pathlib import Path
import secrets
import sys

sys.path.insert(0, '/app')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'epg.docker_settings')
data = Path('/data')
data.mkdir(exist_ok=True)
(data / 'sources').mkdir(exist_ok=True)
role = sys.argv[1] if len(sys.argv) > 1 else 'web'
restored=False
if role in ('web','all') and ((data/'restore-pending.json').exists() or (data/'restore-journal.json').exists()):
    from web.backups import apply_pending
    try:
        restored=apply_pending(data)
    except BlockingIOError:
        raise RuntimeError('恢复前必须先停止使用同一 data 的其他工作进程。')
    except Exception:
        if (data/'restore-journal.json').exists():raise
        if (data/'restore-pending.json').exists():os.replace(data/'restore-pending.json',data/'restore-failed.json')
        print('备份恢复未完成，已保留原数据；请检查维护中心并重新预览备份。',flush=True)

if role in ('all', 'web', 'scheduler', 'crawl') and os.getuid() == 0:
    from docker.permissions import prepare_data_permissions
    for directory in (data, data / 'sources', data / 'download'):
        directory.mkdir(exist_ok=True)
    prepare_data_permissions(data)
    os.setgroups([])
    os.setgid(10001)
    os.setuid(10001)

if role in ('all', 'web', 'scheduler', 'crawl'):
    from docker.permissions import check_data_access
    check_data_access(data)

if role in ('web', 'all'):
    (data / 'download').mkdir(exist_ok=True)
    key = data / 'secret-key'
    if not key.exists():
        with open(key, 'x', opener=lambda p, f: os.open(p, f, 0o600)) as f:
            f.write(secrets.token_urlsafe(64))

import django
django.setup()

if role in ('web', 'all'):
    from django.core.management import call_command
    from django.db import connection, transaction
    from django.contrib.auth import get_user_model
    from django.db.migrations.executor import MigrationExecutor
    database = data / 'epg.sqlite3'
    executor = MigrationExecutor(connection)
    upgrading = bool(executor.migration_plan(executor.loader.graph.leaf_nodes()))
    if database.exists() and database.stat().st_size and upgrading:
        import sqlite3
        import datetime
        backups = data / 'backups'
        backups.mkdir(exist_ok=True)
        backup_file = backups / ('epg-before-upgrade-' + datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f') + '.sqlite3')
        with sqlite3.connect(database) as original, sqlite3.connect(backup_file) as backup:
            original.backup(backup)
        backup_file.chmod(0o600)
        print(f'Database upgrade backup: {backup_file}', flush=True)
    call_command('migrate', interactive=False)
    with connection.cursor() as cursor:
        cursor.execute('PRAGMA journal_mode=WAL')
    marker = data / 'initialized-v1'
    if not marker.exists():
        from web.models import Channel
        with transaction.atomic():
            if not Channel.objects.exists():
                call_command('loaddata', '/app/docker/channels.json')
            User = get_user_model()
            if not User.objects.filter(is_superuser=True).exists():
                username = os.getenv('DJANGO_SUPERUSER_USERNAME', 'admin')
                password = os.getenv('DJANGO_SUPERUSER_PASSWORD') or 'admin'
                # Write before commit so a failed disk write cannot leave an inaccessible account.
                password_file = data / 'admin-password.txt'
                password_file.write_text(password, encoding='utf-8')
                password_file.chmod(0o600)
                User.objects.create_superuser(username=username, password=password)
                print(f'Administrator: {username}; password saved to /data/admin-password.txt', flush=True)
        marker.touch()
    from crawl.crawl import gen_xml
    from utils.general import xmlinfo
    from web.options import apply_legacy
    apply_legacy()
    for config in xmlinfo:
        if restored or upgrading or not (data / 'download' / xmlinfo[config]['basename']).exists():
            _,filename=gen_xml(config)
            import gzip,shutil
            with open(filename,'rb') as src,gzip.open(filename+'.gz.tmp','wb') as dst:
                shutil.copyfileobj(src,dst)
            os.replace(filename+'.gz.tmp',filename+'.gz')
    if restored or upgrading:
        from crawl.crawl import gen_test_m3u
        from utils.general import dirs
        from web.models import Channel
        gen_test_m3u(Channel.get_need_channels(Channel, 'all')[0], dirs['testm3u_dir'])
    web_command = ['gunicorn', 'epg.wsgi:application', '--bind', '0.0.0.0:8000',
                         '--workers', '1', '--threads', '4', '--timeout', '120',
                         '--access-logfile', '-', '--error-logfile', '-']
    if role == 'all':
        from docker.supervisor import main
        sys.exit(main(web_command))
    os.execvp('gunicorn', web_command)
elif role == 'scheduler':
    from web.tasks import worker
    worker()
elif role == 'crawl':
    from docker.run_crawl import main
    sys.exit(main(sys.argv[2:]))
else:
    os.execvp(sys.argv[1], sys.argv[1:])
