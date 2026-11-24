(() => {
  for (const prefix of ['epg', 'catalog', 'xmltv']) {
    const mode = document.getElementById(`id_${prefix}_schedule_mode`);
    if (!mode) continue;
    const interval = prefix === 'epg' ? 'interval_seconds' : `${prefix}_interval_seconds`;
    const onStart = prefix === 'epg' ? 'crawl_on_start' : `${prefix}_on_start`;
    const refresh = () => {
      for (const [name, show] of [[interval, mode.value === 'interval'],
        [`${prefix}_interval_unit`, mode.value === 'interval'],
        [onStart, mode.value === 'interval'], [`${prefix}_schedule_times`, mode.value === 'daily']]) {
        const field = document.querySelector(`[data-setting="${name}"]`);
        if (field) {
          field.dataset.scheduleHidden = String(!show);
          field.hidden = !show && !field.querySelector('.errorlist');
        }
      }
      document.dispatchEvent(new Event('settings:visibility'));
    };
    mode.addEventListener('change', refresh);
    refresh();
  }
})();
