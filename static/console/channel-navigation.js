document.addEventListener('DOMContentLoaded', () => {
  const form = document.getElementById('channel_form');
  if (!form) return;
  const controls = [...form.querySelectorAll('.channel-navigation')];
  if (!controls.length) return;
  const snapshot = () => JSON.stringify([...new FormData(form).entries()]
    .filter(([name]) => !['csrfmiddlewaretoken', '_channel_direction'].includes(name)));
  const initial = snapshot();
  const hasErrors = controls.some(control => control.dataset.hasErrors === 'true');
  let pending = null;
  const close = () => controls.forEach(control => { control.querySelector('.channel-navigation-warning').hidden = true; });
  form.querySelectorAll('[data-channel-direction]').forEach(link => {
    link.addEventListener('click', event => {
      if (!hasErrors && snapshot() === initial) return;
      event.preventDefault();
      close();
      pending = {direction: link.dataset.channelDirection, url: link.href};
      link.closest('.channel-navigation').querySelector('.channel-navigation-warning').hidden = false;
    });
  });
  form.querySelectorAll('[data-navigation-action]').forEach(button => {
    button.addEventListener('click', () => {
      if (!pending) return;
      const action = button.dataset.navigationAction;
      if (action === 'save') {
        form.querySelector('[name="_channel_direction"]').value = pending.direction;
        form.requestSubmit();
      } else if (action === 'discard') {
        window.location.assign(pending.url);
      } else {
        pending = null;
        close();
      }
    });
  });
});
