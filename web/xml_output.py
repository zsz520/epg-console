"""Escape provider/user text as valid XML 1.0 content and attributes."""
import re
from xml.sax.saxutils import escape, quoteattr

_INVALID = re.compile('[^\x09\x0a\x0d\x20-\ud7ff\ue000-\ufffd\U00010000-\U0010ffff]')


def clean(value):
    return _INVALID.sub('', str(value or ''))


def text(value):
    return escape(clean(value))


def attribute(value):
    # Preserve whitespace exactly through XML attribute normalization.
    return quoteattr(clean(value), {'\n':'&#10;', '\r':'&#13;', '\t':'&#9;'})
