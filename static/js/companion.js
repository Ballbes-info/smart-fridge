/* Local pet behavior and explicitly submitted questions. No inference in movement/hints. */
(() => {
 const pet=document.querySelector('.sf-companion');if(!pet||!window.SFBunnySprite)return;
 const button=pet.querySelector('.sf-companion-open'),panel=pet.querySelector('.sf-companion-panel'),tip=pet.querySelector('[data-pet-tip]');
 const form=pet.querySelector('[data-pet-form]'),question=form.elements.question,send=pet.querySelector('[data-pet-send]');
 const status=pet.querySelector('[data-pet-status]'),copy=pet.querySelector('[data-pet-copy]'),current=pet.querySelector('[data-pet-current]');
 const answer=pet.querySelector('[data-pet-answer]'),reply=pet.querySelector('[data-pet-reply]'),dot=pet.querySelector('.sf-companion-dot');
 const reconnect=pet.querySelector('[data-pet-reconnect]'),rest=pet.querySelector('[data-pet-rest]');
 const reduced=matchMedia('(prefers-reduced-motion: reduce)'),sprite=new SFBunnySprite(pet.querySelector('.sf-companion-sprite'));
 const storageKey='smartfridge-pet-'+pet.dataset.scope;
 let stored={};try{stored=JSON.parse(localStorage.getItem(storageKey)||'{}')||{};}catch(_){}
 let position={x:0,y:0},size={w:88,h:124},route=[],roamAt=Date.now()+1100,lastActivity=Date.now(),airborne=false,velocity={x:0,y:0},landUntil=0,nextHop=Date.now()+22000,keyboardMode=false,held=false;
 let movementTimer,animationFrame,pollTimer,tipTimer,drag=null,hover=false,suppressClick=false,stopped=false,obscured=false;
 let activeId=pet.dataset.activeJob||'',lastId=activeId||pet.dataset.lastJob||'',activeMode=pet.dataset.activeMode||form.elements.mode.value;
 let busy=!!activeId,ownRequest=false,lastQuestion='',remaining=Number(pet.querySelector('[data-pet-remaining]').textContent),failures=0,loading=false;
 let restUntil=stored.version===2?(Number(stored.restUntil)||0):0,lastTick=performance.now(),openedAt=Date.now(),lastHint=Number(stored.lastHint)||0,hintDay=Number(stored.hintDay)||0,hintCount=Number(stored.hintCount)||0,greeted=!!stored.greeted,ambientCount=Number(stored.ambientCount)||0;
 const clamp=(value,min,max)=>Math.max(min,Math.min(max,value));
 const fieldFocused=()=>document.activeElement?.matches('input,textarea,select,[contenteditable=true]');
 function viewport(){const v=window.visualViewport;return {x:v?.offsetLeft||0,y:v?.offsetTop||0,w:v?.width||innerWidth,h:v?.height||innerHeight};}
 function limits(){const v=viewport();return {left:v.x+8,top:v.y+8,right:v.x+Math.max(8,v.w-size.w-8),bottom:v.y+Math.max(8,v.h-size.h-8)};}
 function bound(p){const b=limits();return {x:clamp(p.x,b.left,b.right),y:clamp(p.y,b.top,b.bottom)};}
 function save(){const b=limits();try{localStorage.setItem(storageKey,JSON.stringify({version:2,x:(position.x-b.left)/Math.max(1,b.right-b.left),restUntil,lastHint,hintDay,hintCount,greeted,ambientCount}));}catch(_){} }
 function place(p){position=bound(p);pet.style.left=position.x.toFixed(2)+'px';pet.style.top=position.y.toFixed(2)+'px';}
 function roam(){const b=limits(),distance=b.right-b.left;let x=b.left+Math.random()*distance;if(Math.abs(x-position.x)<Math.min(90,distance*.35))x=position.x<(b.left+b.right)/2?b.right:b.left;route=[{x,y:b.bottom}];}
 function jump(){if(airborne||drag||reduced.matches)return;airborne=true;velocity={x:0,y:-395};route=[];sprite.set('jump');pet.dataset.motion='jump';}
 function fall(){route=[];if(reduced.matches){place({x:position.x,y:limits().bottom});airborne=false;sprite.set('idle');return;}airborne=position.y<limits().bottom-.5;velocity={x:0,y:0};sprite.set(airborne?'fall':'idle');pet.dataset.motion=airborne?'fall':'idle';}
 function animate(timestamp){
  if(stopped||document.hidden)return;const dt=Math.min(.034,Math.max(0,(timestamp-lastTick)/1000));lastTick=timestamp;const now=Date.now();
  if(airborne&&!drag&&!obscured){
   velocity.y=Math.min(1100,velocity.y+1450*dt);const floor=limits().bottom,nextY=position.y+velocity.y*dt;
   place({x:position.x+velocity.x*dt,y:Math.min(floor,nextY)});sprite.set(velocity.y<0?'jump':'fall');
   if(nextY>=floor){airborne=false;velocity={x:0,y:0};landUntil=now+240;sprite.set('land');pet.dataset.motion='land';roamAt=now+850;save();}
  }else if(!drag&&!obscured){
   if(!held&&!reduced.matches&&pet.dataset.roaming&&restUntil<=now){
    if(route.length){const dx=route[0].x-position.x,step=Math.sign(dx)*Math.min(Math.abs(dx),66*dt);sprite.walk(dx);place({x:position.x+step,y:limits().bottom});pet.dataset.motion='walk';if(Math.abs(dx)<1.5){route=[];roamAt=now+1000+Math.random()*1800;save();}}
    else if(now>=roamAt){roam();roamAt=now+2500;}
    else if(now>=landUntil){sprite.set('idle');pet.dataset.motion='idle';}
    if(now>=nextHop){nextHop=now+20000+Math.random()*25000;jump();}
   }else if(now>=landUntil){sprite.set(restUntil>now?'sleeping':busy||!panel.hidden||hover?'alert':'idle');pet.dataset.motion=restUntil>now?'sleep':'idle';}
  }
  sprite.tick(timestamp);animationFrame=requestAnimationFrame(animate);
 }
 function positionBubble(node){
  if(node.hidden)return;const v=viewport(),r=node.getBoundingClientRect();
  const maxHeight=Math.max(100,v.h-24);node.style.maxHeight=maxHeight+'px';
  const height=Math.min(node.scrollHeight,maxHeight),width=Math.min(r.width||350,v.w-24);
  const x=clamp(position.x+size.w/2-width/2,v.x+12,v.x+v.w-width-12);
  let y=position.y-height-12;if(y<v.y+12)y=position.y+size.h+12;
  y=clamp(y,v.y+12,v.y+v.h-height-12);node.style.left=Math.round(x)+'px';node.style.top=Math.round(y)+'px';
 }
 function nonce(){return [...crypto.getRandomValues(new Uint8Array(16))].map(x=>x.toString(16).padStart(2,'0')).join('');}
 form.elements.nonce.value=nonce();
 function lock(value){busy=value;question.disabled=value;form.elements.mode.disabled=value;form.elements.with_stock.disabled=value;send.disabled=value||remaining<=0;}
 async function jsonRequest(url,options={}){
  const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),15000);
  try{const response=await fetch(url,{...options,cache:'no-store',signal:controller.signal,headers:{Accept:'application/json',...(options.headers||{})}});const data=await response.json().catch(()=>({}));if(!response.ok||response.redirected){const e=new Error(data.error||'Не удалось выполнить запрос. Обновите страницу.');e.status=response.status;throw e;}return data;}
  finally{clearTimeout(timeout);}
 }
 function format(text){
  answer.replaceChildren();let list=null,kind='';
  function inline(node,line){for(const part of line.split(/(\*\*[^*\n]+\*\*)/g)){const e=part.startsWith('**')&&part.endsWith('**')?document.createElement('strong'):document.createTextNode(part);if(e.nodeType===1)e.textContent=part.slice(2,-2);node.append(e);}}
  for(const line of text.split('\n')){if(!line.trim()){list=null;continue;}const ordered=line.match(/^\s*(\d+)[.)]\s+(.+)$/),unordered=line.match(/^\s*[-•*]\s+(.+)$/),heading=line.match(/^#{1,3}\s+(.+)$/);if(ordered||unordered){const type=ordered?'ol':'ul';if(!list||kind!==type){list=document.createElement(type);kind=type;if(ordered)list.start=Number(ordered[1]);answer.append(list);}const li=document.createElement('li');inline(li,ordered?ordered[2]:unordered[1]);list.append(li);}else{list=null;const p=document.createElement(heading?'h3':'p');inline(p,heading?heading[1]:line);answer.append(p);}}
 }
 function updateState(value){
  pet.dataset.state=value;
  copy.textContent=value==='pending'?'Готовлю ответ. Можно продолжать работу на сайте.':value==='used'?'Ответ готов.':value==='error'?'Не получилось ответить. Можно повторить вопрос.':'Спросите о продуктах или приготовлении.';
  dot.hidden=value!=='used'||!panel.hidden;button.setAttribute('aria-label','Клевер: открыть помощника'+(value==='used'?'. Ответ готов.':'. Можно перетащить.'));
 }
 function showReply(data){
  if(typeof data.question==='string')pet.querySelector('[data-pet-question]').textContent=data.question;
  reply.hidden=false;
  if(data.status==='used')format(data.answer||'');else answer.textContent=(data.error||'Запрос прерван.')+' Лимит не потрачен.';
  current.hidden=false;const mode=data.mode||activeMode;current.href=mode==='cooking'?panel.dataset.cookingUrl:panel.dataset.chatUrl;
  if(typeof data.remaining==='number'){remaining=data.remaining;pet.querySelector('[data-pet-remaining]').textContent=remaining;}
  if(data.status==='error'&&!question.value){question.value=data.question||lastQuestion;form.elements.with_stock.checked=!!data.with_stock;}
  positionBubble(panel);
 }
 async function poll(id,background=false){
  if(stopped||document.hidden)return;
  try{
   const data=await jsonRequest(panel.dataset.jobBase+encodeURIComponent(id));failures=0;
   if(stopped)return;updateState(data.status);
   if(data.status==='pending'){
    lock(true);activeId=id;lastId=id;activeMode=data.mode||activeMode;
    pollTimer=setTimeout(()=>poll(id,panel.hidden),panel.hidden?8000:1800);return;
   }
   activeId='';lastId=id;if(typeof data.remaining==='number'){remaining=data.remaining;pet.querySelector('[data-pet-remaining]').textContent=remaining;}lock(false);reconnect.hidden=true;
   if(!background||!panel.hidden)showReply(data);
   if(ownRequest){document.dispatchEvent(new CustomEvent('sf-assistant-job',{detail:{id,mode:activeMode,status:data.status,source:'pet',remaining:data.remaining,data}}));ownRequest=false;}
   status.textContent=data.status==='used'?'Ответ сохранён в истории разговора.':'Лимит не потрачен.';
   route=[];roamAt=Date.now()+3000;
  }catch(error){
   if([401,403,404].includes(error.status)){status.textContent=error.message;activeId='';reconnect.hidden=true;lock(false);if(error.status===403)send.disabled=true;return;}
   if(++failures<5){pollTimer=setTimeout(()=>poll(id,background),3000);return;}
   status.textContent='Связь прервалась. Вопрос сохранён — проверьте ответ позже.';reconnect.hidden=false;
  }
 }
 function dismissTip(){tip.hidden=true;clearTimeout(tipTimer);roamAt=Date.now()+2500;}
 function close(focus=false){panel.hidden=true;button.setAttribute('aria-expanded','false');if(focus)button.focus();roamAt=Date.now()+1800;}
 async function open(focus=false){
  dismissTip();route=[];panel.hidden=false;button.setAttribute('aria-expanded','true');sprite.set('alert');positionBubble(panel);
  if(focus)question.focus({preventScroll:true});
  if(lastId&&!loading){loading=true;clearTimeout(pollTimer);await poll(lastId);loading=false;}
 }
 function watch(detail){
  if(!detail||detail.source==='pet'||!['chat','cooking'].includes(detail.mode))return;
  activeMode=detail.mode;lastId=detail.id;activeId=detail.status==='pending'?detail.id:'';updateState(detail.status);lock(detail.status==='pending');
  clearTimeout(pollTimer);if(activeId&&!document.querySelector('[data-assistant]'))pollTimer=setTimeout(()=>poll(activeId,true),4000);
  else if(!activeId&&!panel.hidden)poll(lastId);
 }
 form.addEventListener('submit',async event=>{
  event.preventDefault();if(busy||remaining<=0)return;
  if(!question.value.trim()){status.textContent='Напишите вопрос.';question.focus();return;}
  const payload=new FormData(form),text=question.value.trim(),mode=form.elements.mode.value;
  lock(true);route=[];status.textContent='Отправляю вопрос…';
  try{
   const data=await jsonRequest(form.action,{method:'POST',body:payload});if(typeof data.id!=='string'||!data.id)throw new Error('Не подтверждена отправка. Повторите тот же вопрос.');
   activeId=lastId=data.id;activeMode=mode;lastQuestion=text;ownRequest=true;remaining=Math.max(0,remaining-1);pet.querySelector('[data-pet-remaining]').textContent=remaining;
   question.value='';form.elements.nonce.value=nonce();reply.hidden=false;pet.querySelector('[data-pet-question]').textContent=text;answer.textContent='Готовлю ответ…';updateState('pending');status.textContent='';positionBubble(panel);
   document.dispatchEvent(new CustomEvent('sf-assistant-job',{detail:{id:data.id,mode,status:'pending',source:'pet',question:text,remaining}}));clearTimeout(pollTimer);failures=0;poll(data.id);
  }catch(error){
   status.textContent=error.name==='AbortError'||error instanceof TypeError?'Не удалось подтвердить отправку. Повторите: вопрос не будет списан дважды.':error.message;
   if(error.status===422)form.elements.nonce.value=nonce();lock(false);
  }
 });
 question.addEventListener('keydown',event=>{if(event.key==='Enter'&&(event.ctrlKey||event.metaKey)&&!send.disabled){event.preventDefault();form.requestSubmit();}});
 form.elements.mode.addEventListener('change',()=>{const note=pet.querySelector('[data-pet-attachment]');if(note)note.hidden=form.elements.mode.value!=='cooking';});
 reconnect.addEventListener('click',()=>{if(activeId){reconnect.hidden=true;failures=0;poll(activeId);}});
 pet.querySelector('[data-pet-close]').addEventListener('click',()=>close(true));
 button.addEventListener('click',()=>{if(suppressClick){suppressClick=false;return;}sprite.tap?.();if(panel.hidden)open();else close();});
 button.addEventListener('pointerenter',()=>{hover=true;route=[];sprite.set('alert');});
 button.addEventListener('pointerleave',()=>{hover=false;roamAt=Date.now()+1200;});
 button.addEventListener('pointerdown',event=>{
  if(event.button!==0)return;drag={id:event.pointerId,x:event.clientX,y:event.clientY,start:{...position},moved:false};button.setPointerCapture(event.pointerId);route=[];dismissTip();
 });
 button.addEventListener('pointermove',event=>{
  if(!drag||drag.id!==event.pointerId)return;const dx=event.clientX-drag.x,dy=event.clientY-drag.y;
  if(Math.hypot(dx,dy)>6)drag.moved=true;if(!drag.moved)return;
  close();airborne=false;sprite.set('drag');pet.dataset.motion='drag';place({x:drag.start.x+dx,y:drag.start.y+dy});positionBubble(tip);
 });
 function release(event){
  if(!drag||drag.id!==event.pointerId)return;
  if(drag.moved){suppressClick=true;setTimeout(()=>{suppressClick=false;},400);fall();button.blur();save();}
  drag=null;roamAt=Date.now()+2000;
 }
 button.addEventListener('pointerup',release);button.addEventListener('pointercancel',release);
 button.addEventListener('keydown',event=>{
  if(event.key==='ArrowUp'){event.preventDefault();jump();return;}
  if(!['ArrowLeft','ArrowRight','ArrowDown'].includes(event.key))return;event.preventDefault();route=[];
  if(event.key==='ArrowDown')fall();else{const dx=event.key==='ArrowLeft'?-18:18;place({x:position.x+dx,y:airborne?position.y:limits().bottom});sprite.walk(dx);save();}
 });
 rest.addEventListener('click',()=>{restUntil=restUntil>Date.now()?0:Date.now()+600000;route=[];save();refreshRest();sprite.set(restUntil?'sleeping':'alert');status.textContent=restUntil?'Клевер отдыхает. Вопросы по-прежнему можно задавать.':'Клевер проснулся.';});
 function refreshRest(){rest.textContent=restUntil>Date.now()?'Разбудить':'Отдыхать 10 минут';}
 pet.querySelector('[data-pet-hide]').addEventListener('click',async event=>{
  const control=event.currentTarget;control.disabled=true;
  try{const body=new FormData();body.set('csrf_token',panel.dataset.csrf);const data=await jsonRequest(panel.dataset.hideUrl,{method:'POST',body});if(!data.ok)throw new Error();stop();sprite.destroy?.();pet.remove();document.querySelector('#main-content')?.focus({preventScroll:true});}
  catch(_){status.textContent='Не удалось скрыть питомца. Попробуйте ещё раз.';control.disabled=false;}
 });
 function contextualHint(){
  const notification=document.querySelector('.sf-notification-list .sf-notification-item:not(.is-expired)');
  if(notification){const name=notification.querySelector('strong')?.textContent?.trim();const url=new URL(notification.href,location.href);if(name&&url.origin===location.origin)return {text:'У «'+name.slice(0,70)+'» скоро срок. Проверьте карточку, чтобы не забыть о продукте.',url:url.pathname+url.search+url.hash,label:'Проверить продукт →'};}
  const empty=document.querySelector('.product-empty'),heading=empty?.querySelector('h2')?.textContent?.trim();
  if(empty&&['Продуктов пока нет','Сначала создайте место хранения'].includes(heading)){
   const action=empty.querySelector('a[href]');if(action){const url=new URL(action.href,location.href);if(url.origin===location.origin)return {text:heading==='Продуктов пока нет'?'Можно начать с одного продукта: укажите название, количество и срок, если он известен.':'Сначала выберите, где будут храниться продукты. После создания места можно наполнить его.',url:url.pathname+url.search,label:heading==='Продуктов пока нет'?'Добавить продукт →':'Создать место →'};}
  }
  if(form.elements.recipe_key.value)return {text:'Если шаг приготовления непонятен, можете спросить меня об открытом рецепте.',label:'Задать вопрос →',ask:true};
  const error=[...document.querySelectorAll('.product-error,[aria-invalid=true]')].find(n=>!pet.contains(n)&&n.getClientRects().length);
  if(error)return {text:'На странице есть отмеченные поля. Можно проверить их или открыть справку по приложению.',url:'/home/assistant/guide',label:'Открыть справку →'};
  const messages=['Я гуляю здесь, внизу. Если понадобится помощь — нажмите на меня.','Можно задать мне вопрос о продуктах или приготовлении. Я отвечу рядом с вами.','Если хотите тишины, подсказки можно выключить в настройках питомца.'];return {text:messages[ambientCount%messages.length],label:'Задать вопрос →',ask:true};
 }
 function hint(now){
  if(!pet.dataset.hints||now-openedAt<(greeted?20000:10000)||now-lastHint<180000||busy||drag||hover||!panel.hidden||!tip.hidden||obscured||fieldFocused()||document.querySelector('dialog[open]'))return;
  let shared={};try{shared=JSON.parse(localStorage.getItem(storageKey)||'{}')||{};}catch(_){}
  if(Number(shared.lastHint)>lastHint)lastHint=Number(shared.lastHint);if(Number(shared.hintDay)===Math.floor(now/86400000))hintCount=Math.max(hintCount,Number(shared.hintCount)||0);if(now-lastHint<180000)return;
  const day=Math.floor(now/86400000);if(day!==hintDay){hintDay=day;hintCount=0;}if(hintCount>=8)return;
  const item=greeted?contextualHint():{text:'Привет! Я Клевер. Гуляю по нижнему краю. Меня можно поднять и отпустить — я приземлюсь.',label:'Познакомиться →',ask:true};if(!item)return;
  pet.querySelector('[data-tip-text]').textContent=item.text;const link=pet.querySelector('[data-tip-link]');link.textContent=item.label;link.href=item.url||'#companion-panel';link.dataset.ask=item.ask?'1':'';
  lastHint=now;hintCount++;greeted=true;ambientCount++;save();tip.hidden=false;route=[];sprite.set('alert');positionBubble(tip);tipTimer=setTimeout(dismissTip,14000);
 }
 pet.querySelector('[data-dismiss-tip]').addEventListener('click',dismissTip);
 pet.querySelector('[data-tip-link]').addEventListener('click',event=>{const ask=event.currentTarget.dataset.ask==='1';dismissTip();if(ask){event.preventDefault();if(form.elements.recipe_key.value)form.elements.mode.value='cooking';open(true);}});
 function tick(){
  if(stopped||document.hidden)return;const now=Date.now(),outsideInput=fieldFocused()&&!pet.contains(document.activeElement);
  obscured=!!document.querySelector('dialog[open]')||(outsideInput&&innerWidth<=650);pet.classList.toggle('is-obscured',obscured);
  held=obscured||!panel.hidden||!tip.hidden||hover||!!drag||(keyboardMode&&pet.contains(document.activeElement))||outsideInput||busy;
  if(!obscured&&!airborne)hint(now);if(!panel.hidden)positionBubble(panel);if(!tip.hidden)positionBubble(tip);refreshRest();movementTimer=setTimeout(tick,180);
 }
 function resize(){size={w:pet.offsetWidth,h:pet.offsetHeight};route=[];place({x:position.x,y:airborne||drag?position.y:limits().bottom});if(!panel.hidden)positionBubble(panel);if(!tip.hidden)positionBubble(tip);}
 function stop(){stopped=true;save();clearTimeout(movementTimer);cancelAnimationFrame(animationFrame);clearTimeout(pollTimer);clearTimeout(tipTimer);}
 document.addEventListener('pointerdown',event=>{keyboardMode=false;lastActivity=Date.now();if(!pet.contains(event.target))close();},{passive:true});
 document.addEventListener('pointermove',()=>{lastActivity=Date.now();},{passive:true});
 document.addEventListener('keydown',event=>{keyboardMode=true;lastActivity=Date.now();if(event.key==='Escape'){dismissTip();if(!panel.hidden){close(true);event.stopPropagation();}}});
 document.addEventListener('focusin',event=>{if(!pet.contains(event.target))close();});
 document.addEventListener('sf-assistant-job',event=>{if(!stopped)watch(event.detail);});
 document.addEventListener('visibilitychange',()=>{clearTimeout(movementTimer);cancelAnimationFrame(animationFrame);clearTimeout(pollTimer);route=[];if(!document.hidden&&!stopped){lastTick=performance.now();roamAt=Date.now()+700;tick();animationFrame=requestAnimationFrame(animate);if(activeId)poll(activeId,panel.hidden);}});
 window.addEventListener('resize',resize);window.visualViewport?.addEventListener('resize',resize);window.visualViewport?.addEventListener('scroll',resize);

 window.addEventListener('pagehide',stop);window.addEventListener('pageshow',event=>{if(event.persisted){stopped=false;lastTick=performance.now();tick();animationFrame=requestAnimationFrame(animate);if(activeId)poll(activeId,panel.hidden);}});
 reduced.addEventListener('change',()=>{sprite.reduce(reduced.matches);route=[];sprite.set('idle');});sprite.reduce(reduced.matches);
 resize();const b=limits();place({x:Number.isFinite(stored.x)?b.left+clamp(stored.x,0,1)*(b.right-b.left):b.right-30,y:b.bottom});
 lock(busy);refreshRest();if(activeId){updateState('pending');if(!document.querySelector('[data-assistant]'))pollTimer=setTimeout(()=>poll(activeId,true),4000);}
 tick();animationFrame=requestAnimationFrame(animate);
})();
