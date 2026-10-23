/* Application navigation only. All destinations are existing page shells. */
(() => {
  const errors = document.querySelector('[data-storage-errors]');
  if (errors) errors.focus();
  const desktop = window.matchMedia('(min-width: 901px)');
  const openers = new WeakMap();
  const getDrawer = () => document.getElementById('workspace-drawer');
  const getSearch = () => document.getElementById('workspace-search');
  const closeDialog = dialog => { if (dialog && dialog.open) dialog.close(); };
  function openDialog(dialog, opener) {
    if (!dialog) return;
    document.querySelectorAll('dialog[open]').forEach(other => { if (other !== dialog) closeDialog(other); });
    openers.set(dialog, opener || document.activeElement);
    dialog.showModal();
    document.documentElement.classList.add('workspace-menu-open');
    if (opener && opener.matches('[data-open-navigation],[data-open-search],[data-open-notifications],[data-toggle-help]')) opener.setAttribute('aria-expanded', 'true');
    const focus = dialog.querySelector(dialog.id === 'workspace-search' ? 'input' : dialog.id === 'workspace-notifications' ? '[data-close-notifications]' : dialog.id === 'workspace-help' ? '[data-close-help]' : dialog.id === 'workspace-access' ? '[data-close-access]' : '[data-close-navigation]');
    if (focus) focus.focus();
  }
  function syncSidebar() {
    const collapsed = document.documentElement.dataset.sidebar === 'collapsed';
    document.querySelectorAll('[data-toggle-sidebar]').forEach(button => {
      button.setAttribute('aria-expanded', String(!collapsed));
      button.setAttribute('aria-label', collapsed ? 'Развернуть боковое меню' : 'Свернуть боковое меню');
      button.title = button.getAttribute('aria-label');
    });
  }
  function syncHelp() {
    const visible = document.documentElement.dataset.help !== 'hidden';
    document.querySelectorAll('[data-help-preference]').forEach(control => { control.checked = visible; });
    if (!visible) closeDialog(document.getElementById('workspace-help'));
  }
  syncSidebar(); syncHelp();
  window.addEventListener('hashchange', () => requestAnimationFrame(() => { syncSidebar(); syncHelp(); }));
  document.addEventListener('change', event => {
    if (!event.target.matches('[data-help-preference]')) return;
    const hidden = !event.target.checked;
    document.documentElement.dataset.help = hidden ? 'hidden' : 'visible';
    try { localStorage.setItem('smartfridge-help', hidden ? 'hidden' : 'visible'); } catch (error) {}
    syncHelp();
  });
  const normalize = text => text.toLocaleLowerCase('ru-RU').replaceAll('ё','е');
  function renderSearch(query = '') {
    const data = document.getElementById('workspace-search-data');
    const container = document.getElementById('workspace-search-results');
    if (!data || !container) return;
    const tokens = normalize(query.trim()).split(/\s+/).filter(Boolean);
    const entries = JSON.parse(data.textContent);
    const matches = entries.filter(item => tokens.length ? tokens.every(token => normalize(item.name+' '+item.group+' '+item.keywords).includes(token)) : item.group === 'Раздел');
    container.replaceChildren();
    matches.slice(0,12).forEach(item => {
      const link = document.createElement('a'); link.href = item.url;
      const title = document.createElement('span'); title.textContent = item.name;
      const group = document.createElement('small'); group.textContent = item.group;
      if (item.locked) {
        link.dataset.locked = '';
        link.dataset.lockedTitle = item.name;
        link.dataset.lockedPlans = item.requirement.plans;
        link.dataset.lockedDetail = item.requirement.detail;
        link.setAttribute('aria-haspopup', 'dialog'); link.setAttribute('aria-controls', 'workspace-access');
        group.textContent = item.requirement.name;
      }
      link.append(title,group); container.append(link);
    });
    if (!matches.length) { const empty=document.createElement('p'); empty.textContent='Ничего не найдено. Попробуйте другое название.'; container.append(empty); }
    const count = new Intl.NumberFormat('ru-RU').format(matches.length);
    document.getElementById('workspace-search-summary').textContent = tokens.length ? `Найдено: ${count}${matches.length>12?' · показаны первые 12':''}` : 'Основные разделы';
  }
  function openSearch(trigger) {
    const dialog = getSearch(); if (!dialog) return;
    dialog.querySelector('input').value = ''; renderSearch(); openDialog(dialog,trigger);
  }
  document.querySelectorAll('[data-search-shortcut]').forEach(label => { label.textContent=/Mac|iPhone|iPad/.test(navigator.platform)?'⌘ K':'Ctrl K'; });
  document.addEventListener('input', event => { if (event.target.id === 'workspace-search-input') renderSearch(event.target.value); });
  document.addEventListener('click', event => {
    const drawer = getDrawer(), search = getSearch();
    const locked = event.target.closest('[data-locked]');
    if (locked) {
      if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      event.preventDefault();
      const dialog = document.getElementById('workspace-access');
      if (!dialog) return;
      dialog.querySelector('#access-dialog-title').textContent = locked.dataset.lockedTitle;
      dialog.querySelector('#access-dialog-detail').textContent = locked.dataset.lockedDetail;
      dialog.querySelector('#access-dialog-plans').textContent = locked.dataset.lockedPlans;
      openDialog(dialog, locked); return;
    }
    if (event.target.closest('[data-close-access]')) { closeDialog(document.getElementById('workspace-access')); return; }
    const helpToggle = event.target.closest('[data-toggle-help]');
    if (helpToggle) {
      const copy = document.getElementById(helpToggle.dataset.helpSource);
      const dialog = document.getElementById('workspace-help');
      if (!copy || !dialog) return;
      dialog.querySelector('#help-dialog-title').textContent = helpToggle.dataset.helpTitle;
      const content = dialog.querySelector('#help-dialog-copy');
      content.replaceChildren();
      copy.textContent.trim().split(/\n\s*\n/).forEach(text => {
        const paragraph = document.createElement('p'); paragraph.textContent = text.trim(); content.append(paragraph);
      });
      openDialog(dialog, helpToggle); return;
    }
    if (event.target.closest('[data-close-help]')) { closeDialog(document.getElementById('workspace-help')); return; }
    const sidebarToggle = event.target.closest('[data-toggle-sidebar]');
    if (sidebarToggle) {
      const collapsed = document.documentElement.dataset.sidebar !== 'collapsed';
      document.documentElement.dataset.sidebar = collapsed ? 'collapsed' : 'expanded';
      try { localStorage.setItem('smartfridge-sidebar', collapsed ? 'collapsed' : 'expanded'); } catch (error) {}
      syncSidebar(); return;
    }
    const subnavToggle = event.target.closest('[data-toggle-subnav]');
    if (subnavToggle) {
      const expanded = subnavToggle.getAttribute('aria-expanded') === 'true';
      const subnav = document.getElementById(subnavToggle.getAttribute('aria-controls'));
      subnavToggle.setAttribute('aria-expanded', String(!expanded));
      if (subnav) subnav.hidden = expanded;
      return;
    }
    const notificationTrigger = event.target.closest('[data-open-notifications]');
    if (notificationTrigger) { openDialog(document.getElementById('workspace-notifications'), notificationTrigger); return; }
    if (event.target.closest('[data-close-notifications]')) { closeDialog(document.getElementById('workspace-notifications')); return; }
    const navigationTrigger = event.target.closest('[data-open-navigation]');
    if (navigationTrigger) { openDialog(drawer,navigationTrigger); return; }
    const searchTrigger = event.target.closest('[data-open-search]');
    if (searchTrigger) { openSearch(searchTrigger); return; }
    if (event.target.closest('[data-close-navigation], #workspace-drawer a')) { closeDialog(drawer); return; }
    if (event.target.closest('[data-close-search], #workspace-search-results a')) { closeDialog(search); return; }
    if (event.target instanceof HTMLDialogElement) {
      const bounds = event.target.getBoundingClientRect();
      if (event.clientX<bounds.left || event.clientX>bounds.right || event.clientY<bounds.top || event.clientY>bounds.bottom) closeDialog(event.target);
    }
  });
  document.addEventListener('keydown', event => {
    if ((event.ctrlKey || event.metaKey) && !event.altKey && event.key.toLowerCase() === 'k' && getSearch()) {
      event.preventDefault(); const search=getSearch(); if(search.open) closeDialog(search); else openSearch(document.activeElement); return;
    }
    const dialog = document.querySelector('dialog[open]'); if (!dialog) return;
    if (event.key === 'Escape') { event.preventDefault(); closeDialog(dialog); return; }
    if (dialog.id === 'workspace-search') {
      const links = [...dialog.querySelectorAll('.app-search-results a')];
      if (event.key==='Enter' && document.activeElement===dialog.querySelector('input') && links.length) { event.preventDefault(); links[0].click(); return; }
      if (['ArrowDown','ArrowUp'].includes(event.key) && links.length) {
        const index=links.indexOf(document.activeElement), delta=event.key==='ArrowDown'?1:-1;
        const next=index<0?(delta>0?0:links.length-1):(index+delta+links.length)%links.length;
        event.preventDefault(); links[next].focus(); return;
      }
    }
    if (event.key !== 'Tab') return;
    const controls=[...dialog.querySelectorAll('a[href],button:not([disabled]),input:not([disabled]),[tabindex="0"]')].filter(node=>node.getClientRects().length);
    const first=controls[0],last=controls[controls.length-1]; if(!first) return;
    if(event.shiftKey && (document.activeElement===first || !dialog.contains(document.activeElement))) {event.preventDefault();last.focus();}
    else if(!event.shiftKey && (document.activeElement===last || !dialog.contains(document.activeElement))) {event.preventDefault();first.focus();}
  });
  document.addEventListener('close', event => {
    if (!['workspace-drawer','workspace-search','workspace-notifications','workspace-help','workspace-access'].includes(event.target.id)) return;
    if (!document.querySelector('dialog[open]')) document.documentElement.classList.remove('workspace-menu-open');
    const opener=openers.get(event.target); if(!opener) return;
    if(opener.matches('[data-open-navigation],[data-open-search],[data-open-notifications],[data-toggle-help]')) opener.setAttribute('aria-expanded','false');
    if (document.querySelector('dialog[open]')) return;
    if (opener.closest('dialog:not([open])')) {
      const fallback = document.querySelector(desktop.matches ? '[data-open-search]' : '[data-open-navigation]');
      if (fallback) fallback.focus({preventScroll:true});
    } else if(document.contains(opener) && (event.target.id!=='workspace-drawer' || !desktop.matches)) opener.focus({preventScroll:true});
  },true);
  desktop.addEventListener('change',event=>{if(event.matches)closeDialog(getDrawer());});
  document.querySelector('[data-read-notifications]')?.addEventListener('submit',async event=>{
    event.preventDefault();const form=event.currentTarget,button=form.querySelector('button'),status=document.querySelector('[data-notification-status]');
    button.disabled=true;
    try{
      const response=await fetch(form.action,{method:'POST',body:new FormData(form),headers:{Accept:'application/json'},signal:AbortSignal.timeout(10000)});
      if(!response.ok || !response.headers.get('content-type')?.includes('application/json'))throw new Error();
      const result=await response.json(),unread=Number(result.unread)||0;
      document.querySelectorAll('[data-notification-count]').forEach(node=>{node.hidden=!unread;node.textContent=unread>99?'99+':String(unread);});
      document.querySelectorAll('[data-open-notifications]').forEach(node=>node.setAttribute('aria-label',unread?'Уведомления, непрочитанных: '+unread:'Уведомления'));
      document.querySelectorAll('.sf-notification-item').forEach(node=>node.classList.remove('is-unread'));
      status.textContent=unread?'Осталось непрочитанных: '+unread+'. Обновите страницу.':'Уведомления прочитаны.';
    }catch(_){status.textContent='Не удалось сохранить. Повторите позже.';button.disabled=false;}
  });
})();
