"""Metadata-only discovery; source Python executes only when its worker calls it."""
import ast
from dataclasses import dataclass
from functools import lru_cache
import importlib
import importlib.util
import inspect
import os
from pathlib import Path
import re
import sys
from .legacy_sources import LEGACY

@dataclass(frozen=True)
class Source:
    id: str
    name: str
    path: Path
    module: str
    epg: str
    channels: str | None
    argument: object = 0
    external: bool = False


def _metadata(path):
    tree=ast.parse(path.read_text(encoding='utf-8-sig'),filename=path.name)
    values={}; functions=set()
    for node in tree.body:
        if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)): functions.add(node.name)
        if isinstance(node,ast.Assign):
            for target in node.targets:
                if isinstance(target,ast.Name) and target.id in ('SOURCE_ID','SOURCE_NAME','SOURCE_ENABLED','SOURCE_ARGUMENT'):
                    try: values[target.id]=ast.literal_eval(node.value)
                    except (ValueError,TypeError): raise ValueError(f'{target.id} 必须是常量') from None
    return values,functions


def discover(builtin=None,external=None):
    builtin=Path(builtin) if builtin is not None else Path(__file__).parent/'spiders'
    external=Path(external) if external is not None else Path(os.getenv('EPG_DATA_DIR','/data'))/'sources'
    sources={}; errors=[]
    # Legacy files remain supported without editing their parsing implementations.
    for key,entry in LEGACY.items():
        path=builtin/(entry['module'].split('.')[-1]+'.py')
        if path.is_file(): sources[key]=Source(key,entry['name'],path,entry['module'],entry['epg'],entry['channels'])
    for directory,is_external in ((builtin,False),(external,True)):
        candidates={}
        if not directory.is_dir(): continue
        for path in sorted(directory.glob('*.py')):
            if path.name.startswith('_'): continue
            try:
                if path.is_symlink(): raise ValueError('不加载符号链接来源文件')
                if path.stat().st_size>2*1024*1024: raise ValueError('来源文件超过 2 MiB')
                values,functions=_metadata(path)
                if not values: continue
                if 'SOURCE_ID' not in values and any(source.path==path for source in sources.values()): continue
                if values.get('SOURCE_ENABLED',True) is False: continue
                identifier=values.get('SOURCE_ID'); name=values.get('SOURCE_NAME')
                if not isinstance(identifier,str) or not re.fullmatch(r'[a-z0-9][a-z0-9_]{0,19}',identifier):
                    raise ValueError('SOURCE_ID 使用 1–20 位小写英文、数字、下划线，英文或数字开头')
                if not isinstance(name,str) or not name.strip() or len(name)>80: raise ValueError('SOURCE_NAME 必须为 1–80 字名称')
                epg=next((v for v in (f'get_epgs_{identifier}','get_epgs') if v in functions),None)
                channels=next((v for v in (f'get_channels_{identifier}','get_channels') if v in functions),None)
                # XMLTV remains a special built-in programme adapter without a remote catalogue function.
                if not epg or (not channels and identifier!='xmltv'): raise ValueError('缺少 get_channels_<id> 或 get_epgs_<id> 函数')
                if identifier in sources and not ((is_external and sources[identifier].path.name==path.name) or (not is_external and sources[identifier].path==path)):
                    raise ValueError(f'来源 ID {identifier} 与已注册来源重复，不覆盖原来源')
                module='' if is_external else 'crawl.spiders.'+path.stem
                candidates.setdefault(identifier,[]).append(Source(identifier,name.strip(),path,module,epg,channels,values.get('SOURCE_ARGUMENT',0),is_external))
            except SyntaxError as exc: errors.append(f'{path.name}：Python 语法错误，第 {exc.lineno} 行')
            except (OSError,ValueError,TypeError) as exc: errors.append(f'{path.name}：{exc}')
        for identifier,entries in candidates.items():
            if len(entries)>1:
                errors.append(f'{identifier}：多个来源文件使用相同 ID，均未加载（'+', '.join(e.path.name for e in entries)+'）')
            else: sources[identifier]=entries[0]
    return sources,errors


@lru_cache(maxsize=1)
def registry():
    return discover()


def get_source_names():
    return {key:source.name for key,source in registry()[0].items()}


def get_source_choices():
    return list(get_source_names().items())


_versions={}


def load(source):
    from .source_env import LOCK,scope,active_snapshot
    with LOCK:
        snapshot=active_snapshot()
        if not snapshot or snapshot['source_id']!=source.id:
            with scope(source.id):
                return load(source)
        name=source.module or '_epg_source_'+source.id
        version=(snapshot['version'],str(source.path),source.path.stat().st_mtime_ns)
        if _versions.get(name)!=version:
            sys.modules.pop(name,None)
        if name not in sys.modules:
            spec=importlib.util.spec_from_file_location(name,source.path)
            module=importlib.util.module_from_spec(spec)
            sys.modules[name]=module
            try:
                # Read current source rather than a same-second stale .pyc.
                exec(compile(source.path.read_bytes(),str(source.path),'exec'),module.__dict__)
                _versions[name]=version
            except BaseException:
                sys.modules.pop(name,None);raise
        return sys.modules[name]


def invoke(source,kind,*args):
    from .source_env import scope,redact
    with scope(source.id):
        try:
            function=getattr(load(source),source.channels if kind=='channels' else source.epg)
            if kind=='channels':
                result=function()
            else:
                signature=inspect.signature(function)
                try:signature.bind(*args,source.argument)
                except TypeError:
                    signature.bind(*args)
                    result=function(*args)
                else:result=function(*args,source.argument)
            if isinstance(result,dict) and result.get('msg'):result['msg']=redact(result['msg'])
            return result
        except Exception as exc:
            raise ValueError(redact(str(exc))) from None
