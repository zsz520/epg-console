document.addEventListener('DOMContentLoaded', () => {
  if (document.body.dataset.saved === 'true') {
    if (window.parent !== window) window.parent.postMessage({type:'epg-channel-saved'}, window.location.origin);
    return;
  }
  const form = document.getElementById('channel_form');
  const snapshot = () => JSON.stringify([...new FormData(form).entries()]);
  const initial = snapshot();
  window.epgQuickDirty = () => document.body.dataset.hasErrors === 'true' || snapshot() !== initial;
  document.querySelector('.quick-fill')?.addEventListener('click', () => {
    let count = 0;
    const get = name => document.getElementById('id_' + name);
    const fill = (name, value) => {
      const field = get(name);
      if (!field.value.trim() && value) { field.value = value; field.dispatchEvent(new Event('input',{bubbles:true})); count++; }
    };
    fill('name', get('tvg_name').value.trim());
    fill('tvg_name', get('name').value.trim());
    fill('tvg_id', get('tvg_name').value.trim());
    fill('sort', '其他');
    if (!get('source').value) {
      let keys=[];
      try { keys=[...window.EpgSourceMapping.parse(get('channel_id').value).keys()]; } catch { /* The picker displays invalid mapping errors. */ }
      const source = keys.find(key=>[...get('source').options].some(o=>o.value===key));
      if (source) { get('source').value=source; get('source').dispatchEvent(new Event('change',{bubbles:true})); count++; }
    }
    document.querySelector('.quick-fill-notice').textContent = count ? `已补全 ${count} 个空字段，请核对后保存。来源 ID 请从目录选择补充。` : '这些字段已经完整；新增来源请在下方搜索目录并选择。';
  });
});
