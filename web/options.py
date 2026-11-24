"""Typed, public runtime knobs. Deployment/security settings remain in Compose."""
DEFAULTS = {
    'auto_retry_failures':False,'failure_retry_minutes':30,'failure_retry_rounds':3,
    'quality_check':False,'quality_min_percent':50,'quality_min_old_count':10,'quality_gap_minutes':0,
    'auto_backup':False,'backup_hour':4,'backup_keep':5,
    'auto_cleanup':False,'cleanup_log_days':14,'cleanup_cache_days':2,
    'daily_first_full_refresh':False,
    'enable_recrawl':True,
    'traditional_to_simplified':True,
    'crawl_days':2, 'export_days':2, 'recrawl_days':1, 'refresh_existing':False, 'retries':3, 'retry_delay_seconds':1, 'fallback':True,
    'retain_days':14, 'enable_retention':False, 'title_suffix':'', 'description_suffix':'',
    'preview_timeout':15, 'preview_cache_seconds':60, 'preview_error_cache_seconds':15,
    'preview_concurrency':3, 'preview_rows':500, 'preview_previous_day':True, 'fetch_previous_day':True,
    'xmltv_download_mib':100, 'xmltv_expanded_mib':512, 'xmltv_max_programmes':2000000,
    'xmltv_download_seconds':90, 'xmltv_connect_seconds':10, 'xmltv_read_seconds':20,
    'xmltv_snapshot_keep':2, 'xmltv_snapshot_grace_seconds':3600,
    'site_name':'EPG 节目单', 'admin_name':'EPG 控制台', 'list_per_page':50, 'catalog_search_limit':50,
    'task_log_bytes':24000,
    'cc_categories':['央视','卫视'], 'local_categories':['数字付费','辽宁','山东','湖北','四川'],
    'overseas_categories':['香港','澳门','台湾','海外'],
    'cc_filename':'cc.xml', 'local_filename':'difang.xml', 'overseas_filename':'gat.xml',
    'exclude_categories':[], 'include_channels':[],
}


def options(config=None):
    if config is None:
        from .runtime_config import get_config
        config=get_config()
    return {**DEFAULTS, **(config.options or {})}


def apply_legacy(config=None):
    from utils import general
    opts=options(config)
    general.crawl_info.update(max_crawl_days=opts['crawl_days'],gen_xml_days=opts['export_days'],
        del_days=opts['retain_days'],recrawl_days=opts['recrawl_days'],retry_crawl_times=opts['retries'],change_source=int(opts['fallback']))
    for key,prefix in [('yangwei','cc'),('difang','local'),('oversea','overseas')]:
        general.xmlinfo[key].update(basename=opts[prefix+'_filename'],sortname=opts[prefix+'_categories'])
    general.in_exclude_channel.update(xml=opts['exclude_categories'],include_xml=opts['include_channels'])
    general.add_info_title=opts['title_suffix']; general.add_info_desc=opts['description_suffix']
    return opts
