(() => {
  let timer,loading=false,finished=false;
  const summary=document.getElementById('task-summary');
  const labels={success:'成功',failed:'失败',partial:'部分完成',running:'运行中',queued:'排队中',cancelled:'已取消'};
  const resultLine=result=>{
    const line=document.createElement('div');line.className='task-result-row';
    if(result.backup){line.textContent=`完整备份：${result.backup}，可到维护中心下载。`;return line;}
    const name=result.name || result.source || (result.feed_id ? `XMLTV #${result.feed_id}` : '来源');
    line.textContent=[name,labels[result.status] || result.status || '等待结果',
      result.total!=null ? `${result.total} 个频道` : '',
      result.attempts!=null ? `尝试 ${result.attempts} 次` : '',result.error || ''].filter(Boolean).join(' · ');
    if(Array.isArray(result.feeds)){
      const list=document.createElement('div');list.className='task-feed-results';
      list.append(...result.feeds.map(resultLine));line.append(list);
    }
    return line;
  };
  const schedule=delay=>{clearTimeout(timer);if(!finished && !document.hidden)timer=setTimeout(poll,delay);};
  const poll=async()=>{
    if(loading || finished || document.hidden)return;
    loading=true;
    const controller=new AbortController();const timeout=setTimeout(()=>controller.abort(),15000);
    try{
      const response=await fetch(location.pathname+'?format=json',{cache:'no-store',signal:controller.signal});
      if(response.redirected || response.status===403){summary.textContent='登录已过期或没有查看权限，请重新登录后刷新。';finished=true;return;}
      if(response.status===404){
        summary.textContent=document.querySelector('[data-task-kind]')?.dataset.taskKind==='restore'
          ?'恢复后历史任务可能已替换。请重新登录并前往维护中心查看。':'任务已删除，请返回任务中心。';
        finished=true;return;
      }
      if(!response.ok)throw new Error('进度读取失败，稍后自动重试。');
      const task=await response.json();
      if(!Array.isArray(task.details))throw new Error('任务结果格式异常，请刷新页面。');
      const badge=document.getElementById('task-status');badge.textContent=task.label;badge.className='badge '+task.status;
      summary.textContent=task.summary || '等待工作进程执行';
      const log=document.getElementById('task-log');
      const atBottom=log.scrollHeight-log.scrollTop-log.clientHeight<32;
      if(log.textContent!==task.log){log.textContent=task.log;if(atBottom)log.scrollTop=log.scrollHeight;}
      document.getElementById('source-results').replaceChildren(...task.details.map(resultLine));
      finished=!['queued','running'].includes(task.status);
      const cancel=document.getElementById('cancel-task');if(cancel)cancel.hidden=finished;
      schedule(2000);
    }catch(error){summary.textContent=error.name==='AbortError'?'进度读取超时，稍后自动重试。':error.message;schedule(5000);}
    finally{clearTimeout(timeout);loading=false;}
  };
  document.addEventListener('visibilitychange',()=>{if(document.hidden)clearTimeout(timer);else poll();});
  poll();
})();
