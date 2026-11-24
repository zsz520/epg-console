"""Versioned, bounded backup archives. Database restore runs only at cold boot."""
import contextlib
import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path,PurePosixPath
import re
import shutil
import sqlite3
import stat
import tempfile
import uuid
import zipfile

MAX_BYTES=2*1024**3
MAX_FILES=10000
DIRECTORIES=('sources','xmltv','download')
FILES=('epg.sqlite3','secret-key','admin-password.txt')


def write_json(path,value):
    temp=path.with_name(path.name+'.tmp')
    with temp.open('w') as f:
        json.dump(value,f);f.flush();os.fsync(f.fileno())
    temp.chmod(0o600);os.replace(temp,path)


def finish_restore(root,nonce):
    if not re.fullmatch('[a-f0-9]{32}',nonce):raise ValueError('恢复事务路径无效。')
    (root/'restore-pending.json').unlink(missing_ok=True)
    (root/'restore-journal.json').unlink(missing_ok=True)
    shutil.rmtree(root/('restore-old-'+nonce),ignore_errors=True)
    shutil.rmtree(root/'restore-staging'/nonce,ignore_errors=True)
    (root/'restore-failed.json').unlink(missing_ok=True)


def digest(path):
    h=hashlib.sha256()
    with open(path,'rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()


def check_db(path,current=None,current_schema=None):
    with contextlib.closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro&immutable=1',uri=True)) as db:
        db.execute('PRAGMA trusted_schema=OFF')
        if db.execute("SELECT count(*) FROM sqlite_master WHERE type IN ('view','trigger')").fetchone()[0]:raise ValueError('备份数据库包含不支持的视图或触发器。')
        if db.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise ValueError('备份数据库完整性检查失败。')
        migrations=set(db.execute('SELECT app,name FROM django_migrations'))
        if current and not migrations.issubset(current):raise ValueError('备份版本比当前程序更新，不能恢复。')
        required={'web_channel':{'id','name','tvg_name','tvg_id','channel_id','source','ineed','has_epg','recrawl'},
                  'web_epg':{'channel_id','program_date','starttime','endtime','title'},
                  'web_runtimeconfig':{'id','options','auto_crawl'},'web_task':{'id','kind','status','params','summary','finished_at'},
                  'web_xmltvfeed':{'id','url','snapshot'},'auth_user':{'id','username','password','is_active','is_superuser'}}
        for table,columns in required.items():
            actual={r[1] for r in db.execute('PRAGMA table_info("'+table+'")')}
            if not columns.issubset(actual):raise ValueError('备份缺少关键数据表或字段。')
        if current_schema and migrations==current:
            for table,columns in current_schema.items():
                actual={r[1] for r in db.execute('PRAGMA table_info("'+table.replace('"','""')+'")')}
                if not columns.issubset(actual):raise ValueError('备份数据库结构不完整。')
        if db.execute('PRAGMA foreign_key_check').fetchone():raise ValueError('备份数据库存在关联错误。')
        if not db.execute('SELECT count(*) FROM auth_user WHERE is_superuser=1 AND is_active=1').fetchone()[0]:raise ValueError('备份没有可用管理员。')
        return {'channels':db.execute('SELECT count(*) FROM web_channel').fetchone()[0],
                'programmes':db.execute('SELECT count(*) FROM web_epg').fetchone()[0]}


def create(root=None,db_path=None):
    if root is None:
        from django.conf import settings
        root=Path(settings.DATA_DIR)
        db_path=settings.DATABASES['default']['NAME']
    root=Path(root);db_path=Path(db_path or root/'epg.sqlite3')
    directory=root/'backups';directory.mkdir(exist_ok=True)
    name='epg-full-'+datetime.datetime.now().strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:8]+'.zip'
    target=directory/name;temp=directory/(name+'.tmp');manifest={'format':1,'files':{}};total=0
    try:
        with tempfile.TemporaryDirectory(dir=root) as stage:
            snapshot=Path(stage)/'epg.sqlite3'
            with contextlib.closing(sqlite3.connect(db_path.resolve().as_uri()+'?mode=ro',uri=True)) as db,contextlib.closing(sqlite3.connect(snapshot)) as dest:
                db.backup(dest);dest.execute('PRAGMA journal_mode=DELETE');dest.commit()
            items=[('epg.sqlite3',snapshot)]
            for filename in FILES[1:]:
                p=root/filename
                if p.is_file() and not p.is_symlink():items.append((filename,p))
            for directory_name in DIRECTORIES:
                for p in (root/directory_name).rglob('*'):
                    if p.is_file() and not p.is_symlink() and not any(part.startswith('.') for part in p.relative_to(root).parts) and not p.name.endswith(('.tmp','-wal','-shm')):
                        p.resolve().relative_to(root.resolve());items.append((p.relative_to(root).as_posix(),p))
            if len(items)>MAX_FILES:raise ValueError('备份文件数量超过上限。')
            with zipfile.ZipFile(temp,'w',compression=zipfile.ZIP_DEFLATED) as z:
                for relative,p in items:
                    h=hashlib.sha256()
                    with p.open('rb') as f,z.open(relative,'w') as dest:
                        for chunk in iter(lambda:f.read(1024*1024),b''):
                            total+=len(chunk)
                            if total>MAX_BYTES:raise ValueError('完整备份超过 2 GiB 上限。')
                            h.update(chunk);dest.write(chunk)
                    manifest['files'][relative]=h.hexdigest()
                z.writestr('manifest.json',json.dumps(manifest))
        temp.chmod(0o600);os.replace(temp,target)
        return name
    finally:temp.unlink(missing_ok=True)


def stage_restore(upload,user):
    from django.conf import settings
    from django.core import signing
    from django.db import connection
    root=Path(settings.DATA_DIR);base=root/'restore-staging';base.mkdir(exist_ok=True,mode=0o700)
    nonce=uuid.uuid4().hex;directory=base/nonce;directory.mkdir(mode=0o700)
    try:
        source=directory/'upload.zip';size=0
        with source.open('wb') as dest:
            for chunk in upload.chunks():
                size+=len(chunk)
                if size>MAX_BYTES:raise ValueError('备份文件超过 2 GiB。')
                dest.write(chunk)
        with zipfile.ZipFile(source) as z:
            infos=z.infolist();names=[i.filename for i in infos]
            if len(names)!=len(set(names)) or len(names)>MAX_FILES+1 or sum(i.file_size for i in infos)>MAX_BYTES:raise ValueError('备份重复文件、数量或展开大小无效。')
            if 'manifest.json' not in names or z.getinfo('manifest.json').file_size>2*1024*1024:raise ValueError('缺少有效备份清单。')
            manifest=json.loads(z.read('manifest.json'))
            if not isinstance(manifest,dict) or manifest.get('format')!=1 or not isinstance(manifest.get('files'),dict) or set(names)!=set(manifest['files'])|{'manifest.json'}:raise ValueError('备份清单不匹配。')
            for relative,expected in manifest['files'].items():
                path=PurePosixPath(relative);parts=path.parts
                if not parts or path.is_absolute() or '..' in parts or '\\' in relative or str(path)!=relative or (relative not in FILES and (parts[0] not in DIRECTORIES or len(parts)<2)):
                    raise ValueError('备份含非法文件路径。')
                info=z.getinfo(relative)
                if stat.S_ISLNK(info.external_attr>>16) or info.is_dir():raise ValueError('备份不能包含符号链接或目录条目。')
                dest=directory/'files'/relative;dest.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
                with z.open(info) as inp,dest.open('wb') as output:shutil.copyfileobj(inp,output,1024*1024)
                dest.chmod(0o600)
                if digest(dest)!=expected:raise ValueError('备份校验和错误。')
        database=directory/'files/epg.sqlite3'
        with connection.cursor() as cur:cur.execute('SELECT app,name FROM django_migrations');current=set(cur.fetchall())
        schema={}
        with connection.cursor() as cur:
            for table in connection.introspection.table_names():
                cur.execute('PRAGMA table_info('+connection.ops.quote_name(table)+')')
                schema[table]={r[1] for r in cur.fetchall()}
        summary=check_db(database,current,schema)
        # Never replay queued/running jobs from the restored snapshot.
        with contextlib.closing(sqlite3.connect(database)) as db:
            db.execute("UPDATE web_task SET status='cancelled',summary='备份恢复时取消旧排队任务',finished_at=? WHERE status IN ('queued','running')",(datetime.datetime.now(datetime.timezone.utc).isoformat(),))
            db.commit();db.execute('PRAGMA journal_mode=DELETE')
        manifest['files']['epg.sqlite3']=digest(database)
        state=directory/'state.json';state.write_text(json.dumps(manifest));state.chmod(0o600)
        seal=digest(state)
        source.unlink()
        token=signing.dumps({'nonce':nonce,'user':user.pk,'seal':seal},salt='epg-restore')
        return summary,token
    except Exception:
        shutil.rmtree(directory);raise


def confirm_restore(token,user):
    from django.conf import settings
    from django.core import signing
    data=signing.loads(token,salt='epg-restore',max_age=900)
    if data.get('user')!=user.pk or not re.fullmatch('[a-f0-9]{32}',data.get('nonce','')):raise ValueError('恢复凭证无效。')
    state=Path(settings.DATA_DIR)/'restore-staging'/data['nonce']/'state.json'
    if not state.exists() or digest(state)!=data['seal']:raise ValueError('恢复预览已失效。')
    return {'nonce':data['nonce'],'seal':data['seal']}


def request_restore(params):
    from django.conf import settings
    root=Path(settings.DATA_DIR);pending=root/'restore-pending.json'
    if pending.exists():raise ValueError('已有恢复任务等待重启。')
    write_json(pending,params)


def apply_pending(root):
    root=Path(root)
    if not (root/'restore-pending.json').exists() and not (root/'restore-journal.json').exists():return False
    with contextlib.ExitStack() as stack:
        for name in ('worker.lock','crawl.lock'):
            f=stack.enter_context((root/name).open('a'))
            fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
        return _apply_pending(root)


def _apply_pending(root):
    root=Path(root);pending=root/'restore-pending.json'
    journal=root/'restore-journal.json'
    if journal.exists():
        record=json.loads(journal.read_text())
        if record.get('committed'):
            finish_restore(root,record['nonce']);return True
    if not pending.exists():raise ValueError('缺少恢复事务凭证，请保留数据检查。')
    params=json.loads(pending.read_text());nonce=params.get('nonce','')
    if not re.fullmatch('[a-f0-9]{32}',nonce):raise ValueError('恢复路径无效。')
    directory=root/'restore-staging'/nonce;state=directory/'state.json'
    if directory.is_symlink() or digest(state)!=params['seal']:raise ValueError('恢复清单发生变化。')
    # Recover interrupted directory swaps before validating the staged files.
    journal=root/'restore-journal.json'
    if journal.exists():
        record=json.loads(journal.read_text())
        if record.get('nonce')!=nonce or any(name not in FILES+DIRECTORIES for name in record['destinations']):raise ValueError('恢复事务记录无效。')
        old=root/('restore-old-'+nonce)
        for name in record['destinations']:
            target=root/name;original=old/name;new=directory/'files'/name
            if original.exists():
                if target.exists():
                    if new.exists():raise ValueError('恢复目录状态冲突。')
                    new.parent.mkdir(parents=True,exist_ok=True);os.replace(target,new)
                os.replace(original,target)
            elif name not in record['originals'] and target.exists() and not new.exists():
                new.parent.mkdir(parents=True,exist_ok=True);os.replace(target,new)
        shutil.rmtree(old,ignore_errors=True);journal.unlink()
    files=json.loads(state.read_text())['files']
    for relative,expected in files.items():
        p=directory/'files'/relative
        if p.is_symlink() or digest(p)!=expected:raise ValueError('恢复文件发生变化。')
    # Full rollback archive is created only after all old services stopped.
    rollback=create(root)
    if os.getuid()==0:os.chown(root/'backups'/rollback,10001,10001)
    destinations=[name for name in FILES if name in files]+list(DIRECTORIES)
    if journal.exists():raise ValueError('存在未完成的恢复事务，请保留数据并检查恢复日志。')
    old=root/('restore-old-'+nonce);old.mkdir(exist_ok=False)
    record={'nonce':nonce,'backup':rollback,'destinations':destinations,'originals':[name for name in destinations if (root/name).exists()]}
    write_json(journal,record)
    moved=[];installed=[]
    try:
        for name in destinations:
            target=root/name;new=directory/'files'/name
            if target.is_symlink():raise ValueError('目标目录不能为符号链接。')
            if target.exists():os.replace(target,old/name);moved.append(name)
            if not new.exists():new.mkdir(parents=True)
            os.replace(new,target);installed.append(name)
            if os.getuid()==0:
                os.chown(target,10001,10001)
                if target.is_dir():
                    for p in target.rglob('*'):os.chown(p,10001,10001)
        for suffix in ('-wal','-shm'):(root/('epg.sqlite3'+suffix)).unlink(missing_ok=True)
        (root/'initialized-v1').touch()
        record['committed']=True;write_json(journal,record)
        finish_restore(root,nonce)
        backup=root/'backups'/rollback
        if os.getuid()==0:os.chown(backup,10001,10001)
        return True
    except Exception:
        if record.get('committed'):raise
        for name in reversed(installed):
            p=root/name
            staged=directory/'files'/name
            staged.parent.mkdir(parents=True,exist_ok=True)
            os.replace(p,staged)
        for name in reversed(moved):os.replace(old/name,root/name)
        journal.unlink(missing_ok=True)
        os.replace(pending,root/'restore-failed.json')
        with contextlib.suppress(OSError):old.rmdir()
        raise

