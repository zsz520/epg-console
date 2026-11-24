"""Bounded HTTP and result validation shared by built-in provider scripts."""
import datetime
from functools import wraps
import re
from urllib.parse import urlsplit

import requests

SHANGHAI = datetime.timezone(datetime.timedelta(hours=8))


class SourceError(ValueError):
    def __init__(self, message, ban=0):
        super().__init__(message)
        self.ban = ban


def error_message(value):
    if isinstance(value, KeyError):
        return f'接口缺少必要字段 {str(value)[:100]}，网站可能已改版。'
    message = str(value)
    for token, explanation in [('JSONDecodeError', '接口没有返回有效 JSON，可能返回错误页面。'),
            ('Expecting value', '接口没有返回有效 JSON，可能返回错误页面。'),
            ('Read timed out', '读取超时：源站响应缓慢，请稍后重试。'),
            ('SSLError', 'TLS 连接失败：请检查设备时间、证书及源站连通性。'),
            ('ConnectionError', '网络连接失败：请检查 DNS、代理与路由。')]:
        if token in message:
            return explanation
    return re.sub(r'https?://[^\s\)]+', '[来源接口]', message)[:700]


class SourceHTTP:
    exceptions = requests.exceptions

    def request(self, method, url, **kwargs):
        # Do not advertise Brotli when the runtime only installs gzip/deflate decoders.
        supplied = dict(kwargs.get('headers') or {})
        supplied['Accept-Encoding'] = 'gzip, deflate'
        kwargs['headers'] = supplied
        kwargs.setdefault('timeout', (8, 20))
        if kwargs['timeout'] is None:
            kwargs['timeout'] = (8, 20)
        try:
            response = requests.request(method, url, **kwargs)
        except requests.exceptions.Timeout:
            raise SourceError('网络请求超时，请检查连通性或稍后重试。') from None
        except requests.exceptions.SSLError:
            raise SourceError('TLS 连接失败，请检查设备时间、证书及源站连通性。') from None
        except requests.exceptions.ProxyError:
            raise SourceError('代理连接失败，请检查容器代理设置。') from None
        except requests.exceptions.ConnectionError:
            raise SourceError('网络连接失败，请检查 DNS 与路由。') from None
        except requests.exceptions.RequestException as exc:
            raise SourceError(f'网络请求异常（{type(exc).__name__}）。') from None
        try:
            if (urlsplit(url).hostname or '') == 'www.cabletv.com.hk' and (urlsplit(response.url).hostname or '') == 'service.i-cable.com':
                raise SourceError('旧有线宽频节目接口已迁移到服务页，未返回原频道节目；请改用可用备用来源。')
            if response.status_code >= 400:
                explanations = {401: '授权无效或已过期，请检查来源授权。', 403: '网站拒绝访问，可能存在访问或地区限制。',
                    404: '接口或频道不存在，请更新来源脚本或检查来源 ID。', 429: '请求过于频繁，请稍后重试。'}
                raise SourceError(f'HTTP {response.status_code}：' + explanations.get(response.status_code, '源站服务异常。'), ban=int(response.status_code in (403, 429)))
            host = urlsplit(response.url).hostname or ''
            original_host = urlsplit(url).hostname or ''
            if original_host == 'epg.i-cable.com' and host in ('hoy.tv', 'www.hoy.tv'):
                raise SourceError('旧 i-Cable 节目接口已重定向到 HOY 网页，不能读取原频道节目；请改用可用备用来源。')
            if not response.content:
                raise SourceError('源站返回空响应。')
            response.encoding = 'utf-8'
            original_json = response.json
            def checked_json(**options):
                try:
                    return original_json(**options)
                except ValueError:
                    raise SourceError('接口没有返回有效 JSON，可能返回错误或验证页面。') from None
            response.json = checked_json
            return response
        finally:
            response.close()  # body is buffered; release the connection before parsing

    def get(self, url, **kwargs):
        return self.request('GET', url, **kwargs)

    def post(self, url, **kwargs):
        return self.request('POST', url, **kwargs)


source_http = SourceHTTP()


def programme_result(name):
    def decorate(function):
        @wraps(function)
        def invoke(channel, identifier, day, *args, **kwargs):
            if isinstance(day, datetime.datetime):
                day = day.date()
            failure = {'success': 0, 'epgs': [], 'msg': '', 'ban': 0, 'last_program_date': day}
            try:
                result = function(channel, identifier, day, *args, **kwargs)
                if not isinstance(result, dict) or not isinstance(result.get('epgs'), list):
                    raise SourceError('来源返回的数据格式无效。')
                if result.get('ban') or result.get('success') == 0:
                    return {**failure, 'ban': result.get('ban', 0), 'msg': f'{name}：' + error_message(result.get('msg') or '来源获取失败，保留原数据。')}
                if not result['epgs']:
                    raise SourceError('该日期没有有效节目，可能尚未发布，或页面/接口已经变化。')
                records, repaired = [], 0
                for number, raw in enumerate(result['epgs'], 1):
                    row = dict(raw)
                    start = row.get('starttime')
                    if not isinstance(start, datetime.datetime):
                        raise SourceError(f'第 {number} 条节目开始时间无效。')
                    start = start.replace(tzinfo=SHANGHAI) if start.tzinfo is None else start.astimezone(SHANGHAI)
                    stop = row.get('endtime')
                    if stop is not None:
                        if not isinstance(stop, datetime.datetime):
                            stop = None
                        else:
                            stop = stop.replace(tzinfo=SHANGHAI) if stop.tzinfo is None else stop.astimezone(SHANGHAI)
                            if stop <= start:
                                stop = None
                        if stop is None:
                            repaired += 1
                    row.update(starttime=start, endtime=stop, program_date=start.date())
                    records.append(row)
                from web.programmes import prepare
                records = prepare(records, strict=True)
                if any(str(row.get('channel_id')) != str(getattr(channel, 'pk', channel.id)) for row in records):
                    raise SourceError('节目所属频道与请求不符。')
                midnight = datetime.datetime.combine(day, datetime.time.min, tzinfo=SHANGHAI)
                if not any(row['program_date'] == day or (row['starttime'] < midnight and row['endtime'] and row['endtime'] > midnight) for row in records):
                    raise SourceError('接口未提供请求日期的节目，未将其他日期节目当作成功。')
                message = result.get('msg') or f'获取 {len(records)} 条节目。'
                if repaired:
                    message += f' {repaired} 条结束时间无效，已交由公共逻辑推断。'
                return {**result, 'success': 1, 'epgs': records, 'msg': error_message(message),
                        'last_program_date': max(row['program_date'] for row in records)}
            except Exception as exc:
                return {**failure, 'ban': getattr(exc, 'ban', 0), 'msg': f'{name}：{error_message(exc)}'}
        return invoke
    return decorate


def catalogue_result(name):
    def decorate(function):
        @wraps(function)
        def invoke(*args, **kwargs):
            try:
                records = function(*args, **kwargs)
                if not isinstance(records, list) or not records:
                    raise SourceError('未获取到有效频道，网站可能已改版或接口不可用；保留原目录。')
                result, seen = [], set()
                for raw in records:
                    row = dict(raw)
                    identifiers = row.get('id')
                    if not isinstance(identifiers, list) or not identifiers or any(v is None or not str(v).strip() for v in identifiers):
                        raise SourceError('频道目录包含无效 ID，未覆盖原目录。')
                    row['id'] = [str(v).strip() for v in identifiers]
                    row['name'] = str(row.get('name') or '').strip()
                    if not row['name']:
                        raise SourceError('频道目录包含空名称，未覆盖原目录。')
                    key = (tuple(row['id']), row.get('sort'), row['name'])
                    if key not in seen:
                        result.append(row); seen.add(key)
                return result
            except Exception as exc:
                raise ValueError(f'{name}频道目录：{error_message(exc)}') from None
        return invoke
    return decorate
