"""Scoped DB overrides; serialize environment-sensitive source calls per process."""
from contextlib import contextmanager, redirect_stdout, redirect_stderr
import hashlib
import json
import logging
import os
import re
import sys
import threading
from urllib.parse import unquote,urlsplit

LOCK=threading.RLock()
_active=None
_last_keys=set()
_baseline={}
_record_factory=logging.getLogRecordFactory()


def _safe_record(*args,**kwargs):
    record=_record_factory(*args,**kwargs)
    if _active:
        record.msg=redact(record.getMessage())
        record.args=()
        if record.exc_info:
            import traceback
            record.exc_text=redact(''.join(traceback.format_exception(*record.exc_info)))
            record.exc_info=None
    return record


logging.setLogRecordFactory(_safe_record)


def read(source_id):
    from django.apps import apps
    from django.db import connection
    if not apps.ready:return None
    # Pre-migration startup must remain usable, but never swallow unrelated DB failures.
    if 'web_sourceenvironment' not in connection.introspection.table_names():return None
    from web.models import SourceEnvironment
    return SourceEnvironment.objects.filter(source_id=source_id).first()


def apply(source_id):
    """Apply under LOCK/scope; return the exact configured environment snapshot."""
    with LOCK:
        return _apply(source_id)


def _apply(source_id):
    global _active,_last_keys
    for key in _last_keys:
        if _baseline.get(key) is None:os.environ.pop(key,None)
        else:os.environ[key]=_baseline[key]
    row=read(source_id)
    values=dict(row.extra_env) if row else {}
    proxies=row.proxies.splitlines() if row and row.proxies else []
    if proxies:
        joined=','.join(proxies)
        prefix=source_id.upper()
        values.update({prefix+'_PROXIES':joined,prefix+'_PROXY':proxies[0],
                       'HTTP_PROXY':proxies[0],'HTTPS_PROXY':proxies[0],'ALL_PROXY':proxies[0]})
    for key,value in values.items():
        if key not in _baseline:_baseline[key]=os.environ.get(key)
        os.environ[key]=str(value)
    _last_keys=set(values)
    version=hashlib.sha256(json.dumps([values,proxies],sort_keys=True).encode()).hexdigest()
    _active={'source_id':source_id,'env':values,'proxies':proxies,'version':version}
    from .proxy_transport import install
    install()
    return _active


def active_snapshot():
    return _active


def redact(value):
    text=str(value)
    snapshot=_active or {}
    proxies=list(snapshot.get('proxies',[]))
    if snapshot:
        for key,value in os.environ.items():
            if 'PROXY' in key.upper():
                proxies.extend(re.split(r'[,;\s]+',value))
    for proxy in proxies:
        from web.source_environment import mask_proxy
        if not proxy or '://' not in proxy:continue
        text=text.replace(proxy,mask_proxy(proxy))
        try:p=urlsplit(proxy)
        except ValueError:continue
        if p.password:
            text=text.replace(p.password,'***').replace(unquote(p.password),'***')
    for key,value in snapshot.get('env',{}).items():
        if value and any(word in key for word in ('TOKEN','PASSWORD','SECRET','AUTHORIZATION','_KEY','COOKIE','CREDENTIAL')):
            text=text.replace(str(value),'***')
    return re.sub(r'((?:socks5h?|https?)://[^/\s:@]+:)[^@\s/]+@',r'\1***@',text)


class RedactedStream:
    def __init__(self,stream):self.stream=stream;self.pending=''
    def write(self,text):
        self.pending+=text
        while '\n' in self.pending:
            line,self.pending=self.pending.split('\n',1)
            self.stream.write(redact(line)+'\n')
        if len(self.pending)>65536:
            self.stream.write('[来源输出过长，已省略]\n');self.pending=''
        return len(text)
    def flush(self):
        if self.pending:self.stream.write(redact(self.pending));self.pending=''
        self.stream.flush()
    def __getattr__(self,key):return getattr(self.stream,key)


@contextmanager
def scope(source_id):
    global _active,_last_keys
    with LOCK:
        if _active and _active['source_id']==source_id:
            yield _active;return
        previous=_active
        previous_keys=set(_last_keys)
        before=dict(os.environ)
        out,err=RedactedStream(sys.stdout),RedactedStream(sys.stderr)
        try:
            snapshot=apply(source_id)
            with redirect_stdout(out),redirect_stderr(err):
                yield snapshot
        finally:
            out.flush();err.flush()
            for key in _last_keys|previous_keys:
                if key in before:os.environ[key]=before[key]
                else:os.environ.pop(key,None)
            _last_keys=previous_keys
            _active=previous
