(() => {
  const poll = async () => {
    try {
      const response = await fetch(location.pathname + '?format=json');
      if (response.redirected || response.status === 403) {
        document.getElementById('task-summary').textContent = '登录已过期或没有查看权限，请重新登录后刷新。';
        return;
      }
      if(response.status===404 && document.querySelector('[data-task-kind]')?.dataset.taskKind==='restore'){
        document.getElementById('task-summary').textContent='恢复后历史任务可能已替换。请重新登录并前往维护中心查看。';return;
      }
      if (!response.ok) throw new Error('进度读取失败，请刷新页面。');
      const task = await response.json();
      const badge = document.getElementById('task-status'); badge.textContent = task.label; badge.className = 'badge ' + task.status;
      document.getElementById('task-summary').textContent = task.summary || '等待工作进程执行';
      document.getElementById('task-log').textContent = task.log;
      document.getElementById('source-results').replaceChildren(...task.details.map(result => {
        const line = document.createElement('p');
        if(result.backup){line.textContent=`完整备份：${result.backup}，可到维护中心下载。`;return line;}
        line.textContent = `${result.source} · ${result.status === 'success' ? '成功' : '失败'} · ${result.total ?? ''} ${result.error ?? ''}`;
        return line;
      }));
      const active = ['queued', 'running'].includes(task.status);
      const cancel = document.getElementById('cancel-task');
      if (cancel) cancel.hidden = !active;
      if (active) setTimeout(poll, 2000);
    } catch (error) { document.getElementById('task-summary').textContent = error.message; setTimeout(poll, 5000); }
  };
  poll();
})();
