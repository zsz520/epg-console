document.addEventListener('DOMContentLoaded',()=>{
  const search=document.querySelector('.settings-search'),advanced=document.querySelector('.settings-advanced');
  if(!search || !advanced)return;
  const sections=[...document.querySelectorAll('.settings-center-form > section')];
  if(document.querySelector('.setting-field[data-error="true"]'))advanced.checked=true;
  const update=()=>{
    const query=search.value.trim().toLocaleLowerCase();let visible=0;
    sections.forEach(section=>{
      let count=0;
      section.querySelectorAll('.setting-field').forEach(field=>{
        const matches=!query || (field.textContent+' '+field.dataset.setting).toLocaleLowerCase().includes(query);
        const show=field.dataset.error==='true' || matches && (query || advanced.checked || field.dataset.advanced!=='true') && (query || field.dataset.scheduleHidden!=='true');
        field.hidden=!show;if(show){count++;visible++;}
      });
      section.hidden=count===0;
    });
    document.querySelector('.settings-search-status').textContent=`显示 ${visible} 项设置`;
  };
  search.addEventListener('input',update);
  search.addEventListener('keydown',event=>{if(event.key==='Enter')event.preventDefault();});
  advanced.addEventListener('change',update);
  document.addEventListener('settings:visibility',update);
  document.querySelector('.settings-clear').addEventListener('click',()=>{search.value='';update();search.focus();});
  update();
  const form=document.querySelector('.settings-center-form');
  if(form){
    const snapshot=()=>JSON.stringify([...new FormData(form).entries()]);
    const initial=snapshot();let submitting=false;
    const state=document.createElement('span');state.setAttribute('role','status');state.className='settings-save-state';
    const submit=form.querySelector('button[type="submit"],input[type="submit"]');
    submit?.parentElement.append(state);
    const changed=()=>{state.textContent=snapshot()!==initial?'有未保存的修改':'';};
    form.addEventListener('input',changed);form.addEventListener('change',changed);
    window.addEventListener('beforeunload',event=>{if(!submitting && snapshot()!==initial){event.preventDefault();event.returnValue='';}});
    form.addEventListener('submit',event=>{
      if(submitting){event.preventDefault();return;}
      submitting=true;state.textContent='正在保存…';submit?.setAttribute('aria-busy','true');
    });
    window.addEventListener('pageshow',()=>{submitting=false;submit?.removeAttribute('aria-busy');changed();});
  }
});
