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
        const show=field.dataset.error==='true' || matches && (query || advanced.checked || field.dataset.advanced!=='true');
        field.hidden=!show;if(show){count++;visible++;}
      });
      section.hidden=count===0;
    });
    document.querySelector('.settings-search-status').textContent=`显示 ${visible} 项设置`;
  };
  search.addEventListener('input',update);
  search.addEventListener('keydown',event=>{if(event.key==='Enter')event.preventDefault();});
  advanced.addEventListener('change',update);
  document.querySelector('.settings-clear').addEventListener('click',()=>{search.value='';update();search.focus();});
  update();
});
