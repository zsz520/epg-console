"""One grouping/order definition shared by admin, public views and exports."""
from django.db.models import Case, When, Value, IntegerField

GROUPS = ('央视', '卫视', '数字付费', '香港', '澳门', '台湾', '海外')
GROUP_PRIORITY = Case(*[When(sort=name, then=Value(index)) for index,name in enumerate(GROUPS)],
                      default=Value(len(GROUPS)), output_field=IntegerField())
CHANNEL_ORDERING = (GROUP_PRIORITY, 'sort', 'sort_order', 'id')


def ordered_channels(queryset):
    return queryset.order_by(*CHANNEL_ORDERING)
