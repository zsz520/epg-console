"""Validation and private configuration helpers for source workers."""
import ast
import ipaddress
import re
from urllib.parse import urlsplit, urlunsplit


class LineError(ValueError):
    def __init__(self, line, column, message):
        super().__init__(f'第 {line} 行，第 {column} 个字符：{message}')


def parse_proxies(text):
    result=[]
    if len(text)>16000:
        raise LineError(1,1,'代理列表过长（最多 16000 字符）。')
    for number,raw in enumerate(text.splitlines(),1):
        value=raw.strip()
        if not value:continue
        offset=len(raw)-len(raw.lstrip())
        port_at=raw.rfind(':')+2
        control=next((i for i,c in enumerate(raw) if ord(c)<32 or ord(c)==127),None)
        if control is not None:raise LineError(number,control+1,'代理地址不能包含控制字符。')
        for index,char in enumerate(raw):
            if char.isspace() and raw[:index].strip() and raw[index:].strip():
                raise LineError(number,index+1,'代理地址不能包含空白；账号密码中的特殊字符请用百分号编码。')
        added_scheme='://' not in value
        if added_scheme:value='socks5h://'+value
        try:
            parts=urlsplit(value)
            if parts.scheme not in ('http','https','socks5','socks5h'):
                raise LineError(number,offset+1,'仅支持 http、https、socks5、socks5h。')
            if not parts.hostname or not parts.port or not 1<=parts.port<=65535:
                raise LineError(number,max(offset+1,port_at),'必须提供有效主机和 1–65535 端口。')
            if parts.path not in ('','/') or parts.query or parts.fragment:
                start=0 if added_scheme else raw.find('://')+3
                match=re.search(r'[/\?#]',raw[start:])
                raise LineError(number,start+match.start()+1 if match else offset+1,'代理地址不能带路径、查询或片段。')
            if parts.username is None and '@' in parts.netloc:
                raise ValueError('代理账号格式无效。')
            for match in re.finditer('%',value):
                if not re.match('[0-9a-fA-F]{2}',value[match.start()+1:match.start()+3]):
                    raise LineError(number,offset+match.start()+1-(10 if added_scheme else 0),'百分号后需要两位十六进制字符。')
            host=parts.hostname
            if ':' not in host and not re.fullmatch(r'[A-Za-z0-9_.-]+',host):
                raise ValueError('主机名格式无效。')
            if ':' in host:ipaddress.IPv6Address(host)
            hostname='['+str(ipaddress.IPv6Address(host))+']' if ':' in host else host.lower()
            credentials=parts.netloc.rsplit('@',1)[0]+'@' if '@' in parts.netloc else ''
            value=urlunsplit((parts.scheme,credentials+hostname+':'+str(parts.port),'','',''))
        except LineError:raise
        except ValueError as exc:
            # URL parser errors may include credentials, so only use our static messages.
            msg=str(exc) if str(exc).startswith(('仅支持','必须','代理','主机')) else '主机、端口或 IPv6 地址格式无效。'
            column=port_at if 'port' in str(exc).lower() else offset+1
            raise LineError(number,max(1,column),msg) from None
        if value not in result:
            if len(result)>=20:raise LineError(number,offset+1,'最多配置 20 个代理。')
            result.append(value)
    return result


RESERVED={'PATH','HOME','TMPDIR','LD_PRELOAD','LD_LIBRARY_PATH','PYTHONPATH','PYTHONHOME',
          'DJANGO_SETTINGS_MODULE','DJANGO_SECRET_KEY','DATABASE_URL','HTTP_PROXY','HTTPS_PROXY',
          'ALL_PROXY','NO_PROXY','REQUESTS_CA_BUNDLE','CURL_CA_BUNDLE','SSL_CERT_FILE'}


def parse_extra(text):
    values={}
    if len(text)>16000:raise LineError(1,1,'环境变量过长（最多 16000 字符）。')
    for number,line in enumerate(text.splitlines(),1):
        if not line.strip():continue
        key,separator,value=line.partition('=')
        if not separator:raise LineError(number,len(line)+1,'缺少等号，使用 KEY=value。')
        invalid=next((i for i,c in enumerate(key) if c not in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_'),None)
        if invalid is not None or not key:
            raise LineError(number,(invalid or 0)+1,'变量名只允许大写字母、数字、下划线。')
        if key in RESERVED or key.startswith(('EPG_','DJANGO_','PYTHON','LD_')):
            raise LineError(number,1,'不能修改应用、数据库或进程安全设置。')
        if key in values:raise LineError(number,1,'变量名重复。')
        if '\x00' in value:raise LineError(number,len(key)+2+value.index('\x00'),'不能包含空字符。')
        values[key]=value
    return values


def mask_proxy(value):
    try:
        p=urlsplit(value)
        host=p.hostname or ''
        if ':' in host:host='['+host+']'
        auth=((p.username or 'user')+':***@') if p.username is not None else ''
        return f'{p.scheme}://{auth}{host}:{p.port}'
    except ValueError:return '[代理地址已隐藏]'


def source_targets(source):
    """Only literal URLs belonging to trusted source code, never URLs from POST/env."""
    tree=ast.parse(source.path.read_text(encoding='utf-8-sig'))
    urls=[]
    for node in ast.walk(tree):
        if isinstance(node,ast.Constant) and isinstance(node.value,str):
            value=node.value.strip()
            if re.fullmatch(r'https?://[^\s<>{}]+',value):
                try:
                    p=urlsplit(value)
                    if not p.hostname or p.username or p.password:continue
                    host=p.hostname.lower()
                    if host=='localhost' or host.endswith(('.localhost','.local','.invalid','.test')):continue
                    try:
                        if not ipaddress.ip_address(host).is_global:continue
                    except ValueError:pass
                    if value not in urls:urls.append(value)
                except ValueError:continue
    # Explicit, static health endpoint may select among the source's literal domains.
    explicit=None
    for node in tree.body:
        if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='SOURCE_HEALTH_URL' for t in node.targets):
            try:explicit=ast.literal_eval(node.value)
            except (ValueError,TypeError):pass
    if explicit in urls:return [explicit]
    if source.id=='hnyst':
        base=next((u for u in urls if 'hntvepg.taipan.jda.bcs.ottcn.com' in u),None)
        return [base.rstrip('/')+'/ysten-lvoms-epg/epg/getChannels.shtml?templateId=0731&districtCode=430000'] if base else []
    # A fixed URL from the script, preferably an actual API rather than an image.
    urls.sort(key=lambda u:(bool(re.search(r'\.(png|jpg|gif|svg)(\?|$)',u)), 'api' not in u and 'epg' not in u))
    return urls[:1]
