/* Refresh old form tokens without discarding edited fields or bypassing CSRF checks. */
(()=>{
  const endpoint=document.querySelector('meta[name="epg-csrf-url"]')?.content;
  if(!endpoint)return;
  let pending=null;
  window.epgCsrfToken=()=>{
    if(!pending)pending=(async()=>{
      const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),10000);
      try{
        const response=await fetch(endpoint,{credentials:'same-origin',cache:'no-store',
          signal:controller.signal,headers:{Accept:'application/json'}});
        if(response.redirected || response.status===403)throw Error('登录已失效，请在新标签页登录后回到此页保存；当前修改仍保留。');
        if(!response.ok)throw Error('无法刷新校验信息，请检查连接后重试。');
        const data=await response.json();
        if(typeof data.token!=='string' || !/^[a-zA-Z0-9]{64}$/.test(data.token))throw Error('校验信息无效，请重新登录后重试。');
        document.querySelectorAll('input[name="csrfmiddlewaretoken"]').forEach(field=>field.value=data.token);
        return data.token;
      }catch(error){
        if(error.name==='AbortError')throw Error('刷新校验信息超时，当前修改仍保留，请重试。');
        throw error;
      }finally{clearTimeout(timer);pending=null;}
    })();
    return pending;
  };
  const ready=new WeakSet(),busy=new WeakSet();
  document.addEventListener('submit',async event=>{
    const form=event.target;
    if(!(form instanceof HTMLFormElement) || form.method.toLowerCase()!=='post')return;
    const action=new URL(form.action,location.href);
    if(action.origin!==location.origin || !form.querySelector('[name="csrfmiddlewaretoken"]'))return;
    if(action.pathname.endsWith('/login/') || action.pathname.endsWith('/logout/'))return;
    if(ready.has(form)){ready.delete(form);return;}
    event.preventDefault();event.stopImmediatePropagation();
    if(busy.has(form))return;
    busy.add(form);
    let status=form.querySelector('.csrf-refresh-status');
    if(!status){status=document.createElement('p');status.className='csrf-refresh-status';status.setAttribute('role','status');form.append(status);}
    status.textContent='正在校验保存请求…';
    const submitter=event.submitter;
    try{
      await window.epgCsrfToken();
      status.textContent='';
      ready.add(form);
      form.requestSubmit(submitter?.form===form?submitter:undefined);
      ready.delete(form);
    }catch(error){status.textContent=error.message;}
    finally{busy.delete(form);}
  },true);
})();
