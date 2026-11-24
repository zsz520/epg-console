document.addEventListener('DOMContentLoaded', () => {
  const form = document.getElementById('changelist-form');
  const dialog = document.querySelector('.channel-quick-dialog');
  if (!form || !dialog) return;
  const frame = dialog.querySelector('iframe');
  const warning = dialog.querySelector('.quick-close-warning');
  const notice = document.querySelector('.list-edit-notice');
  const snapshot = () => JSON.stringify([...new FormData(form).entries()]
    .filter(([key]) => key.startsWith('form-')).sort(([a],[b]) => a.localeCompare(b)));
  const initial = snapshot();
  const close = () => {
    try {
      if (frame.contentWindow.epgQuickDirty?.()) { warning.hidden = false; return; }
    } catch {}
    dialog.close();
  };
  document.querySelectorAll('.channel-quick-edit').forEach(button => button.addEventListener('click', () => {
    if (snapshot() !== initial || form.dataset.orderDirty === 'true') {
      notice.textContent = '列表有未保存修改或拖动顺序，请先保存，再打开编辑 / 补全。';
      notice.scrollIntoView({block:'nearest'}); return;
    }
    notice.textContent = '';
    warning.hidden = true;
    dialog.querySelector('h2').textContent = '编辑 / 补全 · ' + button.dataset.channelName;
    frame.src = button.dataset.quickUrl;
    dialog.showModal();
  }));
  dialog.querySelector('.quick-close').addEventListener('click', close);
  dialog.addEventListener('cancel', event => { event.preventDefault(); close(); });
  dialog.querySelector('.quick-discard').addEventListener('click', () => dialog.close());
  dialog.querySelector('.quick-continue').addEventListener('click', () => { warning.hidden = true; });
  window.addEventListener('message', event => {
    if (event.origin === window.location.origin && event.source === frame.contentWindow && event.data?.type === 'epg-channel-saved') {
      dialog.close(); window.location.reload();
    }
  });
});
