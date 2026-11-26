from django.shortcuts import render
from django.http import HttpResponse, FileResponse, Http404, JsonResponse
from web.models import Channel, Epg
from utils.general import noepgjson, crawl_info, root_dir
from utils.aboutdb import get_html_info
import datetime, re, json, os
from dateutil import tz
import xml.etree.ElementTree as ET
from urllib.parse import parse_qsl, unquote_plus, urlencode, parse_qs

tz_sh = tz.gettz("Asia/Shanghai")


def index(request, category=''):
    from .options import apply_legacy
    opts=apply_legacy()
    crawl_days = crawl_info["gen_xml_days"]
    from django.utils import timezone
    today = timezone.localdate()
    start_date = (
        today
        .strftime("%Y{y}%m{m}%d{d}")
        .format(y="年", m="月", d="日")
    )
    start_date_no = today.strftime("%Y%m%d")
    end_date_date = today + datetime.timedelta(days=crawl_days - 1)
    end_date = (
        (end_date_date).strftime("%Y{y}%m{m}%d{d}").format(y="年", m="月", d="日")
    )
    info = get_html_info(end_date_date)
    from .categories import filter_category, PRESETS
    from django.db.models import Q
    channels = filter_category(info['channels'], category, request.GET.get('sort', ''))
    from .ordering import ordered_channels
    query = request.GET.get('q', '').strip()[:100]
    if query:
        channels = channels.filter(Q(name__icontains=query) | Q(tvg_name__icontains=query) | Q(tvg_id__icontains=query))
    label = next((label for key, label, _ in PRESETS if key == category), request.GET.get('sort', '') or '全部频道')
    channel_no = info["channels"].count()
    epg_no = info["epg_no"]
    channels=ordered_channels(channels)
    visible_count=channels.count()
    page=None
    if opts['public_page_size']:
        from django.core.paginator import Paginator
        page=Paginator(channels,opts['public_page_size']).get_page(request.GET.get('page'))
        channels=page.object_list
    params=request.GET.copy()
    params.pop('page',None)
    pagination_query=params.urlencode()
    ret = {
        "channel_no": channel_no,
        "channel_total": Channel.objects.count(),
        "crawl_day": crawl_days,
        "epg_no": epg_no,
        "start_date": start_date,
        "start_date_no": start_date_no,
        "end_date": end_date,
        "channels": channels,
        'channel_page':page,
        'channel_offset':page.start_index()-1 if page else 0,
        'pagination_query':pagination_query+'&' if pagination_query else '',
        'category_label': label,
        'category_key': category,
        'visible_channel_count': visible_count,
        'search_query': query,
        "root_dir": root_dir,
        "n": 0,
    }
    return render(request, "public/index.html", context=ret)


def download(requests, title):
    from django.http import HttpResponseNotAllowed
    from django.utils.cache import get_conditional_response
    from django.utils.http import http_date
    import hashlib
    if requests.method not in ('GET','HEAD'):
        return HttpResponseNotAllowed(['GET','HEAD'])
    if title != os.path.basename(title) or title in ('.', '..'):
        raise Http404
    # Keep existing player subscriptions on the current complete export.
    if title in ('all.xml','all.xml.gz'):
        from .options import options
        title=options()['all_filename'] + ('.gz' if title.endswith('.gz') else '')
    try:
        file = open(os.path.join(root_dir, title), "rb")
    except (FileNotFoundError, IsADirectoryError):
        raise Http404
    try:
        stat=os.fstat(file.fileno())
        etag='"'+hashlib.sha256(f'{title}:{stat.st_ino}:{stat.st_mtime_ns}:{stat.st_size}'.encode()).hexdigest()+'"'
        response=get_conditional_response(requests,etag=etag,last_modified=int(stat.st_mtime))
        if response is not None:
            file.close()
        elif requests.method=='HEAD':
            file.close()
            response=HttpResponse()
        else:
            response=FileResponse(file)
        if response.status_code==200:
            response['Content-Length']=str(stat.st_size)
            response['Content-Type']='application/gzip' if title.endswith('.gz') else ('application/xml; charset=utf-8' if title.endswith('.xml') else 'application/octet-stream')
        response['ETag']=etag
        response['Last-Modified']=http_date(stat.st_mtime)
        response['Cache-Control']='no-cache'
        response['X-Content-Type-Options']='nosniff'
    except Exception:
        file.close()
        raise
    return response


def diyp(request):
    from .options import apply_legacy
    from django.utils import timezone
    opts = apply_legacy()
    raw_date = (request.GET.get('date') or '').strip() or timezone.localdate().strftime('%Y%m%d')
    try:
        date_parts = re.fullmatch(r'(\d{4})-?(\d{2})-?(\d{2})', raw_date)
        if not date_parts:
            raise ValueError
        datetime.date(*map(int, date_parts.groups()))
    except ValueError:
        return HttpResponse(json.dumps({'error': 'date 应为有效的 YYYYMMDD 或 YYYY-MM-DD 日期'}, ensure_ascii=False), status=400, content_type='application/json')

    # Preserve a literal '+' in names such as CCTV5+, as well as encoded %2B.
    query_params = parse_qs(request.META.get('QUERY_STRING','').replace('+','%2B'), keep_blank_values=True)
    tvg_names = (query_params.get('ch',[''])[0]).strip() or opts['diyp_default_channel']
    tvg_name = tvg_names
    if not Channel.get_spec_channel_strict(Channel,tvg_name).exists():
        tvg_name = standardize_channel_name(tvg_name)
    request.GET = request.GET.copy()
    request.GET['ch'] = tvg_name
    request.GET['date'] = raw_date
    tvg_date = raw_date
    ret = single_channel_epg(request)
    if ret.get('error_status'):
        return HttpResponse(ret['msg'], status=ret['error_status'], content_type='text/plain; charset=utf-8')
    ret_epgs = ret["epgs"]
    datas = []
    for epg in ret_epgs:
        epg1 = {
            "start": epg["starttime"],
            "end": epg["endtime"],
            "title": epg["title"],
            "desc": epg["descr"],
        }
        datas.append(epg1)

    ret1 = {
        "tvg_name": tvg_names,
        "tvg_date": tvg_date,
        "channel_name": ret["channel"],
        "date": ret["epg_date"].strftime("%Y-%m-%d"),
        "epg_data": datas,
    }
    return JsonResponse(ret1, json_dumps_params={'ensure_ascii':False})


# WEB查询某频道信息接口
def web_single_channel_epg(request):
    ret = single_channel_epg(request)
    if ret.get('error_status'):
        return HttpResponse(ret['msg'], status=ret['error_status'], content_type='text/plain; charset=utf-8')
    ret_epgs = ret["epgs"]
    empty_message = ret['msg'] or '该日期暂无已采集节目。'
    title = "%s -- %s" % (ret["channel"], ret["epg_date"].strftime("%Y-%m-%d"))

    def date_url(delta):
        try:
            date = (ret['epg_date'] + datetime.timedelta(days=delta)).isoformat()
        except OverflowError:
            return '#'
        params = {'ch': ret['tvg_name'], 'date': date}
        if ret.get('channel_id'):
            params['channel_id'] = ret['channel_id']
        return '?' + urlencode(params)
    tomorrow_url, yesterday_url = date_url(1), date_url(-1)
    from .catalog import SOURCE_NAMES
    source = SOURCE_NAMES.get(ret['source'], ret['source'])
    ret = {
        "title": title,
        "tomorrow_url": tomorrow_url,
        "yesterday_url": yesterday_url,
        "epgs": ret_epgs,
        "source": source,
        'empty_message': empty_message,
    }
    return render(request, "public/programme.html", context=ret)


# 请求某个频道数据的通用接口
def single_channel_epg(request):
    from django.utils import timezone
    tvg_name = request.GET.get('ch', '')
    need_date = timezone.localdate()
    result = {'success': 0, 'msg': '', 'epgs': [], 'channel': tvg_name or '未提供',
              'tvg_name': tvg_name, 'epg_date': need_date, 'source': '', 'channel_id': None}
    channel_id = request.GET.get('channel_id') if request.path == '/api/web/' else None
    if request.method != 'GET' or not (tvg_name or channel_id):
        return {**result, 'msg': '请提供频道名称或有效的频道链接。', 'error_status': 400}
    raw_date = request.GET.get('date', need_date.strftime('%Y%m%d'))
    date_match = re.fullmatch(r'(\d{4})-?(\d{2})-?(\d{2})', raw_date)
    try:
        if not date_match:
            raise ValueError
        need_date = datetime.date(*map(int, date_match.groups()))
    except ValueError:
        return {**result, 'msg': 'date 应为有效的 YYYYMMDD 或 YYYY-MM-DD 日期。', 'error_status': 400}
    result['epg_date'] = need_date
    if channel_id is not None:
        try:
            channels = Channel.objects.filter(pk=int(channel_id))
        except (ValueError, TypeError, OverflowError):
            return {**result, 'msg': '频道链接无效。', 'error_status': 400}
    else:
        if tvg_name in ['CCTV5 ', 'IPTV5 ', 'IPTV6 ', 'IPTV3 ']:
            tvg_name = tvg_name.strip() + '+'
        channels = Channel.get_spec_channel_strict(Channel, tvg_name)
    channel = channels.first()
    if channel is None:
        return {**result, 'msg': '没有此频道'}
    records = Epg.get_single_epg(Epg, channel, need_date)
    return {**result, 'success': int(bool(records)), 'channel': channel.name,
            'tvg_name': tvg_name or channel.tvg_name, 'epgs': records,
            'source': next((row['source'] for row in records if row.get('source')),channel.source), 'channel_id': channel.pk}

def standardize_channel_name(channel):

    tid = channel.upper()
    # 第一轮清理，移除特定格式的标识符
    re_patterns = r"\[.*?\]|[0-9\.]+M|[0-9]{3,4}[pP]?|[0-9\.]+FPS"
    tid = re.sub(re_patterns, "", tid)
    tid = tid.strip()
    # 第二轮清理，移除特定的词汇和结尾标识符
    re_patterns = (
        r"超高清|超清|高清|高清$|蓝光|频道|频道$|标清|FHD|HD|HD$|HEVC|HDR|-|\s+"
    )
    tid = re.sub(re_patterns, "", tid)
    tid = tid.strip()

    # 处理特定的 CCTV 格式
    if "CCTV0" in tid:
        tid = re.sub(r"CCTV0+(\d+)", r"CCTV\1", tid)
    # 对包含 CCTV 但不包含特定子字符串的情况进行处理
    if "CCTV" in tid and "CCTV4K" not in tid and "CCTV5+" not in tid:
        re_pattern = r"CCTV[0-9+]{1,2}[48]?K?"
        matches = re.findall(re_pattern, tid)
        if matches:
            tid = tid.replace("4K", "", 1)
        else:
            re_pattern = r"CCTV[^0-9]+"
            matches = re.findall(re_pattern, tid)
            if matches:
                tid = tid.replace("CCTV", "", 1)
    # 特定替换
    tid = tid.replace("BTV", "北京")
    tid = channel_xml(tid)
    return tid


def channel_xml(id, filename=None):
    filename = filename or os.environ.get('EPG_MATCH_FILE', os.path.join(os.path.dirname(os.path.dirname(__file__)), 'epg_match.xml'))
    try:
        # 打开并解析 XML 文件
        tree = ET.parse(filename)
        root = tree.getroot()
        arr = {}

        # 遍历每个 epg 节点
        for epg in root.findall("epg"):
            tvid = epg.find("tvid").text
            epgid = epg.find("epgid").text
            name = epg.find("name").text
            status = epg.find("status").text

            # 跳过状态不为1的条目
            if status != "1":
                continue
            # 处理 name 字段，根据逗号分割并添加到 arr 字典中
            arrData = name.split(",")
            for item in arrData:
                if item.strip() != "":
                    arr[item.strip()] = {"tvid": tvid, "epgid": epgid}

        # 检查映射数组是否为空或者没有对应的 id，则返回原始 id
        if not arr or id not in arr:
            return id

        return arr[id]["epgid"]

    except FileNotFoundError:
        data = {
            "code": 500,
            "msg": "文件获取失败!",
            "name": None,
            "date": None,
            "data": None,
        }
        return json.dumps(data, ensure_ascii=False)  # 返回 JSON 格式的字符串
    except Exception as e:
        data = {"code": 500, "msg": str(e), "name": None, "date": None, "data": None}
        return json.dumps(data, ensure_ascii=False)  # 返回 JSON 格式的字符串



