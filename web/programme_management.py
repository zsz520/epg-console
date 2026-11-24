"""Preview and execute bounded, serialized programme deletions."""
import datetime
from django import forms
from django.core import signing
from django.db import transaction
from django.db.models import Max
from django.db.models.functions import Cast
from django.db.models import CharField, OuterRef, Subquery
from .models import Channel, Epg


class PurgeForm(forms.Form):
    scope = forms.ChoiceField(label='删除范围', choices=[('dates','指定日期范围（含起止日期）'),('all','全部节目单')])
    start = forms.DateField(label='开始日期',required=False,widget=forms.DateInput(attrs={'type':'date'}))
    end = forms.DateField(label='结束日期',required=False,widget=forms.DateInput(attrs={'type':'date'}))

    def clean(self):
        data=super().clean()
        if data.get('scope')=='dates':
            if not data.get('start') or not data.get('end'):
                raise forms.ValidationError('请选择开始和结束日期；删除一天时填相同日期。')
            if data['start']>data['end']:
                raise forms.ValidationError('开始日期不能晚于结束日期。')
        return data


def selection(params):
    query=Epg.objects.all()
    if params.get('scope')=='dates':
        start=datetime.date.fromisoformat(params['start'])
        end=datetime.date.fromisoformat(params['end'])
        if start>end: raise ValueError('删除日期范围无效。')
        query=query.filter(program_date__range=(start,end))
    elif params.get('scope')!='all':
        raise ValueError('删除范围无效。')
    if 'max_pk' in params: query=query.filter(pk__lte=int(params['max_pk']))
    return query


def preview(data,user):
    params={'scope':data['scope']}
    if data['scope']=='dates':params.update(start=data['start'].isoformat(),end=data['end'].isoformat())
    query=selection(params)
    params.update(max_pk=query.aggregate(n=Max('pk'))['n'] or 0,count=query.count())
    token=signing.dumps({'user':user.pk,'params':params},salt='programme-purge',compress=True)
    return params,token


def confirm(token,user):
    payload=signing.loads(token,salt='programme-purge',max_age=900)
    if payload.get('user')!=user.pk:raise ValueError('删除确认不属于当前账号。')
    return payload['params']


def execute(params):
    with transaction.atomic():
        query=selection(params)
        count=query.count()
        if count!=params['count']:
            raise ValueError('节目数据已变化，请重新预览删除范围；本次未删除。')
        affected=list(query.values_list('channel_id',flat=True).distinct())
        deleted,_=query.delete()
        if deleted!=count:raise ValueError('节目数据已变化，本次未删除；请重新预览。')
        remaining=Epg.objects.filter(channel_id=Cast(OuterRef('pk'),output_field=CharField())).order_by('-program_date').values('program_date')[:1]
        Channel.objects.annotate(channel_key=Cast('pk',output_field=CharField())).filter(channel_key__in=affected).update(last_program_date=Subquery(remaining))
    return count
