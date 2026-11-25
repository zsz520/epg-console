document.addEventListener('DOMContentLoaded', () => {
  document.querySelectorAll('.operation-task-form').forEach(form => {
    const picker = form.querySelector('.task-source-picker');
    if (!picker) return;
    const kind = form.querySelector('.task-kind');
    const order = form.querySelector('.task-source-order');
    const options = [...picker.querySelectorAll('.task-source-option')];
    const boxes = options.map(option => option.querySelector('input'));
    const labels = new Map(boxes.map(box => [box.value, box.dataset.label]));
    const summary = picker.querySelector('.task-source-summary');
    const selected = picker.querySelector('.task-source-selected');
    const notice = picker.querySelector('.task-source-notice');
    const search = picker.querySelector('.task-source-search');
    let current = new Map(boxes.filter(box => box.checked).map(box => [box.value, labels.get(box.value)]));
    try {
      const saved = JSON.parse(order.value);
      if (Array.isArray(saved) && saved.length === current.size && new Set(saved).size === saved.length && saved.every(key => current.has(key)))
        current = new Map(saved.map(key => [key, labels.get(key)]));
    } catch { /* A fresh form has no saved order. */ }
    const saveOrder = () => { order.value = JSON.stringify([...current.keys()]); };
    const render = focus => {
      boxes.forEach(box => { box.checked = current.has(box.value); });
      selected.replaceChildren();
      for (const [source, label] of current) {
        const chip = document.createElement('span');
        chip.className = 'task-source-chip'; chip.dataset.source = source;
        const handle = document.createElement('button');
        handle.type = 'button'; handle.className = 'task-source-handle'; handle.textContent = '⠿';
        handle.title = '拖动排序，也可用方向键调整'; handle.setAttribute('aria-label', `调整 ${label} 的顺序`);
        const text = document.createElement('span'); text.textContent = label;
        const remove = document.createElement('button');
        remove.type = 'button'; remove.className = 'task-source-remove'; remove.textContent = '×';
        remove.setAttribute('aria-label', `取消选择 ${label}`);
        chip.append(handle, text, remove); selected.append(chip);
      }
      summary.textContent = current.size ? `已选 ${current.size} 个来源 · 点击选择` : '全部来源 · 点击多选';
      notice.textContent = current.size
        ? '仅抓取所选来源，按标签从左到右执行；可拖动手柄或用方向键排序。'
        : '未选择时抓取全部来源，按默认顺序执行；选中来源后可拖动调整顺序。';
      saveOrder();
      if (focus) [...selected.children].find(chip => chip.dataset.source === focus)?.querySelector('.task-source-handle').focus();
    };
    const filter = () => {
      const query = search.value.trim().toLocaleLowerCase();
      let count = 0;
      options.forEach(option => {
        option.hidden = !option.textContent.toLocaleLowerCase().includes(query);
        if (!option.hidden) count++;
      });
      picker.querySelector('.task-source-empty').hidden = count > 0;
    };
    boxes.forEach(box => box.addEventListener('change', () => {
      if (box.checked) current.set(box.value, labels.get(box.value));
      else current.delete(box.value);
      render();
    }));
    picker.querySelector('.task-source-all').addEventListener('click', () => {
      current = new Map([...current, ...boxes.filter(box => !current.has(box.value)).map(box => [box.value, labels.get(box.value)])]);
      render();
    });
    picker.querySelector('.task-source-reset').addEventListener('click', () => {
      current.clear(); search.value = ''; filter(); render();
    });
    search.addEventListener('input', filter);
    search.addEventListener('keydown', event => { if (event.key === 'Enter') event.preventDefault(); });
    selected.addEventListener('click', event => {
      const remove = event.target.closest('.task-source-remove');
      if (remove) { current.delete(remove.closest('.task-source-chip').dataset.source); render(); }
    });
    selected.addEventListener('keydown', event => {
      const handle = event.target.closest('.task-source-handle');
      if (!handle || !['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'].includes(event.key)) return;
      event.preventDefault();
      const source = handle.closest('.task-source-chip').dataset.source;
      const keys = [...current.keys()], index = keys.indexOf(source);
      const next = index + (['ArrowLeft', 'ArrowUp'].includes(event.key) ? -1 : 1);
      if (next < 0 || next >= keys.length) return;
      [keys[index], keys[next]] = [keys[next], keys[index]];
      current = new Map(keys.map(key => [key, labels.get(key)])); render(source);
    });
    let dragging = null;
    selected.addEventListener('pointerdown', event => {
      const handle = event.target.closest('.task-source-handle');
      if (!handle || event.button !== 0) return;
      event.preventDefault(); dragging = handle.closest('.task-source-chip');
      dragging.classList.add('dragging'); selected.setPointerCapture(event.pointerId);
    });
    selected.addEventListener('pointermove', event => {
      if (!dragging) return;
      const target = document.elementFromPoint(event.clientX, event.clientY)?.closest('.task-source-chip');
      if (!target || target === dragging || target.parentElement !== selected) return;
      const chips = [...selected.children];
      selected.insertBefore(dragging, chips.indexOf(dragging) < chips.indexOf(target) ? target.nextSibling : target);
      current = new Map([...selected.children].map(chip => [chip.dataset.source, labels.get(chip.dataset.source)]));
      saveOrder();
    });
    const finish = () => { if (dragging) { const source = dragging.dataset.source; dragging = null; render(source); } };
    for (const event of ['pointerup', 'pointercancel', 'lostpointercapture']) selected.addEventListener(event, finish);
    const updateKind = () => {
      const active = kind.value === 'catalog';
      picker.hidden = !active; order.disabled = !active;
      boxes.forEach(box => { box.disabled = !active; });
    };
    kind.addEventListener('change', updateKind);
    form.addEventListener('submit', saveOrder);
    render(); filter(); updateKind();
  });
});
