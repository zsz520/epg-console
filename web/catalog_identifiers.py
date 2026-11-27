"""Readable provider IDs for searching; stored mapping IDs remain unchanged."""
import base64
import binascii


def searchable_identifier(source, identifier):
    if source=='xmltv':
        try:
            _,encoded=identifier.split(':',1)
            return base64.b64decode(encoded+'='*(-len(encoded)%4),altchars=b'-_',validate=True).decode('utf-8')
        except (ValueError,UnicodeError,binascii.Error):
            pass
    return identifier
