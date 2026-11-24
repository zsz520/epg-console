"""User configured XML/XML.GZ sources, read from the latest successful local snapshot."""


def get_epgs_xmltv(channel, channel_id, dt, func_arg=0):
    from web.xmltv import get_programmes
    return get_programmes(channel, channel_id, dt)
