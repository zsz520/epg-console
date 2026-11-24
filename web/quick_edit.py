import hashlib
import json
from django import forms
from .forms import ChannelForm
from .models import Channel

FIELDS = ('name', 'tvg_id', 'tvg_name', 'sort', 'logo', 'source', 'channel_id', 'has_epg', 'ineed', 'recrawl', 'sort_order')


def revision(channel):
    values = {key: getattr(channel, key) for key in FIELDS}
    return hashlib.sha256(json.dumps(values, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


class QuickChannelForm(ChannelForm):
    class Meta(ChannelForm.Meta):
        model = Channel
        fields = ('name', 'tvg_id', 'tvg_name', 'sort', 'logo', 'catalog_entries',
                  'source', 'channel_id', 'has_epg', 'ineed', 'recrawl', 'sort_order')

    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.fields['recrawl'].required=False

    def clean_recrawl(self):
        value=self.cleaned_data.get('recrawl')
        return self.instance.recrawl if value in (None,'') else int(value)
