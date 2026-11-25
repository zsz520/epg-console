document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('.catalog-picker').forEach(root => {
    const selected = root.querySelector('.catalog-selected');
    const notice = root.querySelector('.catalog-notice');
    const idField = document.getElementById('id_channel_id');
    const primary = document.getElementById('id_source');
    const names = new Map([...root.querySelector('.catalog-source').options].map(o => [o.value,o.text]));
    const metadata = new Map();
    const autoValues = {};
    const query = root.querySelector('.catalog-query');
    const matchName = document.getElementById('id_tvg_name');
    let previousName = matchName?.value || '';
    if (!query.value && previousName) query.value = previousName;
    matchName?.addEventListener('input', () => {
      if (!query.value || query.value === previousName) query.value = matchName.value;
      previousName = matchName.value;
    });
    const M = window.EpgSourceMapping;
    const parse = M.parse;
    const feeds = JSON.parse(root.dataset.xmltvFeeds || '{}');
    const sourceName = (source, identifier) => source === 'xmltv'
      ? `XMLTV · ${feeds[identifier.split(':',1)[0]] || '文件 ' + identifier.split(':',1)[0]}`
      : names.get(source) || source;
    for (const chip of selected.children) {
      metadata.set(chip.dataset.source + ':' + chip.dataset.identifier, {
        id:chip.dataset.id, name:chip.dataset.name || matchName?.value || '',
        source_name:sourceName(chip.dataset.source,chip.dataset.identifier)
      });
    }
    let current = new Map(), valid = true, writing = false;
    const mapping = () => {
      if (!valid) throw new Error('请先修正来源 ID 格式，再选择频道。');
      return new Map(current);
    };
    const render = () => {
      selected.replaceChildren();
      for (const [key, identifier] of current) {
        const source = M.source(key);
        const row = metadata.get(source + ':' + identifier);
        const chip = document.createElement('span'); chip.className = 'catalog-chip';
        Object.assign(chip.dataset,{source,identifier,key});
        if (row?.id) {
          chip.dataset.id = row.id;
          const input = document.createElement('input'); input.type='hidden'; input.name=root.dataset.field; input.value=row.id;
          chip.append(input);
        }
        const handle = document.createElement('button'); handle.type='button'; handle.className='source-drag-handle';
        handle.textContent='⠿'; handle.setAttribute('aria-label',`拖动来源：${sourceName(source,identifier)}`);
        handle.title='拖动排序，也可用左右方向键调整';
        const label = document.createElement('span'); label.className='source-chip-label';
        label.textContent=`${row?.name || matchName?.value || '自定义频道'} · ${row?.source_name || sourceName(source,identifier)} · ${identifier}`;
        const remove=document.createElement('button'); remove.type='button'; remove.className='source-remove';
        remove.textContent='×'; remove.setAttribute('aria-label',`移除来源：${sourceName(source,identifier)}`);
        chip.append(handle,label,remove); selected.append(chip);
      }
    };
    const sync = (next, promote=false) => {
      if (promote) next = M.promote(next,primary.value);
      const text=M.text(next);
      if (text.length>300) throw new Error('来源 ID 超过 300 字符，请减少来源。');
      current=next; valid=true;
      const first=next.keys().next().value || '';
      const primaryChanged=primary.value!==first;
      primary.replaceChildren();
      const automatic=document.createElement('option'); automatic.value=''; automatic.textContent='自动选择第一个来源';
      primary.append(automatic);
      for (const [key,identifier] of next) {
        const option=document.createElement('option');option.value=key;option.textContent=sourceName(M.source(key),identifier);
        primary.append(option);
      }
      primary.value=first;
      render();
      writing=true;
      idField.value=text;
      idField.dispatchEvent(new Event('input',{bubbles:true}));
      if (primaryChanged) primary.dispatchEvent(new Event('change',{bubbles:true}));
      writing=false;
      notice.textContent=next.size ? `共 ${next.size} 个来源，按从左到右顺序抓取；可拖动排序，第一项为节目来源。保存频道后生效。` : '';
      root.dispatchEvent(new CustomEvent('catalog-mapping-change'));
    };
    const reconcile = () => {
      try { sync(parse(idField.value),true); }
      catch(error) { valid=false; selected.replaceChildren(); notice.textContent=error.message; }
    };
    idField.addEventListener('input',()=>{if(!writing) reconcile();});
    primary.addEventListener('change',()=>{
      if (writing) return;
      try {
        if (primary.value && !current.has(primary.value)) {
          notice.textContent='该来源尚无频道 ID，请从目录选择或添加自定义 ID。'; return;
        }
        sync(mapping(),true);
      } catch(error) {notice.textContent=error.message;}
    });
    selected.addEventListener('click',event=>{
      const remove=event.target.closest('.source-remove'); if(!remove) return;
      try {const next=mapping();next.delete(remove.closest('.catalog-chip').dataset.key);sync(next);}
      catch(error) {notice.textContent=error.message;}
    });
    const reorder = (sources, focusSource) => {
      try {
        sync(new Map(sources.map(key=>[key,current.get(key)])));
        [...selected.querySelectorAll('.catalog-chip')].find(c=>c.dataset.key===focusSource)?.querySelector('.source-drag-handle').focus();
      } catch(error) {notice.textContent=error.message;}
    };
    let dragging=null;
    selected.addEventListener('pointerdown',event=>{
      const handle=event.target.closest('.source-drag-handle');
      if(!handle || event.button!==0) return;
      event.preventDefault();
      dragging=handle.closest('.catalog-chip');dragging.classList.add('source-dragging');
      selected.setPointerCapture(event.pointerId);
    });
    selected.addEventListener('pointermove',event=>{
      if(!dragging) return;
      const target=document.elementFromPoint(event.clientX,event.clientY)?.closest('.catalog-chip');
      if(target && target!==dragging && target.parentElement===selected) {
        const chips=[...selected.children];
        selected.insertBefore(dragging,chips.indexOf(dragging)<chips.indexOf(target)?target.nextSibling:target);
        current=new Map([...selected.children].map(c=>[c.dataset.key,c.dataset.identifier]));
        writing=true;
        idField.value=M.text(current);
        idField.dispatchEvent(new Event('input',{bubbles:true}));
        primary.value=current.keys().next().value || '';
        writing=false;
        notice.textContent='已调整抓取顺序，松开后请保存频道。';
      }
    });
    const finish=()=>{
      if(!dragging) return;
      const source=dragging.dataset.key;dragging=null;reorder([...current.keys()],source);
    };
    selected.addEventListener('pointerup',finish);
    selected.addEventListener('pointercancel',finish);
    selected.addEventListener('lostpointercapture',finish);
    selected.addEventListener('keydown',event=>{
      const handle=event.target.closest('.source-drag-handle');
      if(!handle || !['ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(event.key)) return;
      event.preventDefault();
      const source=handle.closest('.catalog-chip').dataset.key;
      const keys=[...current.keys()],index=keys.indexOf(source);
      const next=index+(['ArrowLeft','ArrowUp'].includes(event.key)?-1:1);
      if(next>=0 && next<keys.length){[keys[index],keys[next]]=[keys[next],keys[index]];reorder(keys,source);}
    });
    const choose = (row, confirmed=false) => {
      try {
        const next=mapping(), key=M.key(row.source,row.identifier), old=next.get(key);
        if(old && old!==row.identifier && !confirmed) {
          notice.replaceChildren(document.createTextNode(`${row.source_name} 已选 ID：${old}；新 ID：${row.identifier}。同一脚本来源或同一 XMLTV 文件只能保留一个频道 ID。 `));
          const replace=document.createElement('button');replace.type='button';replace.className='secondary';replace.textContent='替换此来源';
          replace.addEventListener('click',()=>choose(row,true));
          const keep=document.createElement('button');keep.type='button';keep.className='secondary';keep.textContent='保留原选择';
          keep.addEventListener('click',()=>sync(mapping()));notice.append(replace,keep);return;
        }
        const fillDetails=!next.size;
        next.set(key,row.identifier);
        M.text(next);
        metadata.set(row.source+':'+row.identifier,row);
        sync(next,true);
        if(fillDetails) {
          for(const [field,value] of Object.entries({name:row.name,tvg_name:row.name,sort:row.category || '其他'})) {
            const element=document.getElementById('id_'+field);
            if(element && (!element.value || element.value===autoValues[field])){element.value=value;autoValues[field]=value;element.dispatchEvent(new Event('input',{bubbles:true}));}
          }
          render();
        }
        root.dispatchEvent(new CustomEvent('catalog-source-selected',{detail:row}));
      } catch(error) {notice.textContent=error.message;}
    };
    reconcile();
    let searchController;
    const search = async () => {
      searchController?.abort();
      const controller = new AbortController(); searchController = controller;
      const results = root.querySelector('.catalog-results');
      results.textContent = '正在查询…';
      const params = new URLSearchParams({q: root.querySelector('.catalog-query').value, source: root.querySelector('.catalog-source').value});
      try {
        const response = await fetch(root.dataset.searchUrl + '?' + params, {signal: controller.signal});
        if (!response.ok) throw new Error('查询失败，请检查登录状态与目录权限。');
        const data = await response.json(); results.replaceChildren();
        if (!data.results.length) results.textContent = '没有匹配项，请先抓取频道目录。';
        for (const row of data.results) {
          const wrapper = document.createElement('div'); wrapper.className = 'catalog-result-row';
          const button = document.createElement('button'); button.type = 'button'; button.className = 'catalog-result';
          button.textContent = `${row.name} · ${row.source_name} · ${row.identifier}`;
          button.addEventListener('click', () => choose(row));
          const preview = document.createElement('button'); preview.type = 'button'; preview.className = 'secondary'; preview.textContent = '节目预览';
          preview.setAttribute('aria-label', `预览 ${row.name} / ${row.source_name}`);
          preview.addEventListener('click', () => root.dispatchEvent(new CustomEvent('catalog-preview-request', {detail: row})));
          wrapper.append(button, preview); results.append(wrapper);
        }
      } catch (error) { if (error.name !== 'AbortError' && searchController === controller) results.textContent = error.message; }
    };
    root.querySelector('.catalog-search').addEventListener('click', search);
    root.addEventListener('catalog-select-request', event => choose(event.detail));
    root.querySelector('.catalog-query').addEventListener('keydown', event => {
      if (event.key === 'Enter') { event.preventDefault(); search(); }
    });
  });
});

