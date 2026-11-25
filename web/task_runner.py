import gzip
import json
import os
from pathlib import Path
import subprocess
import sys
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'epg.docker_settings')
import django
django.setup()
from .models import Task
from .catalog import SOURCE_NAMES


def main(pk):
    from .options import apply_legacy, options
    apply_legacy()
    opts = options()
    task = Task.objects.get(pk=pk)
    if task.kind in ('retry','backup','restore','cleanup'):
        if task.kind=='retry':
            from .failures import run
            run(task);return
        if task.kind=='backup':
            from .backups import create
            name=create()
            Task.objects.filter(pk=pk).update(status='success',summary='完整备份已生成，可到维护中心下载。',details=[{'backup':name}]);return
        if task.kind=='cleanup':
            from .maintenance import execute,candidates
            from django.utils import timezone
            import datetime
            from .options import options
            plan=candidates() if task.params.get('automatic') else task.params.get('plan',[])
            cutoff=task.params.get('cutoff') or (timezone.now()-datetime.timedelta(days=options()['cleanup_log_days'])).isoformat()
            files,rows=execute(plan,cutoff)
            Task.objects.filter(pk=pk).update(status='success',summary=f'已清理 {files} 个旧文件、{rows} 条抓取日志；频道、节目和来源均保留。');return
        if task.kind=='restore':
            if not task.requested_by or not task.requested_by.is_active or not task.requested_by.is_superuser:raise PermissionError('恢复仅限当前有效管理员。')
            from .backups import request_restore
            Task.objects.filter(pk=pk).update(status='success',summary='备份已确认，单容器将自动重启恢复；恢复后使用备份中的账号登录。分容器模式请停止 crawler 后重启 web。')
            request_restore(task.params);return
    if task.kind=='purge':
        from .programme_management import execute
        from .tasks import enqueue
        from django.db import transaction
        with transaction.atomic():
            count=execute(task.params)
            export,_=enqueue('xml',user=task.requested_by,params={'after_purge':task.pk})
            Task.objects.filter(pk=pk).update(status='success',summary=f'已删除 {count} 条节目；频道和来源配置保留。XML 更新任务 #{export.pk} 已排队。')
        return
    from .xmltv import refresh_feeds_for_task
    xmltv_results = []
    if task.kind == 'xmltv' or (task.kind == 'epg' and opts['xmltv_with_epg']):
        xmltv_results = refresh_feeds_for_task(task)
    if task.kind == 'xmltv':
        passed = sum(row['status'] == 'success' for row in xmltv_results)
        Task.objects.filter(pk=pk).update(status='success' if passed == len(xmltv_results) else ('partial' if passed else 'failed'),
            summary=f'{passed}/{len(xmltv_results)} 个 XMLTV 来源更新成功；成功来源已同步目录，失败时保留原数据。节目入库请执行抓取节目单。')
        return
    if task.kind == 'catalog':
        from .runtime_config import get_config
        source_timeout = get_config().source_timeout_seconds
        results = []
        if task.source:
            sources = [task.source]
        elif task.params.get('sources'):
            sources = list(dict.fromkeys(task.params['sources']))
        elif task.params.get('all_sources'):
            sources = list(SOURCE_NAMES)
        else:
            selected = opts['catalog_sources']
            sources = [key for key in SOURCE_NAMES if key != 'xmltv' and (not selected or key in selected)]
            if opts['xmltv_with_catalog']:
                sources.append('xmltv')
        if not sources:
            Task.objects.filter(pk=pk).update(status='failed', summary='当前自动刷新范围没有已加载来源，请检查来源范围或启用来源文件。')
            return
        for source in sources:
            if source == 'xmltv':
                feeds = refresh_feeds_for_task(task)
                failed = any(row['status'] != 'success' for row in feeds)
                results.append({'source': source, 'status': 'failed' if failed else 'success', 'feeds': feeds})
                continue
            if source not in SOURCE_NAMES:
                results.append({'source': source, 'status': 'failed', 'error': '来源未注册或已停用，请检查来源文件。'})
                continue
            print(f'正在抓取 {SOURCE_NAMES[source]} ({source})…', flush=True)
            Task.objects.filter(pk=pk).update(summary=f'正在抓取 {SOURCE_NAMES[source]}；已处理 {len(results)}/{len(sources)} 个来源')
            for attempt in range(1, opts['catalog_retries'] + 1):
                result_file = Path(os.getenv('EPG_DATA_DIR', '/data')) / 'tasks' / f'{pk}-{source}.json'
                try:
                    result_file.unlink(missing_ok=True)
                    child = subprocess.run([sys.executable, '-m', 'web.catalog_runner', source, str(result_file)],
                                           timeout=source_timeout)
                    if child.returncode or not result_file.exists():
                        raise ValueError(f'来源执行失败（退出码 {child.returncode}），详情见运行日志。')
                    result = {**json.loads(result_file.read_text(encoding='utf-8')), 'status': 'success', 'attempts': attempt}
                    break
                except Exception as exc:
                    result = {'source': source, 'status': 'failed', 'error': str(exc)[-1000:], 'attempts': attempt}
                    print(f'{source} 第 {attempt}/{opts["catalog_retries"]} 次获取失败：{exc}', flush=True)
                    if attempt < opts['catalog_retries']:
                        import time
                        time.sleep(opts['catalog_retry_delay_seconds'])
                finally:
                    result_file.unlink(missing_ok=True)
            results.append(result)
            Task.objects.filter(pk=pk).update(details=results, summary=f'已处理 {len(results)}/{len(sources)} 个来源')
        passed = sum(r['status'] == 'success' for r in results)
        Task.objects.filter(pk=pk).update(
            status='success' if passed == len(results) else ('partial' if passed else 'failed'),
            summary=f'{passed}/{len(results)} 个来源更新成功；失败或空列表保留原有目录。')
    else:
        if task.params.get('channel_ids'):
            os.environ['EPG_CHANNEL_IDS'] = ','.join(map(str, task.params['channel_ids']))
        from crawl.crawl import main as crawl_main, gen_xml
        if task.kind == 'epg':
            crawl_main()
            from django.utils import timezone
            from .models import Channel, Epg
            channels = Channel.objects.filter(ineed=1, has_epg=1)
            if task.params.get('channel_ids'):
                channels = channels.filter(pk__in=task.params['channel_ids'])
            ids = [str(pk) for pk in channels.values_list('pk', flat=True)]
            covered = Epg.objects.filter(channel_id__in=ids, program_date=timezone.localdate()).values('channel_id').distinct().count()
            xmltv_failed = sum(row['status'] != 'success' for row in xmltv_results)
            from .models import CrawlFailure
            failures=CrawlFailure.objects.filter(channel_id__in=ids,resolved=False,
                last_failed__gte=task.started_at or task.created_at,program_date__gte=timezone.localdate()).count()
            Task.objects.filter(pk=pk).update(status='success' if covered == len(ids) and not xmltv_failed else ('partial' if covered or not ids else 'failed'),
                summary=f'已生成 XML；今日 {covered}/{len(ids)} 个启用频道有节目数据。本轮失败项 {failures} 个，可在维护中心补抓。XMLTV 更新失败 {xmltv_failed} 个，失败时保留原数据。')
            if failures:Task.objects.filter(pk=pk).update(status='partial' if covered else 'failed')
        else:
            from utils.general import xmlinfo
            for sort in xmlinfo:
                _, filename = gen_xml(sort)
                temp = filename + '.gz.tmp'
                with open(filename, 'rb') as src, gzip.open(temp, 'wb') as dst:
                    import shutil
                    shutil.copyfileobj(src, dst)
                os.replace(temp, filename + '.gz')
            from crawl.crawl import gen_test_m3u
            from utils.general import dirs
            from .models import Channel
            gen_test_m3u(Channel.get_need_channels(Channel, 'all')[0], dirs['testm3u_dir'])


if __name__ == '__main__':
    main(int(sys.argv[1]))
