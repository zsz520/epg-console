/* Run before the page is painted; one preference for public/admin/iframe pages. */
(() => {
  const key='epg-theme',root=document.documentElement;
  const valid=value=>['auto','light','dark'].includes(value);
  const system=window.matchMedia('(prefers-color-scheme: dark)');
  const read=()=>{
    try{const saved=localStorage.getItem(key) || localStorage.getItem('theme');if(valid(saved))return saved;}catch{}
    try{if(window.parent!==window){const parentMode=window.parent.document.documentElement.dataset.themeMode;if(valid(parentMode))return parentMode;}}catch{}
    return 'auto';
  };
  let mode=read();
  const paint=()=>{
    const dark=mode==='dark' || (mode==='auto' && system.matches);
    root.dataset.theme=dark?'dark':'light';root.dataset.themeMode=mode;
    document.querySelectorAll('[data-theme-toggle]').forEach(button=>{
      const label=dark?'切换到浅色模式':'切换到暗黑模式';
      button.setAttribute('aria-label',label);button.setAttribute('aria-pressed',String(dark));
      button.title=label+(mode==='auto'?'（当前跟随系统）':'');
    });
    document.querySelectorAll('[data-theme-auto]').forEach(button=>{
      button.setAttribute('aria-pressed',String(mode==='auto'));
      button.title=mode==='auto'?'正在跟随系统配色':'跟随系统配色';
    });
    // Storage events synchronize other tabs; this also supports blocked storage in iframes.
    for(let index=0;index<window.frames.length;index++){
      try{window.frames[index].postMessage({type:'epg-theme',mode},window.location.origin);}catch{}
    }
  };
  const setMode=value=>{
    if(!valid(value))return;
    mode=value;
    try{localStorage.setItem(key,mode);}catch{}
    paint();
  };
  paint();
  document.addEventListener('DOMContentLoaded',paint);
  document.addEventListener('click',event=>{
    if(event.target.closest('[data-theme-toggle]'))setMode(root.dataset.theme==='dark'?'light':'dark');
    else if(event.target.closest('[data-theme-auto]'))setMode('auto');
  });
  system.addEventListener('change',()=>{if(mode==='auto')paint();});
  window.addEventListener('storage',event=>{if(event.key===key || event.key===null){mode=read();paint();}});
  window.addEventListener('message',event=>{
    if(window.parent!==window && event.source===window.parent && event.origin===window.location.origin && event.data?.type==='epg-theme' && valid(event.data.mode)){
      mode=event.data.mode;paint();
    }
  });
})();
