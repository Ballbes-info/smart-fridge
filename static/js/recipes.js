/* Recipe interactions: keep working results visible and preserve browser navigation. */
(()=>{
  const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));
  function photos(root=document){
    for(const img of root.querySelectorAll('[data-recipe-photo]')){
      img.addEventListener('error',()=>img.hidden=true,{once:true});
      if(img.complete && !img.naturalWidth)img.hidden=true;
    }
  }
  async function jsonRequest(url,options={}){
    const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),15000);
    try{
      const response=await fetch(url,{...options,credentials:'same-origin',cache:'no-store',headers:{...options.headers,Accept:'application/json'},signal:controller.signal});
      if(response.redirected && !response.headers.get('content-type')?.includes('application/json')){
        location.assign(response.url);throw Object.assign(new Error(),{navigating:true});
      }
      if(!response.headers.get('content-type')?.includes('application/json'))throw new Error('Сервер не ответил. Повторите действие.');
      const data=await response.json();
      if(!response.ok)throw Object.assign(new Error(data.message || 'Не удалось сохранить. Повторите действие.'),{data});
      return data;
    }finally{clearTimeout(timer)}
  }
  function matchBanner(root){
    let bar=root.querySelector('[data-match-banner]');
    if(!bar){bar=document.createElement('div');bar.className='recipe-update-banner';bar.dataset.matchBanner='';bar.setAttribute('role','status');root.prepend(bar)}
    return bar;
  }
  function applyResults(root,html){
    root.innerHTML=html;delete root.dataset.pending;photos(root);
    // No automatic scrolling or focus jumps: this may be a background completion.
  }
  function retryMatching(root,message){
    const bar=matchBanner(root);bar.replaceChildren();
    const text=document.createElement('span');text.textContent=message;bar.append(text);
    const retry=document.createElement('button');retry.type='button';retry.className='workspace-secondary-button';retry.textContent='Повторить';
    retry.addEventListener('click',()=>{bar.textContent='Обновляем подбор…';pollMatching(root,true)});bar.append(retry);
    root.querySelectorAll('.recipe-spinner,.recipe-loading,.recipe-skeleton,.recipe-results-updating').forEach(n=>n.hidden=true);
  }
  async function pollMatching(root,retry=false){
    if(root.dataset.polling)return;
    root.dataset.polling='true';let failures=0;
    try{
      for(let attempt=0;attempt<240 && document.contains(root);attempt++){
        try{
          const url=new URL(root.dataset.pollUrl,location.origin);
          if(retry){url.searchParams.set('retry','1');retry=false}
          const data=await jsonRequest(url);
          if(!['ready','pending','busy','failed'].includes(data.status) || (data.status==='ready' || data.partial) && typeof data.html!=='string')throw new Error('Не удалось прочитать ответ.');
          failures=0;
          if(data.status==='ready'){
            delete root.dataset.pending;
            if(root.querySelector('.recipe-card')){
              const bar=matchBanner(root);bar.replaceChildren();
              const text=document.createElement('span');text.textContent='Подбор по всей базе готов.';
              const show=document.createElement('button');show.type='button';show.className='workspace-secondary-button';show.textContent=`Показать результаты · ${Number(data.total||0).toLocaleString('ru-RU')}`;
              show.addEventListener('click',()=>{applyResults(root,data.html);const first=root.querySelector('.recipe-card-link');if(first)first.focus({preventScroll:true})});
              bar.append(text,show);root.querySelectorAll('.recipe-results-updating,.recipe-loading,.recipe-skeleton').forEach(n=>n.hidden=true);
            }else applyResults(root,data.html);
            return;
          }
          if(data.partial && !root.querySelector('.recipe-card')){root.innerHTML=data.html;root.dataset.preview='true';photos(root)}
          if(data.status==='failed'){retryMatching(root,data.message||'Не удалось завершить подбор. Найденные рецепты сохранены на экране.');return}
          const progress=root.querySelector('[data-match-progress]');
          if(progress)progress.textContent=data.status==='busy'?(data.message||'Подбор занят. Можно открыть каталог.') :data.total?`Проверено ${Number(data.done||0).toLocaleString('ru-RU')} из ${Number(data.total).toLocaleString('ru-RU')}`:'Уточняем подбор…';
        }catch(error){
          if(error.navigating)return;
          if(++failures>=3){retryMatching(root,'Связь прервалась. Уже найденные рецепты можно открыть.');return}
        }
        await delay(attempt<3?250:1500);
      }
      retryMatching(root,'Полный подбор ещё не готов. Уже найденные рецепты доступны.');
    }finally{delete root.dataset.polling}
  }
  function messageFor(form,message,error=false){
    let node=form.querySelector('[data-action-status]');
    if(!node){node=document.createElement('p');node.dataset.actionStatus='';node.className='recipe-action-status';node.setAttribute('role','status');form.append(node)}
    node.textContent=message;node.classList.toggle('is-error',error);
  }
  // Delegation also covers the calculator after its HTML is updated.
  document.addEventListener('submit',async event=>{
    const form=event.target;
    if(!form.matches('[data-recipe-action]') || typeof window.fetch!=='function')return;
    event.preventDefault();if(form.dataset.saving)return;
    const body=new FormData(form),button=event.submitter||form.querySelector('[type=submit]');
    const label=button?.textContent;form.dataset.saving='true';form.setAttribute('aria-busy','true');
    if(button){button.disabled=true;button.textContent='Сохраняем…'}
    try{
      const data=await jsonRequest(form.getAttribute('action')||location.href,{method:'POST',body});
      if(data.ok!==true)throw new Error(data.message||'Не удалось сохранить.');
      const action=body.get('action');
      if(action==='favorite'){
        form.querySelector('[name=value]').value=data.favorite?'0':'1';
        if(button){button.dataset.savedLabel=data.favorite?'Убрать из избранного':'В избранное';button.setAttribute('aria-pressed',String(data.favorite))}
      }
      if(action==='steps_reset')document.querySelectorAll('.recipe-cooking-form [name=checked]').forEach(input=>input.checked=false);
      if(data.nutrition_html){
        const old=document.getElementById('nutrition'),wasOpen=old?.querySelector('details')?.open;
        if(old){const holder=document.createElement('template');holder.innerHTML=data.nutrition_html;old.replaceWith(holder.content);const details=document.querySelector('#nutrition details');if(details)details.open=wasOpen;const status=document.createElement('p');status.setAttribute('role','status');status.className='recipe-action-status';status.textContent='Расчёт сохранён';document.getElementById('nutrition').prepend(status);document.querySelector('#nutrition [type=submit]')?.focus({preventScroll:true})}
      }else messageFor(form,action==='favorite'?(data.favorite?'Добавлено в избранное':'Убрано из избранного'):'Сохранено');
    }catch(error){
      if(error.data?.csrf)document.querySelectorAll('[data-recipe-action] [name=csrf_token]').forEach(input=>input.value=error.data.csrf);
      if(!error.navigating)messageFor(form,error.name==='AbortError'?'Ответ задерживается. Проверьте соединение и повторите действие.':error.message||'Не удалось сохранить.',true);
    }finally{
      delete form.dataset.saving;form.removeAttribute('aria-busy');
      if(button){button.disabled=false;button.textContent=button.dataset.savedLabel||label;delete button.dataset.savedLabel}
    }
  });
  async function pollGeneration(root){
    for(let attempt=0;attempt<300 && document.contains(root);attempt++){
      try{
        const data=await jsonRequest(root.dataset.pollUrl);
        if(data.status==='used' && data.url){location.replace(data.url);return}
        if(data.status==='failed'){location.reload();return}
      }catch(error){if(error.navigating)return;if(attempt>10)break}
      await delay(1500);
    }
    root.replaceChildren();const text=document.createElement('p');text.textContent='Не удалось проверить готовность. Рецепт не нужно создавать повторно.';
    const reload=document.createElement('button');reload.className='workspace-secondary-button';reload.type='button';reload.textContent='Проверить готовность';reload.addEventListener('click',()=>location.reload());root.append(text,reload);
  }
  for(const root of document.querySelectorAll('[data-recipe-match][data-pending]'))pollMatching(root);
  for(const root of document.querySelectorAll('[data-recipe-generation]'))pollGeneration(root);
  for(const form of document.querySelectorAll('[data-generation-form]'))form.addEventListener('submit',()=>{const button=form.querySelector('[type=submit]');button.dataset.originalLabel=button.textContent;button.disabled=true;button.textContent='Отправляем…'});
  window.addEventListener('pageshow',()=>document.querySelectorAll('[data-generation-form] [type=submit][data-original-label]').forEach(button=>{button.disabled=false;button.textContent=button.dataset.originalLabel;delete button.dataset.originalLabel}));
  document.addEventListener('click',event=>{if(event.target.closest('[data-recipe-print]'))window.print()});
  photos();
})();
