(() => {
  const root = document.querySelector('[data-assistant]'); if (!root) return;
  const form = root.querySelector('[data-chat-form]'), log = root.querySelector('[role=log]');
  const question = form.elements.question, send = form.querySelector('[type=submit]');
  const status = root.querySelector('[data-chat-status]'), count = root.querySelector('[data-character-count]');
  const clearButton = root.querySelector('[data-open-clear]'), clearDialog = document.querySelector('[data-clear-dialog]');
  const older = root.querySelector('[data-load-older]'), jump = root.querySelector('[data-jump-latest]'), reconnect = root.querySelector('[data-reconnect]');
  let busy = false, timer, responseNode, activeId = '', stopped = false, elapsedTimer, started = Date.now();
  let remaining = Number(root.querySelector('[data-remaining]').textContent);
  let lastQuestion = [...log.querySelectorAll('.is-user p')].at(-1)?.textContent || '';
  const nearBottom = () => log.scrollHeight - log.scrollTop - log.clientHeight < 90;
  function latest() { log.scrollTop = log.scrollHeight; jump.hidden = true; }
  function updateCount() { count.textContent = question.value.length.toLocaleString('ru-RU') + ' / 2 000'; }
  function lock(value) {
    busy = value; question.disabled = value; form.elements.with_stock.disabled = value;
    send.disabled = value || remaining <= 0; clearButton.disabled = (value && !root.dataset.blocked) || !log.querySelector('.is-user');
    log.setAttribute('aria-busy', value ? 'true' : 'false');
  }
  function nonce() {
    return [...crypto.getRandomValues(new Uint8Array(16))].map(n => n.toString(16).padStart(2,'0')).join('');
  }
  async function jsonRequest(url, options = {}) {
    const controller = new AbortController(), timeout = setTimeout(() => controller.abort(), 15000);
    try {
      const response = await fetch(url, {...options, cache:'no-store', signal:controller.signal, headers:{Accept:'application/json', ...(options.headers || {})}});
      const data = await response.json().catch(() => ({}));
      if (!response.ok || response.redirected) {
        const error = new Error(data.error || 'Не удалось выполнить запрос. Обновите страницу.');
        error.status = response.status; throw error;
      }
      return data;
    } finally { clearTimeout(timeout); }
  }
  // Model output never becomes HTML. Only text, emphasis and lists are constructed.
  function inline(parent, text) {
    text.split(/(\*\*[^*\n]+\*\*)/g).forEach(part => {
      if (part.startsWith('**') && part.endsWith('**')) {const strong=document.createElement('strong');strong.textContent=part.slice(2,-2);parent.append(strong);}
      else parent.append(document.createTextNode(part));
    });
  }
  function renderAnswer(node, text) {
    node.replaceChildren(); let list=null, kind='';
    for (const line of text.split('\n')) {
      if (!line.trim()) {list=null;kind='';continue;}
      const numbered=line.match(/^\s*(\d+)[.)]\s+(.+)$/), bullet=line.match(/^\s*[-•*]\s+(.+)$/), heading=line.match(/^#{1,3}\s+(.+)$/);
      if (numbered || bullet) {
        const nextKind=numbered?'ol':'ul';
        if (!list || kind!==nextKind) {list=document.createElement(nextKind);kind=nextKind;if(numbered)list.start=Number(numbered[1]);node.append(list);}
        const li=document.createElement('li');inline(li,numbered?numbered[2]:bullet[1]);list.append(li);
      } else {list=null;kind='';const block=document.createElement(heading?'h3':'p');inline(block,heading?heading[1]:line);node.append(block);}
    }
    const article=node.closest('article');article.dataset.answerStatus='used';
    if (!article.querySelector('[data-copy-answer]')) {
      const button=document.createElement('button');button.type='button';button.className='assistant-text-button assistant-copy';button.dataset.copyAnswer='';button.textContent='Скопировать ответ';article.append(button);
      button.addEventListener('click', async () => {
        try {await navigator.clipboard.writeText(text);status.textContent='Ответ скопирован.';}
        catch (_) {const range=document.createRange();range.selectNodeContents(node);const selection=getSelection();selection.removeAllRanges();selection.addRange(range);status.textContent='Текст выделен. Скопируйте его через меню или Ctrl + C.';}
      });
    }
  }
  function timestamp(node, seconds) {
    const date=new Date(seconds*1000);if (!Number.isFinite(date.getTime())) return;
    node.dateTime=date.toISOString();node.textContent=new Intl.DateTimeFormat('ru-RU',{day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit',timeZone:'Europe/Moscow'}).format(date)+' МСК';
  }
  function message(text, user, id, seconds=Date.now()/1000) {
    const article = document.createElement('article');article.className='assistant-message '+(user?'is-user':'is-assistant');
    article.dataset[user?'exchange':'answerFor']=id;
    const label=document.createElement('strong');label.textContent=user?'Вы':'СмартФридж';article.append(label);
    if(user){const time=document.createElement('time');timestamp(time,seconds);article.append(time);}
    const copy=document.createElement(user?'p':'div');if(!user)copy.className='assistant-answer';copy.textContent=text;article.append(copy);
    return {article,copy};
  }
  function addContext(article, data) {
    article.querySelectorAll('.assistant-message-context').forEach(node=>node.remove());
    const texts=[];
    if(data.recipe)texts.push('Рецепт: '+data.recipe.title+(data.recipe.step!=null?' · шаг '+(data.recipe.step+1):''));
    if(data.with_stock)texts.push('Учтены запасы: '+data.stock_count+' партий'+(data.stock_omitted?' · передана часть списка':''));
    for(const text of texts){const small=document.createElement('small');small.className='assistant-message-context';small.textContent=text;article.append(small);}
  }
  function waiting() {
    clearInterval(elapsedTimer);started=Date.now();
    elapsedTimer=setInterval(()=>{if(busy&&!reconnect.hidden)return; if(busy)status.textContent=Date.now()-started>20000?'Ответ ещё готовится. Можно перейти в другой раздел — разговор сохранится.':'Помощник готовит ответ…';},1000);
  }
  async function poll(id, failures = 0) {
    if (stopped) return;
    try {
      const data=await jsonRequest(root.dataset.jobBase+encodeURIComponent(id));
      if (data.status==='pending') {timer=setTimeout(()=>poll(id),2000);return;}
      const shouldScroll=nearBottom();
      if (data.status==='used') renderAnswer(responseNode,data.answer);
      else {responseNode.textContent=(data.error || 'Не удалось получить ответ.')+' Лимит не потрачен.';responseNode.closest('article').dataset.answerStatus='error';}
      const user=[...log.querySelectorAll('[data-exchange]')].find(node=>node.dataset.exchange===id);if(user)addContext(user,data);
      remaining=Number(data.remaining);root.querySelector('[data-remaining]').textContent=remaining;
      status.textContent=data.status==='used'?'Ответ готов.':'Можно повторить вопрос.';
      document.dispatchEvent(new CustomEvent('sf-assistant-job',{detail:{id,mode:form.elements.mode.value,status:data.status}}));
      activeId='';reconnect.hidden=true;clearInterval(elapsedTimer);lock(false);
      if(data.status!=='used'){question.value=lastQuestion;updateCount();}
      if(shouldScroll)latest();else jump.hidden=false;
    } catch (error) {
      if([401,403,404].includes(error.status)){status.textContent=error.message;reconnect.hidden=true;clearInterval(elapsedTimer);return;}
      if(failures<5){timer=setTimeout(()=>poll(id,failures+1),3000);return;}
      status.textContent='Связь прервалась. Вопрос сохранён; проверьте ответ после восстановления интернета.';reconnect.hidden=false;
    }
  }
  reconnect.addEventListener('click',()=>{if(activeId){reconnect.hidden=true;status.textContent='Проверяю ответ…';poll(activeId);}});
  root.querySelectorAll('[data-prompt]').forEach(button=>button.addEventListener('click',()=>{
    if(busy)return;question.value=button.dataset.prompt;
    if(button.hasAttribute('data-use-stock')){form.elements.with_stock.checked=true;status.textContent='В следующем вопросе будут учтены ваши запасы.';}
    updateCount();question.focus();
  }));
  clearButton.addEventListener('click',()=>clearDialog.showModal());
  clearDialog.querySelector('[data-close-clear]').addEventListener('click',()=>clearDialog.close());
  clearDialog.addEventListener('click',event=>{if(event.target===clearDialog){const r=clearDialog.getBoundingClientRect();if(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom)clearDialog.close();}});
  form.addEventListener('submit', async event=>{
    event.preventDefault();if(busy||remaining<=0)return;
    if(!question.value.trim()){status.textContent='Напишите вопрос перед отправкой.';question.focus();return;}
    const text=question.value.trim(),payload=new FormData(form);lock(true);status.textContent='Отправляю вопрос…';
    try {
      const data=await jsonRequest(form.action,{method:'POST',body:payload});
      if(typeof data.id!=='string'||!data.id)throw new Error('Не получено подтверждение отправки. Повторите: вопрос не продублируется.');
      lastQuestion=text;activeId=data.id;
      document.dispatchEvent(new CustomEvent('sf-assistant-job',{detail:{id:data.id,mode:form.elements.mode.value,status:'pending'}}));root.querySelector('.assistant-welcome')?.remove();
      const existing=[...log.querySelectorAll('[data-answer-for]')].find(node=>node.dataset.answerFor===data.id);
      if(existing)responseNode=existing.querySelector('.assistant-answer');
      else {const user=message(text,true,data.id),answer=message('Готовлю ответ…',false,data.id);log.append(user.article,answer.article);responseNode=answer.copy;}
      remaining=Math.max(0,remaining-1);root.querySelector('[data-remaining]').textContent=remaining;
      question.value='';updateCount();form.elements.nonce.value=nonce();latest();waiting();poll(data.id);
    } catch(error) {
      status.textContent=error.name==='AbortError'||error instanceof TypeError?'Не удалось подтвердить отправку. Повторите: один и тот же вопрос не будет списан дважды.':error.message;
      if(error.status===422)form.elements.nonce.value=nonce();
      lock(false);
    }
  });
  older.addEventListener('click',async()=>{
    if(!older.dataset.before)return;older.disabled=true;
    try{
      const data=await jsonRequest(root.dataset.historyUrl+'?before='+encodeURIComponent(older.dataset.before));
      const height=log.scrollHeight,top=log.scrollTop,fragment=document.createDocumentFragment();
      for(const row of data.rows){
        if([...log.querySelectorAll('[data-exchange]')].some(node=>node.dataset.exchange===row.id))continue;
        const user=message(row.question,true,row.id,row.created_at),answer=message(row.answer||((row.error||'Запрос прерван.')+' Лимит не потрачен.'),false,row.id);
        addContext(user.article,row);if(row.status==='used')renderAnswer(answer.copy,row.answer);
        fragment.append(user.article,answer.article);
      }
      log.prepend(fragment);log.scrollTop=top+log.scrollHeight-height;older.dataset.before=data.before||'';older.hidden=!data.before;
      status.textContent='Предыдущие сообщения загружены.';
    }catch(error){status.textContent=error.message||'Не удалось загрузить историю.';}finally{older.disabled=false;}
  });
  question.addEventListener('input',updateCount);
  question.addEventListener('keydown',event=>{if(event.key==='Enter'&&(event.ctrlKey||event.metaKey)&&!busy&&!send.disabled){event.preventDefault();form.requestSubmit();}});
  jump.addEventListener('click',latest);log.addEventListener('scroll',()=>{if(nearBottom())jump.hidden=true;});
  root.querySelectorAll('[data-answer-status=used] .assistant-answer').forEach(node=>renderAnswer(node,node.textContent));
  root.querySelectorAll('time[data-time]').forEach(node=>timestamp(node,Number(node.dataset.time)));
  if(root.dataset.pending){lock(true);activeId=root.dataset.pending;responseNode=[...log.querySelectorAll('[data-answer-for]')].find(node=>node.dataset.answerFor===activeId)?.querySelector('.assistant-answer');if(responseNode){latest();waiting();poll(activeId);}}
  else {lock(Boolean(root.dataset.blocked));if(log.querySelector('.is-user'))latest();}
  let petExchange = null;
  document.addEventListener('sf-assistant-job', event => {
    const detail=event.detail;if(!detail||detail.source!=='pet')return;
    if(detail.status==='pending'){
      lock(true);status.textContent='Клевер готовит ответ на ваш вопрос.';
      if(typeof detail.remaining==='number'){remaining=detail.remaining;root.querySelector('[data-remaining]').textContent=remaining;}
      petExchange={id:detail.id,node:null};
      if(detail.mode===form.elements.mode.value){
        root.querySelector('.assistant-welcome')?.remove();const user=message(detail.question||'Вопрос Клеверу',true,detail.id),assistant=message('Готовлю ответ…',false,detail.id);
        log.append(user.article,assistant.article);petExchange.node=assistant.copy;latest();
      }
    }else if(petExchange?.id===detail.id){
      if(petExchange.node){if(detail.status==='used')renderAnswer(petExchange.node,detail.data?.answer||'');else petExchange.node.textContent=(detail.data?.error||'Не удалось получить ответ.')+' Лимит не потрачен.';}
      if(typeof detail.remaining==='number'){remaining=detail.remaining;root.querySelector('[data-remaining]').textContent=remaining;}
      lock(false);status.textContent=detail.status==='used'?'Ответ готов.':'Клеверу можно задать вопрос ещё раз.';petExchange=null;
    }
  });
  window.addEventListener('pagehide',()=>{stopped=true;clearTimeout(timer);clearInterval(elapsedTimer);});
  window.addEventListener('pageshow',event=>{if(event.persisted)location.reload();});
})();
