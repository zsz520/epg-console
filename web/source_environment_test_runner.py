"""Only tests server-chosen literal source targets; never accepts a target URL."""
import concurrent.futures
import json
import os
import sys
import time
os.environ.setdefault('DJANGO_SETTINGS_MODULE','epg.docker_settings')
import django
django.setup()
import requests
from crawl.source_registry import discover
from crawl.proxy_transport import send_via,failure_reason
from .models import SourceEnvironment
from .source_environment import source_targets,mask_proxy
from .source_environment_views import container_proxies


def main():
    source=discover()[0][sys.argv[1]]
    targets=source_targets(source)
    if source.id=='xmltv':
        from .models import XmltvFeed
        # XML URLs are already registered/admin-controlled, not taken from the test request.
        targets=list(XmltvFeed.objects.filter(enabled=True).values_list('url',flat=True)[:1])
    if not targets:
        print(json.dumps({'error':'此来源未声明可测试的健康地址，请在来源文件中设置常量 SOURCE_HEALTH_URL。'}));return
    row=SourceEnvironment.objects.filter(source_id=source.id).first()
    proxies=row.proxies.splitlines() if row and row.proxies else container_proxies(source.id)[0]
    if not proxies:
        print(json.dumps({'error':'尚未配置代理，请先保存。'}));return
    params=json.loads(sys.stdin.read(1000));index=params.get('index')
    if index is not None and index>=len(proxies):
        print(json.dumps({'error':'代理列表已变化，请刷新页面。'}));return
    chosen=[(index,proxies[index])] if index is not None else list(enumerate(proxies))
    target=targets[0]
    def check(item):
        index,proxy=item;start=time.monotonic();result={'index':index,'proxy':mask_proxy(proxy)}
        session=requests.Session();session.trust_env=False
        try:
            request=requests.Request('GET',target,headers={'User-Agent':'EPG-Source-Health/1.0','Range':'bytes=0-1023'}).prepare()
            response=send_via(session,request,proxy,{'timeout':(4,5),'stream':True,'allow_redirects':False,'verify':True,'cert':None})
            with response:
                if 300<=response.status_code<400:raise ValueError('跳转被拒绝：测试只访问来源白名单地址')
                if response.status_code>=400:
                    result.update(success=False,reason=f'HTTP {response.status_code}')
                else:
                    next(response.iter_content(1024),b'')
                    result.update(success=True,reason=f'HTTP {response.status_code}，建连及读取成功')
        except ValueError:result.update(success=False,reason='跳转被拒绝：测试不访问其他目标')
        except Exception as exc:result.update(success=False,reason=failure_reason(exc))
        finally:session.close()
        result['elapsed_ms']=round((time.monotonic()-start)*1000)
        return result
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        result=list(pool.map(check,chosen))
    print(json.dumps({'results':result},ensure_ascii=False))


if __name__=='__main__':main()
