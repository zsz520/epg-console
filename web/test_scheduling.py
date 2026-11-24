import datetime
from django.test import TestCase
from django.contrib.auth import get_user_model
from .models import Task, AutoScheduleState
from .runtime_config import get_config
from .scheduling import tick, completed, parse_times, next_daily, ZONE
from .forms import RuntimeConfigForm
from .tasks import enqueue


class SchedulingTests(TestCase):
    def setUp(self):
        self.now = datetime.datetime(2026,11,23,8,0,20,tzinfo=ZONE)
        self.config = get_config()
        self.config.auto_crawl = False
        self.config.auto_catalog = False
        self.config.save()

    def enable(self, **kwargs):
        for key,value in kwargs.items(): setattr(self.config,key,value)
        self.config.save()

    def form_data(self, **kwargs):
        data = {key:getattr(self.config,key) for key in RuntimeConfigForm.Meta.fields}
        data.update(_settings_full='1', **kwargs)
        return data

    def test_disabled_defaults_schedule_nothing_and_manual_still_works(self):
        tick(self.now)
        self.assertEqual(Task.objects.count(),0)
        self.assertEqual(enqueue('catalog')[1],True)

    def test_two_schedules_independent_interval_and_daily(self):
        self.enable(auto_crawl=True,crawl_on_start=True,auto_catalog=True,
                    catalog_schedule_mode='daily',catalog_schedule_times='08:00,12:00')
        tick(self.now)
        self.assertEqual(list(Task.objects.values_list('kind',flat=True)),['epg','catalog'])
        self.assertEqual(AutoScheduleState.objects.get(pk='catalog').next_run.astimezone(ZONE).hour,12)
        tick(self.now+datetime.timedelta(seconds=1))
        self.assertEqual(Task.objects.count(),2)

    def test_daily_multiple_times_and_next_day(self):
        self.enable(auto_catalog=True,catalog_schedule_mode='daily',catalog_schedule_times='08:00,12:30,23:59')
        tick(self.now)
        Task.objects.update(status='success')
        tick(self.now.replace(hour=12,minute=30))
        self.assertEqual(Task.objects.count(),2)
        Task.objects.update(status='success')
        tick(self.now.replace(hour=23,minute=59))
        self.assertEqual(Task.objects.count(),3)
        nxt=AutoScheduleState.objects.get(pk='catalog').next_run.astimezone(ZONE)
        self.assertEqual((nxt.day,nxt.hour,nxt.minute),(24,8,0))

    def test_daily_first_enable_does_not_run_all_past_slots(self):
        self.enable(auto_crawl=True,epg_schedule_mode='daily',epg_schedule_times='06:00,09:00')
        tick(self.now)
        self.assertFalse(Task.objects.exists())
        self.assertEqual(AutoScheduleState.objects.get(pk='epg').next_run.astimezone(ZONE).hour,9)

    def test_offline_slots_coalesce_once_and_state_survives_reentry(self):
        self.enable(auto_crawl=True,epg_schedule_mode='daily',epg_schedule_times='08:00,12:00,18:00')
        tick(self.now-datetime.timedelta(hours=1))
        tick(self.now+datetime.timedelta(days=3,hours=12))
        self.assertEqual(Task.objects.count(),1)
        Task.objects.update(status='success')
        tick(self.now+datetime.timedelta(days=3,hours=12,seconds=1))
        self.assertEqual(Task.objects.count(),1)

    def test_interval_waits_from_completion_including_failed_task(self):
        self.enable(auto_catalog=True,catalog_on_start=True,catalog_interval_seconds=3600)
        tick(self.now)
        task=Task.objects.get();task.status='failed';task.save()
        completed(task,self.now+datetime.timedelta(hours=2))
        due=AutoScheduleState.objects.get(pk='catalog').next_run
        self.assertEqual(due,self.now+datetime.timedelta(hours=3))
        tick(self.now+datetime.timedelta(hours=2,minutes=59));self.assertEqual(Task.objects.count(),1)
        tick(due);self.assertEqual(Task.objects.count(),2)

    def test_no_immediate_start_waits_and_configuration_change_rebases(self):
        self.enable(auto_crawl=True,crawl_on_start=False,interval_seconds=3600)
        tick(self.now);self.assertEqual(Task.objects.count(),0)
        self.enable(interval_seconds=7200)
        tick(self.now+datetime.timedelta(minutes=10))
        self.assertEqual(AutoScheduleState.objects.get(pk='epg').next_run,self.now+datetime.timedelta(hours=2,minutes=10))

    def test_upgrade_uses_existing_automatic_completion_anchor(self):
        finish=self.now-datetime.timedelta(minutes=10)
        Task.objects.create(kind='epg',status='success',finished_at=finish)
        self.enable(auto_crawl=True,crawl_on_start=True,interval_seconds=3600)
        tick(self.now)
        self.assertEqual(Task.objects.count(),1)
        self.assertEqual(AutoScheduleState.objects.get(pk='epg').next_run,finish+datetime.timedelta(hours=1))

    def test_manual_and_auto_whole_tasks_deduplicate_both_directions(self):
        self.enable(auto_crawl=True,crawl_on_start=True)
        manual,_=enqueue('epg');tick(self.now)
        self.assertEqual(Task.objects.count(),1)
        manual.status='success';manual.save();Task.objects.all().delete()
        AutoScheduleState.objects.all().delete();tick(self.now)
        task,created=enqueue('epg')
        self.assertFalse(created);self.assertTrue(task.params['automatic'])
        self.assertEqual(Task.objects.count(),1)

    def test_running_jobs_do_not_accumulate_or_lose_other_kind(self):
        self.enable(auto_crawl=True,crawl_on_start=True,auto_catalog=True,catalog_schedule_mode='daily',catalog_schedule_times='08:00,09:00')
        tick(self.now)
        Task.objects.filter(kind='catalog').update(status='running')
        tick(self.now.replace(hour=9));tick(self.now.replace(hour=10))
        self.assertEqual(Task.objects.filter(kind='catalog').count(),1)
        self.assertEqual(Task.objects.filter(kind='epg').count(),1)

    def test_switch_off_stops_new_jobs_without_cancelling_existing(self):
        self.enable(auto_catalog=True,catalog_on_start=True);tick(self.now)
        self.enable(auto_catalog=False);tick(self.now+datetime.timedelta(days=5))
        self.assertEqual(Task.objects.count(),1)
        self.assertEqual(Task.objects.get().status,'queued')

    def test_partial_manual_completion_does_not_move_whole_schedule(self):
        self.enable(auto_crawl=True,crawl_on_start=False);tick(self.now)
        due=AutoScheduleState.objects.get(pk='epg').next_run
        task=Task.objects.create(kind='epg',params={'channel_ids':[1]})
        completed(task,self.now+datetime.timedelta(days=1))
        self.assertEqual(AutoScheduleState.objects.get(pk='epg').next_run,due)

    def test_time_parser_normalizes_deduplicates_and_checks_bounds(self):
        self.assertEqual(parse_times('6:00，18:30\n06:00; 00:00'),['00:00','06:00','18:30'])
        for text in ['24:00','12:60','06:0','every hour','-1:00']:
            with self.assertRaises(ValueError):parse_times(text)
        with self.assertRaises(ValueError):parse_times(','.join(f'01:{i:02d}' for i in range(49)))
        utc=self.now.astimezone(datetime.timezone.utc)
        self.assertEqual(next_daily(utc,['09:00']).hour,9)

    def test_form_interval_units_and_normalized_daily_times_persist(self):
        form=RuntimeConfigForm(self.form_data(interval_seconds=6,epg_interval_unit='3600',
             catalog_interval_seconds=2,catalog_interval_unit='86400',catalog_schedule_mode='daily',
             catalog_schedule_times='4:00，16:00\n04:00'),instance=self.config)
        self.assertTrue(form.is_valid(),form.errors)
        result=form.save()
        self.assertEqual((result.interval_seconds,result.catalog_interval_seconds),(21600,172800))
        self.assertEqual(result.catalog_schedule_times,'04:00,16:00')
        initial=RuntimeConfigForm(instance=result).initial
        self.assertEqual((initial['interval_seconds'],initial['epg_interval_unit']),(6,'3600'))

    def test_invalid_empty_daily_or_interval_limits_rejected(self):
        for data in [self.form_data(epg_schedule_mode='daily',epg_schedule_times=''),
                     self.form_data(interval_seconds=31,epg_interval_unit='86400'),
                     self.form_data(catalog_schedule_times='25:00')]:
            form=RuntimeConfigForm(data,instance=self.config)
            self.assertFalse(form.is_valid())

    def test_settings_page_sections_and_controls(self):
        user=get_user_model().objects.create_superuser('schedule-admin',password='test-only')
        self.client.force_login(user)
        response=self.client.get('/admin/settings/')
        for text in ['节目单自动抓取','频道列表自动抓取','北京时间','id_catalog_schedule_mode','/console/scheduling.']:
            self.assertContains(response,text)
