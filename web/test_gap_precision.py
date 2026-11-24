import datetime
from django.test import SimpleTestCase
from django.utils import timezone
from .programmes import project


class GapPrecisionTests(SimpleTestCase):
    day = datetime.date(2026, 11, 23)

    def dt(self, hour, minute=0, second=0):
        return timezone.make_aware(datetime.datetime.combine(self.day, datetime.time(hour, minute, second)))

    def row(self, start, end, title='实际节目'):
        return dict(channel_id='1', starttime=start, endtime=end, title=title)

    def gaps(self, rows, split=True):
        return [r for r in project(rows,date=self.day,split=split,fill=True) if r.get('gap_filler')]

    def test_2359_end_never_creates_2359_to_2359_placeholder(self):
        for second in [0, 20, 59]:
            for split in [True,False]:
                self.assertEqual(self.gaps([self.row(self.dt(0),self.dt(23,59,second))],split),[])

    def test_same_minute_head_and_internal_gaps_are_omitted(self):
        rows=[self.row(self.dt(0,0,30),self.dt(12,0,10)),
              self.row(self.dt(12,0,40),self.dt(23,59))]
        self.assertEqual(self.gaps(rows),[])

    def test_displayable_gaps_remain_and_real_seconds_are_unchanged(self):
        raw=[self.row(self.dt(0,1),self.dt(12,0,10)),
             self.row(self.dt(12,1,40),self.dt(23,58))]
        result=project(raw,date=self.day,split=True,fill=True)
        self.assertEqual([(r['starttime'].strftime('%H:%M'),r['endtime'].strftime('%H:%M'))
                          for r in result if r.get('gap_filler')],
                         [('00:00','00:01'),('12:00','12:01'),('23:58','23:59')])
        self.assertEqual(raw[0]['endtime'].second,10)
        self.assertEqual([r for r in result if not r.get('gap_filler')][1]['starttime'].second,40)

    def test_midnight_crossing_does_not_add_zero_minute_tail(self):
        row=self.row(self.dt(23),self.dt(0)+datetime.timedelta(days=1,minutes=30))
        result=project([row],date=self.day,split=True,fill=True)
        self.assertEqual(result[-1]['title'],'实际节目')
        self.assertEqual(result[-1]['endtime'].strftime('%H:%M'),'23:59')
        self.assertFalse(result[-1].get('gap_filler'))
