document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('.catalog-picker').forEach(root => {
    const form = root.closest('form');
    const field = document.getElementById('id_channel_id');
    const primary = document.getElementById('id_source');
    const cards = root.querySelector('.source-preview-cards');
    const notice = root.querySelector('.comparison-notice');
    const names = new Map([...root.querySelector('.catalog-source').options].map(o => [o.value,o.text]));
    const M = window.EpgSourceMapping;
    const parse = text => {try {return M.parse(text);} catch {return new Map();}};
    const original = parse(root.dataset.originalMapping || '');
    const elements = new Map();
    const customTargets = new Map();
    const dateField=root.querySelector('.preview-date');
    let chosenDate=dateField.value, generation=0;
    const compared=new Map();
    let active = 0;
    const inFlight = new Set();
    const waiting = [];
    const el = (tag, text, className) => {
      const node = document.createElement(tag); if (text != null) node.textContent = text;
      if (className) node.className = className; return node;
    };
    const button = (text, action) => {
      const node = el('button', text, 'secondary'); node.type = 'button'; node.addEventListener('click', action); return node;
    };
    const targetFor = (source, identifier) => {
      if (!identifier) return null;
      if (customTargets.has(source + ':' + identifier)) return customTargets.get(source + ':' + identifier);
      const chip = [...root.querySelectorAll('.catalog-chip')].find(c => c.dataset.source === source && c.dataset.identifier === identifier);
      if (chip?.dataset.id) return {source, identifier, source_name:names.get(source) || source, catalog_id:chip.dataset.id};
      if (root.dataset.channelPk && M.has(original,source,identifier))
        return {source, identifier, source_name:names.get(source) || source, channel_id:root.dataset.channelPk};
      if (names.has(source)) return {source,identifier,source_name:names.get(source),custom_id:identifier};
      return null;
    };
    const use = (target, confirmed=false) => {
      try {
        if (target.custom_id) {
          const mapping = M.parse(field.value), key = M.key(target.source,target.identifier);
          if (mapping.has(key) && mapping.get(key) !== target.identifier && !confirmed) {
            notice.replaceChildren(el('span','此来源已有不同 ID，请确认是否替换。'),button('替换并使用此 ID',()=>use(target,true)),button('保留原 ID',()=>{notice.textContent='已保留原 ID。';}));
            return;
          }
          mapping.set(key,target.identifier);
          const text=M.text(mapping);
          field.value=text;
          field.dispatchEvent(new Event('input',{bubbles:true}));
        }
        if (target.row) root.dispatchEvent(new CustomEvent('catalog-select-request', {detail:target.row}));
        if (!M.has(parse(field.value),target.source,target.identifier)) {
          notice.textContent = '请先确认同一来源的 ID 替换，或在来源目录中选择此频道。'; return;
        }
        field.value = M.text(M.promote(M.parse(field.value),target.source,target.identifier));
        field.dispatchEvent(new Event('input',{bubbles:true}));
        primary.value = M.key(target.source,target.identifier);
        primary.dispatchEvent(new Event('change',{bubbles:true}));
        notice.textContent = `已选择 ${target.source_name} 为主来源，请保存频道。`;
      } catch (error) {notice.textContent = error.message;}
    };
    const clock = value => value ? new Date(value).toLocaleTimeString('zh-CN', {timeZone:'Asia/Shanghai',hour:'2-digit',minute:'2-digit',hour12:false}) : '未提供';
    const paint = (card, target, data) => {
      card.replaceChildren();
      const heading = el('div', null, 'source-preview-heading');
      heading.append(el('strong', data.origin_name ? 'XMLTV · ' + data.origin_name : target.source_name), button('重新抓取', () => schedule(target,true)));
      card.append(heading, el('code', target.identifier, 'source-preview-id'));
      const fetched = data.fetched_at ? new Date(data.fetched_at).toLocaleTimeString('zh-CN',{hour12:false,timeZone:'Asia/Shanghai'}) : '';
      card.append(el('p', `${data.date || ''} · ${data.count || 0} 条 · ${data.cached ? '缓存' : (data.origin_updated_at ? '读取文件快照' : '实时抓取')} ${fetched}`, 'preview-meta'));
      if (data.real_count != null) card.append(el('p', `真实节目 ${data.real_count} 条 · 缺失补全 ${data.filled_count || 0} 条`, 'preview-meta'));
      if (data.origin_updated_at) card.append(el('p', `文件快照更新于 ${new Date(data.origin_updated_at).toLocaleString('zh-CN',{timeZone:'Asia/Shanghai',hour12:false})}；刷新远端文件请到 XMLTV 来源执行更新。${data.origin_stale ? '最近更新失败，当前使用上次成功快照。' : ''}`, 'preview-meta'));
      if (data.warning) card.append(el('p',data.warning,'preview-error'));
      card.append(button('用此来源', () => use(target)));
      if (data.status !== 'success') {
        card.append(el('p', data.message || '该来源暂未返回节目。', 'preview-error')); return;
      }
      const list = el('ol', null, 'preview-programmes');
      for (const programme of data.programmes) {
        const item = el('li');
        if (programme.end && Date.now() >= Date.parse(programme.start) && Date.now() < Date.parse(programme.end)) item.className = 'on-air';
        const nextDay = programme.end && programme.end.slice(0,10) !== programme.start.slice(0,10) ? '次日 ' : '';
        const inferred = programme.gap_filler ? '（缺失补全）' : (programme.inferred_end ? '（时间补全）' : '');
        item.append(el('time', `${clock(programme.start)}–${nextDay}${clock(programme.end)}${inferred}`), el('span', programme.title));
        list.append(item);
      }
      card.append(list);
      if (data.truncated) card.append(el('small',`仅显示前 ${data.limit || 500} 条。`));
    };
    const pump = () => {
      while (active < 2 && waiting.length) {
        active++;
        const job = waiting.shift();
        run(job.target,job.force,job.date,job.generation).finally(() => { active--; pump(); });
      }
    };
    const run = async (target, force, date, version) => {
      const key = target.source + ':' + target.identifier + ':' + date + ':' + version;
      inFlight.add(key);
      let card = elements.get(key);
      if (!card) { card = el('article',null,'source-preview-card'); elements.set(key,card); cards.append(card); }
      card.replaceChildren(el('strong',target.source_name), el('p',`正在读取 ${date} 节目，最长等待约 ${root.dataset.previewTimeout || 15} 秒…`));
      try {
        const response = await fetch(root.dataset.previewUrl, {method:'POST',headers:{'Content-Type':'application/json',
          'X-CSRFToken':form.querySelector('[name=csrfmiddlewaretoken]').value},
          body:JSON.stringify({...target, row:undefined, force, date})});
        if (!response.ok) throw new Error(response.status === 403 ? '没有预览权限或登录已过期，请刷新页面。' : '节目预览失败，请重试。');
        const data=await response.json();
        if(version===generation) paint(card,target,data);
      } catch (error) { if(version===generation) paint(card,target,{status:'error', message:error.message,programmes:[]}); }
      finally { inFlight.delete(key); }
    };
    const schedule = (target, force=false) => {
      const key=target.source+':'+target.identifier+':'+chosenDate+':'+generation;
      compared.set(target.source+':'+target.identifier,target);
      if(inFlight.has(key)) return;
      if(!waiting.some(job=>job.target.source===target.source && job.target.identifier===target.identifier && job.date===chosenDate))
        waiting.push({target,force,date:chosenDate,generation});
      pump();
    };
    const updateDays=()=>{
      root.querySelector('.preview-day-prev').disabled=chosenDate<=dateField.min;
      root.querySelector('.preview-day-next').disabled=chosenDate>=dateField.max;
    };
    const changeDate=()=>{
      if(!dateField.value || !dateField.checkValidity()){
        notice.textContent='请选择今天前后 7 天内的日期。';dateField.value=chosenDate;return;
      }
      if(dateField.value===chosenDate) return;
      chosenDate=dateField.value;generation++;
      waiting.splice(0);elements.clear();cards.replaceChildren();updateDays();
      const targets=[...compared.values()];
      targets.forEach(target=>schedule(target));
      notice.textContent=targets.length ? `正在对比 ${chosenDate} 的 ${targets.length} 个来源。` : `已选择 ${chosenDate}，请选择来源或点击对比。`;
    };
    dateField.addEventListener('change',changeDate);
    const step=delta=>{
      const date=new Date(chosenDate+'T12:00:00Z');date.setUTCDate(date.getUTCDate()+delta);
      dateField.value=date.toISOString().slice(0,10);changeDate();
    };
    root.querySelector('.preview-day-prev').addEventListener('click',()=>step(-1));
    root.querySelector('.preview-day-next').addEventListener('click',()=>step(1));
    root.querySelector('.preview-day-today').addEventListener('click',()=>{dateField.value=root.dataset.previewDate;changeDate();});
    updateDays();
    root.querySelector('.compare-selected').addEventListener('click', () => {
      let count = 0; const missing = [];
      for (const [key,identifier] of parse(field.value)) {
        const source = M.source(key);
        const target = targetFor(source,identifier);
        if (target) { schedule(target); count++; } else missing.push(names.get(source) || source);
      }
      notice.textContent = count ? `正在对比 ${chosenDate} 的 ${count} 个来源（上海时间 UTC+8）。${missing.length ? '手动新增 ID 请先保存或从目录选择：'+missing.join('、') : ''}` : '请先从来源目录选择频道，或保存手动填写的来源 ID。';
    });
    root.querySelector('.custom-preview').addEventListener('click', () => {
      const source=root.querySelector('.custom-source').value;
      const identifier=root.querySelector('.custom-id').value.trim();
      if(!source || !identifier){notice.textContent='请选择自定义来源，并填写频道 ID。';return;}
      if(identifier.length>285 || /[<>\r\n]/.test(identifier)){notice.textContent='ID 格式无效：不要填写尖括号或换行。';return;}
      const target={source,identifier,source_name:names.get(source)||source,custom_id:identifier,name:document.getElementById('id_tvg_name').value || '自定义频道'};
      customTargets.set(source+':'+identifier,target);
      schedule(target,true);
    });
    root.addEventListener('catalog-preview-request', event => {
      const row = event.detail;
      schedule({...row,catalog_id:row.id,row});
    });
    root.addEventListener('catalog-source-selected', event => {
      const row = event.detail;
      schedule({...row,catalog_id:row.id,row});
    });
    primary.addEventListener('change', () => {
      const mapping=parse(field.value);
      const source=M.source(primary.value);
      const identifier = mapping.get(primary.value) || mapping.get(M.first(mapping,source));
      const target = targetFor(source,identifier);
      if (target) schedule(target);
      else notice.textContent = '请先从来源目录补充该来源的频道 ID，再预览或保存。';
    });
  });
});

