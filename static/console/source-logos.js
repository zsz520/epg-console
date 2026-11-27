document.addEventListener('DOMContentLoaded',()=>{
  document.querySelectorAll('.catalog-picker').forEach(root=>{
    const input=root.closest('form')?.querySelector('[name=logo]');
    if(!input)return;
    const select=document.createElement('select');
    select.className='source-logo-select';
    select.setAttribute('aria-label','选择已配置来源的台标地址');
    const tools=document.createElement('div');tools.className='source-logo-tools';
    const preview=document.createElement('span');preview.className='source-logo-preview';
    tools.append(select,preview);input.insertAdjacentElement('afterend',tools);
    let shown=null,currentImage=null;
    const updatePreview=()=>{
      const url=input.value.trim();
      if(url===shown)return;
      shown=url;currentImage=null;preview.replaceChildren();
      if(!url || !/^(https?:)?\/\//i.test(url)){
        preview.textContent=url?'请输入 http(s) 台标地址':'未设置台标';return;
      }
      preview.textContent='';
      const link=document.createElement('a'),image=document.createElement('img'),status=document.createElement('span');
      link.href=url;link.target='_blank';link.rel='noopener noreferrer';link.title='查看原始台标';
      image.alt='频道台标预览';image.referrerPolicy='no-referrer';currentImage=image;
      status.textContent='正在加载台标…';
      image.addEventListener('load',()=>{if(currentImage===image)status.textContent='';});
      image.addEventListener('error',()=>{if(currentImage===image){link.hidden=true;status.textContent='台标加载失败';}});
      link.append(image);preview.append(link,status);image.src=url;
    };
    const syncValue=()=>{select.value=[...select.options].some(o=>o.value===input.value)?input.value:'';};
    root.addEventListener('catalog-logo-options',event=>{
      const urls=new Map();
      for(const row of event.detail || []){
        const url=(row.logo || '').trim();
        if(!/^(https?:)?\/\//i.test(url))continue;
        if(!urls.has(url))urls.set(url,[]);
        if(!urls.get(url).includes(row.label))urls.get(url).push(row.label);
      }
      select.replaceChildren();
      const placeholder=document.createElement('option');
      placeholder.value='';placeholder.textContent=urls.size?'从已选来源选择台标（也可手动输入）':'已选来源暂无台标，可手动输入地址';
      select.append(placeholder);
      for(const [url,labels] of urls){
        const option=document.createElement('option');option.value=url;
        option.textContent=labels.join(' / ')+' · '+url;select.append(option);
      }
      select.disabled=urls.size===0;
      syncValue();
      updatePreview();
    });
    select.addEventListener('change',()=>{
      if(!select.value)return;
      input.value=select.value;
      input.dispatchEvent(new Event('input',{bubbles:true}));
      input.dispatchEvent(new Event('change',{bubbles:true}));
    });
    input.addEventListener('input',()=>{syncValue();updatePreview();});
    input.addEventListener('change',()=>{syncValue();updatePreview();});
    updatePreview();
  });
});
