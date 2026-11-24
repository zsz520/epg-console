(() => {
  const top=document.querySelector('.back-to-top');
  const bottom=document.querySelector('.back-to-bottom');
  if(!top || !bottom) return;
  const update=()=>{
    const y=window.scrollY || document.documentElement.scrollTop;
    const remaining=document.documentElement.scrollHeight-window.innerHeight-y;
    top.hidden=y<280;
    bottom.hidden=remaining<80;
  };
  window.addEventListener('scroll',update,{passive:true});
  window.addEventListener('resize',update);
  const move=end=>window.scrollTo({top:end?document.documentElement.scrollHeight:0,behavior:window.matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth'});
  top.addEventListener('click',()=>move(false));
  bottom.addEventListener('click',()=>move(true));
  new ResizeObserver(update).observe(document.body);
  update();
})();
