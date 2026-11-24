"""Reversible text presentation; preserve original source text in snapshots."""
from utils.general import cht_to_chs
from functools import lru_cache


@lru_cache(maxsize=4096)
def short_text(value):
    return cht_to_chs(value)


def display_text(value, opts=None):
    if opts is None:
        from .options import options
        opts = options()
    text = str(value or '')
    return (short_text(text) if len(text)<=500 else cht_to_chs(text)) if opts['traditional_to_simplified'] else text
