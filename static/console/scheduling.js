(() => {
  for (const prefix of ['epg', 'catalog']) {
    const mode = document.getElementById(`id_${prefix}_schedule_mode`);
    if (!mode) continue;
    const interval = prefix === 'epg' ? 'interval_seconds' : 'catalog_interval_seconds';
    const onStart = prefix === 'epg' ? 'crawl_on_start' : 'catalog_on_start';
    const refresh = () => {
      for (const [name, show] of [[interval, mode.value === 'interval'],
        [`${prefix}_interval_unit`, mode.value === 'interval'],
        [onStart, mode.value === 'interval'], [`${prefix}_schedule_times`, mode.value === 'daily']]) {
        const field = document.querySelector(`[data-setting="${name}"]`);
        if (field) field.hidden = !show && !field.querySelector('.errorlist');
      }
    };
    mode.addEventListener('change', refresh);
    refresh();
  }
})();
