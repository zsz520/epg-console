"""Requests proxy rotation with a small SOCKS5 connector (no extra dependency)."""
import contextvars
import ipaddress
import socket
import struct
from urllib.parse import urlsplit, unquote
import requests
from requests.adapters import HTTPAdapter
from urllib3.connection import HTTPConnection, HTTPSConnection
from urllib3.connectionpool import HTTPConnectionPool, HTTPSConnectionPool

_busy=contextvars.ContextVar('epg_proxy_transport_busy',default=False)
_original_send=requests.Session.send


def exact(sock,length):
    result=b''
    while len(result)<length:
        piece=sock.recv(length-len(result))
        if not piece:raise OSError('代理握手连接被关闭')
        result+=piece
    return result


def socks_connect(proxy,host,port,timeout):
    p=urlsplit(proxy)
    sock=socket.create_connection((p.hostname,p.port),timeout=timeout)
    try:
        sock.settimeout(timeout)
        authenticated=p.username is not None
        sock.sendall(b'\x05\x02\x00\x02' if authenticated else b'\x05\x01\x00')
        version,method=exact(sock,2)
        if version!=5 or method not in (0,2):raise OSError('SOCKS5 握手失败：代理拒绝认证方式')
        if method==2:
            if not authenticated:raise OSError('SOCKS5 需要账号密码')
            user=unquote(p.username).encode();password=unquote(p.password or '').encode()
            if len(user)>255 or len(password)>255:raise OSError('SOCKS5 账号密码过长')
            sock.sendall(b'\x01'+bytes([len(user)])+user+bytes([len(password)])+password)
            if exact(sock,2)!=b'\x01\x00':raise OSError('SOCKS5 账号密码认证失败')
        target=host
        if p.scheme=='socks5':
            target=socket.getaddrinfo(host,port,type=socket.SOCK_STREAM)[0][4][0]
        try:
            address=ipaddress.ip_address(target)
            payload=(b'\x01' if address.version==4 else b'\x04')+address.packed
        except ValueError:
            name=target.encode('idna')
            if len(name)>255:raise OSError('目标域名过长')
            payload=b'\x03'+bytes([len(name)])+name
        sock.sendall(b'\x05\x01\x00'+payload+struct.pack('!H',int(port)))
        reply=exact(sock,4)
        if reply[0]!=5 or reply[1]!=0:raise OSError(f'SOCKS5 握手失败（响应码 {reply[1]}）')
        size={1:4,4:16}.get(reply[3])
        if reply[3]==3:size=exact(sock,1)[0]
        if size is None:raise OSError('SOCKS5 握手地址无效')
        exact(sock,size+2)
        return sock
    except BaseException:
        sock.close();raise


class SocksAdapter(HTTPAdapter):
    def __init__(self,proxy):
        self.proxy_url=proxy
        super().__init__()

    def init_poolmanager(self,*args,**kwargs):
        proxy=self.proxy_url
        class HTTP(HTTPConnection):
            def _new_conn(self):
                return socks_connect(proxy,self.host,self.port,self.timeout)
        class HTTPS(HTTPSConnection):
            def _new_conn(self):
                return socks_connect(proxy,self.host,self.port,self.timeout)
        class HTTPPool(HTTPConnectionPool):ConnectionCls=HTTP
        class HTTPSPool(HTTPSConnectionPool):ConnectionCls=HTTPS
        super().init_poolmanager(*args,**kwargs)
        self.poolmanager.pool_classes_by_scheme={'http':HTTPPool,'https':HTTPSPool}


def failure_reason(exc):
    text=str(exc).lower()
    if isinstance(exc,requests.HTTPError) and exc.response is not None:return f'HTTP {exc.response.status_code}'
    if 'timed out' in text or 'timeout' in text or '超时' in text:return '连接或读取超时'
    if 'refused' in text or '拒绝' in text:return '连接被拒绝'
    if '认证' in text or '407' in text:return '代理认证失败'
    if 'ssl' in text or 'certificate' in text:return 'TLS 握手或证书校验失败'
    if 'socks' in text or '握手' in text:return 'SOCKS5 握手失败'
    if 'resolve' in text or 'name resolution' in text:return 'DNS 解析失败'
    return '连接失败或代理无响应'


def send_via(session,request,proxy,kwargs):
    """Preserve Requests cookies/redirects while using a per-request private adapter."""
    temporary=requests.Session()
    temporary.trust_env=False
    arguments=dict(kwargs)
    try:
        if urlsplit(proxy).scheme.startswith('socks5'):
            adapter=SocksAdapter(proxy)
            temporary.mount('http://',adapter);temporary.mount('https://',adapter)
            arguments['proxies']={}
        else:arguments['proxies']={'http':proxy,'https':proxy}
        response=_original_send(temporary,request,**arguments)
        session.cookies.update(temporary.cookies)
        # Keep the adapter alive until a streamed response is closed.
        close=response.close
        def finish():
            try:close()
            finally:temporary.close()
        response.close=finish
        if not arguments.get('stream'):temporary.close()
        return response
    except BaseException:
        temporary.close();raise


def send(session,request,**kwargs):
    from .source_env import active_snapshot,redact
    snapshot=active_snapshot()
    proxies=snapshot.get('proxies') if snapshot else None
    if not proxies or _busy.get():return _original_send(session,request,**kwargs)
    token=_busy.set(True)
    reasons=[]
    try:
        if kwargs.get('timeout') is None:kwargs['timeout']=(5,15)
        for index,proxy in enumerate(proxies,1):
            try:
                response=send_via(session,request,proxy,kwargs)
                if response.status_code>=400:
                    code=response.status_code;response.close()
                    reasons.append(f'代理 {index}：HTTP {code}');continue
                return response
            except (requests.RequestException,OSError,ValueError) as exc:
                reasons.append(f'代理 {index}：{failure_reason(exc)}')
        raise requests.ConnectionError(redact('；'.join(reasons)))
    finally:_busy.reset(token)


def install():
    if requests.Session.send is not send:requests.Session.send=send
