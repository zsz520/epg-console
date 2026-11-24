"""Shared, persistent task configuration; legacy environment values seed it once."""
import os
from .models import RuntimeConfig


def env_int(name, default, minimum, maximum):
    try:
        return max(minimum, min(maximum, int(os.getenv(name, default))))
    except ValueError:
        return default


def get_config():
    config, _ = RuntimeConfig.objects.get_or_create(pk=1, defaults={
        'interval_seconds': env_int('CRAWL_INTERVAL_SECONDS', 21600, 60, 2592000),
        'task_timeout_seconds': env_int('CRAWL_TIMEOUT_SECONDS', 3600, 10, 86400),
        'source_timeout_seconds': env_int('CATALOG_SOURCE_TIMEOUT_SECONDS', 120, 5, 3600),
        'crawl_on_start': os.getenv('CRAWL_ON_START', 'true').lower() != 'false',
    })
    return config
