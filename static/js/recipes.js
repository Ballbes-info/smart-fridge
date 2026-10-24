/* Failed remote photos leave the local illustration visible, including offline. */
for (const image of document.querySelectorAll('[data-recipe-photo]')) {
  const hide = () => { image.hidden = true; };
  image.addEventListener('error', hide, {once: true});
  if (image.complete && image.naturalWidth === 0) hide();
}

// Requests return the page immediately; matching and generation continue in the background.
const wait = ms => new Promise(resolve => setTimeout(resolve, ms));
async function pollMatching(root) {
  const url = root.dataset.pollUrl;
  let failures = 0;
  for (let attempt=0; attempt<240 && document.contains(root); attempt++) {
    try {
      const response = await fetch(url, {headers:{Accept:'application/json'}, cache:'no-store', signal:AbortSignal.timeout(12000)});
      if (!response.ok) throw new Error();
      const data = await response.json();
      failures=0;
      if (data.status==='ready' || data.partial && !root.dataset.preview) {
        root.innerHTML=data.html;
        if(data.partial) root.dataset.preview='true';
        for (const image of root.querySelectorAll('[data-recipe-photo]')) {
          image.addEventListener('error',()=>image.hidden=true,{once:true});
          if (image.complete && !image.naturalWidth) image.hidden=true;
        }
        if (data.status==='ready') return;
      }
      if (data.status==='failed') throw new Error(data.message || 'Не удалось выполнить подбор.');
      const text=root.querySelector('[data-match-progress]');
      if (text) text.textContent=data.phase==='index'?'Готовим поиск по базе рецептов…':data.total?`Проверено ${data.done.toLocaleString('ru-RU')} из ${data.total.toLocaleString('ru-RU')} рецептов`:'Сравниваем ингредиенты с запасами…';
    } catch(error) {
      if (++failures>=3 || error.message!=='Failed to fetch' && error.message.startsWith('Не удалось')) break;
    }
    await wait(attempt<5?180:900);
  }
  root.replaceChildren();
  const message=document.createElement('p');message.setAttribute('role','alert');message.textContent='Подбор временно недоступен. ';
  const retry=document.createElement('a');retry.href=location.pathname+location.search+(location.search?'&':'?')+'retry=1';retry.textContent='Повторить';message.append(retry);root.append(message);
}
for (const root of document.querySelectorAll('[data-recipe-match][data-pending]')) pollMatching(root);
async function pollGeneration(root) {
  for (let attempt=0;attempt<180 && document.contains(root);attempt++) {
    try {
      const response=await fetch(root.dataset.pollUrl,{headers:{Accept:'application/json'},cache:'no-store',signal:AbortSignal.timeout(12000)});
      if (!response.ok) throw new Error();
      const data=await response.json();
      if (data.status==='used' && data.url) { location.replace(data.url);return; }
      if (data.status==='failed') {location.reload();return;}
    } catch(error) { if (attempt>10) break; }
    await wait(1200);
  }
  const message=document.createElement('p');message.setAttribute('role','alert');message.textContent='Не удалось проверить готовность. Обновите страницу: повторно создавать рецепт не нужно.';root.replaceChildren(message);
}
for (const root of document.querySelectorAll('[data-recipe-generation]')) pollGeneration(root);
for (const form of document.querySelectorAll('[data-generation-form]')) form.addEventListener('submit',()=>{
  const button=form.querySelector('[type=submit]');button.disabled=true;button.textContent='Отправляем…';
});
for (const button of document.querySelectorAll('[data-recipe-print]')) button.addEventListener('click',()=>window.print());
