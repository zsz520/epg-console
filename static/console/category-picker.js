document.addEventListener('DOMContentLoaded',()=>{
  document.querySelectorAll('.category-picker').forEach(root=>{
    const search=root.querySelector('.category-search');
    const rows=[...root.querySelectorAll('.category-option')];
    const boxes=rows.map(row=>row.querySelector('input[type=checkbox]'));
    const count=root.querySelector('.category-count');
    const select=root.querySelector('.category-select-visible');
    const clear=root.querySelector('.category-clear');
    const update=()=>{
      let visible=0;
      const query=search.value.trim().toLocaleLowerCase();
      rows.forEach(row=>{
        row.hidden=!row.textContent.toLocaleLowerCase().includes(query);
        if(!row.hidden) visible++;
        row.classList.toggle('is-selected',row.querySelector('input').checked);
      });
      const selected=boxes.filter(box=>box.checked).length;
      count.textContent=`已选 ${selected} / ${boxes.length}`;
      root.querySelector('.category-empty').hidden=visible>0;
      select.disabled=!rows.some(row=>!row.hidden && !row.querySelector('input').disabled && !row.querySelector('input').checked);
      clear.disabled=!boxes.some(box=>box.checked && !box.disabled);
    };
    search.addEventListener('input',update);
    search.addEventListener('keydown',event=>{if(event.key==='Enter') event.preventDefault();});
    root.addEventListener('change',update);
    const set=(box,checked)=>{
      if(box.disabled || box.checked===checked) return;
      box.checked=checked;box.dispatchEvent(new Event('change',{bubbles:true}));
    };
    select.addEventListener('click',()=>{
      rows.filter(row=>!row.hidden).forEach(row=>set(row.querySelector('input'),true));update();
    });
    clear.addEventListener('click',()=>{boxes.forEach(box=>set(box,false));update();});
    root.closest('form')?.addEventListener('reset',()=>setTimeout(update,0));
    update();
  });
});
