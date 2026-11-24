document.addEventListener('DOMContentLoaded', () => {
  const toolbar = document.querySelector('.channel-order-toolbar');
  const form = document.getElementById('changelist-form');
  const body = document.querySelector('#result_list tbody');
  if (!toolbar || !body || !form) return;
  const handles = [...body.querySelectorAll('.channel-drag-handle')];
  if (toolbar.dataset.enabled !== 'true') { handles.forEach(h => h.disabled = true); return; }
  let state = JSON.parse(document.getElementById('channel-order-data').textContent);
  const save = toolbar.querySelector('.order-save'), reset = toolbar.querySelector('.order-reset');
  const status = toolbar.querySelector('.order-status');
  let dragging = null, saving = false;
  const ids = () => [...body.querySelectorAll('.channel-drag-handle')].map(h => Number(h.dataset.channelId));
  const dirty = () => JSON.stringify(ids()) !== JSON.stringify(state.ids);
  const update = () => {
    [...body.querySelectorAll('.channel-number')].forEach((node,index)=>node.textContent=String((state.offset||0)+index+1));
    const changed = dirty(); form.dataset.orderDirty = String(changed); save.disabled = !changed || saving; reset.disabled = !changed || saving; status.textContent = changed ? '顺序尚未保存。' : '';
  };
  const groupChanged = () => handles.some(h=>h.closest('tr').querySelector('.field-sort input')?.value !== h.dataset.group);
  handles.forEach(handle => {
    handle.addEventListener('pointerdown', event => {
      if (saving || event.button !== 0) return;
      if(groupChanged()){status.textContent='分组已有修改，请先保存频道字段再拖动。';return;}
      event.preventDefault();
      dragging = handle.closest('tr');
      dragging.classList.add('channel-dragging');
      handle.setPointerCapture(event.pointerId);
    });
    handle.addEventListener('pointermove', event => {
      if (!dragging) return;
      const target = document.elementFromPoint(event.clientX,event.clientY)?.closest('#result_list tbody tr');
      if (target && target !== dragging && target.parentNode === body) {
        if(target.querySelector('.channel-drag-handle')?.dataset.group !== handle.dataset.group){status.textContent='只能在同一分组内拖动；分组顺序固定。';return;}
        const box = target.getBoundingClientRect();
        body.insertBefore(dragging,event.clientY < box.top + box.height/2 ? target : target.nextSibling);
        handle.setPointerCapture(event.pointerId);
        update();
      }
      if (event.clientY < 100) window.scrollBy(0,-20);
      if (event.clientY > window.innerHeight-80) window.scrollBy(0,20);
    });
    const finish = () => { dragging?.classList.remove('channel-dragging'); dragging=null; update(); };
    handle.addEventListener('pointerup',finish); handle.addEventListener('pointercancel',finish);
    handle.addEventListener('keydown', event => {
      if (saving || !['ArrowUp','ArrowDown'].includes(event.key)) return;
      if(groupChanged()){event.preventDefault();status.textContent='分组已有修改，请先保存频道字段再拖动。';return;}
      event.preventDefault(); const row=handle.closest('tr');
      const next=event.key==='ArrowUp' ? row.previousElementSibling : row.nextElementSibling;
      if (next && next.querySelector('.channel-drag-handle')?.dataset.group===handle.dataset.group) { body.insertBefore(row,event.key==='ArrowUp' ? next : next.nextSibling); update(); handle.focus(); }
    });
  });
  reset.addEventListener('click', () => { const rows=new Map(handles.map(h=>[Number(h.dataset.channelId),h.closest('tr')])); state.ids.forEach(id=>body.append(rows.get(id))); update(); });
  save.addEventListener('click', async () => {
    saving=true; update(); status.textContent='正在保存顺序…';
    try {
      const order=ids();
      const response=await fetch(toolbar.dataset.url,{method:'POST',headers:{'Content-Type':'application/json','X-CSRFToken':form.querySelector('[name=csrfmiddlewaretoken]').value},body:JSON.stringify({token:state.token,ids:order})});
      if(response.status===403 || response.redirected) throw new Error('登录已过期或没有保存权限，请重新登录后刷新。');
      if(!response.headers.get('content-type')?.includes('application/json')) throw new Error('服务器未正常返回，请稍后重试。');
      const data=await response.json(); if(!response.ok) throw new Error(data.error || '顺序保存失败，请刷新后重试。');
      state={...state,ids:order,token:data.token}; saving=false; update(); status.textContent='频道顺序与序号已保存，首页同步生效。';
    } catch(error) { saving=false; update(); status.textContent=error.message; }
  });
  form.addEventListener('submit',event=>{ if(dirty()) {event.preventDefault();status.textContent='请先保存顺序或撤销拖动，再保存其他字段。';toolbar.scrollIntoView({block:'nearest'});} });
  window.addEventListener('beforeunload',event=>{if(dirty()){event.preventDefault();event.returnValue='';}});
});
