document.addEventListener('DOMContentLoaded',()=>{
  const form=document.querySelector('.source-environment-form');
  if(!form)return;
  const panel=form.querySelector('.source-environment-tests');
  const status=panel.querySelector('.environment-test-status');
  let dirty=false;
  form.querySelectorAll('textarea').forEach(field=>field.addEventListener('input',()=>{dirty=true;}));
  panel.addEventListener('click',async event=>{
    const button=event.target.closest('.environment-test');
    if(!button)return;
    if(dirty){status.textContent='配置已修改，请先保存后再测试。';return;}
    const buttons=[...panel.querySelectorAll('.environment-test')];
    buttons.forEach(b=>b.disabled=true);
    status.textContent='正在建连并请求来源健康地址…';
    const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),60000);
    try{
      const response=await fetch(panel.dataset.testUrl,{method:'POST',signal:controller.signal,headers:{
        'Content-Type':'application/json','X-CSRFToken':form.querySelector('[name=csrfmiddlewaretoken]').value
      },body:JSON.stringify({index:button.dataset.index==='all'?null:Number(button.dataset.index)})});
      if(response.redirected)throw Error('登录已过期，请重新登录。');
      const data=await response.json();
      if(!response.ok || data.error)throw Error(data.error || '测试失败，请稍后重试。');
      for(const row of data.results || []){
        const cell=panel.querySelector(`[data-proxy-index="${row.index}"] .environment-test-result`);
        if(cell)cell.textContent=`${row.success?'成功':'失败'} · ${row.elapsed_ms} ms · ${row.reason}`;
      }
      status.textContent='本次测试完成。';
    }catch(error){status.textContent=error.name==='AbortError'?'测试超时，请检查网络。':error.message;}
    finally{clearTimeout(timer);buttons.forEach(b=>b.disabled=false);}
  });
});
