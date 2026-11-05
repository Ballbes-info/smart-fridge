/* Local pet behavior and explicitly submitted questions. No inference in movement/hints. */
(() => {
 const pet=document.querySelector('.sf-companion');if(!pet||!window.SFNekoSprite)return;
 const button=pet.querySelector('.sf-companion-open'),panel=pet.querySelector('.sf-companion-panel'),tip=pet.querySelector('[data-pet-tip]');
 const form=pet.querySelector('[data-pet-form]'),question=form.elements.question,send=pet.querySelector('[data-pet-send]');
 const status=pet.querySelector('[data-pet-status]'),copy=pet.querySelector('[data-pet-copy]'),current=pet.querySelector('[data-pet-current]');
 const answer=pet.querySelector('[data-pet-answer]'),reply=pet.querySelector('[data-pet-reply]'),dot=pet.querySelector('.sf-companion-dot');
 const reconnect=pet.querySelector('[data-pet-reconnect]'),rest=pet.querySelector('[data-pet-rest]');
 const reduced=matchMedia('(prefers-reduced-motion: reduce)'),sprite=new SFNekoSprite(pet.querySelector('.sf-companion-sprite'));
 const storageKey='smartfridge-pet-'+pet.dataset.scope;
 let stored={};try{stored=JSON.parse(localStorage.getItem(storageKey)||'{}')||{};}catch(_){}
 let position={x:0,y:0},size={w:72,h:76},route=[],obstacles=[],obstaclesAt=0,roamAt=Date.now()+1800,poseUntil=0,lastActivity=Date.now();
 let movementTimer,pollTimer,tipTimer,drag=null,hover=false,suppressClick=false,stopped=false,obscured=false;
 let activeId=pet.dataset.activeJob||'',lastId=activeId||pet.dataset.lastJob||'',activeMode=pet.dataset.activeMode||form.elements.mode.value;
 let busy=!!activeId,ownRequest=false,lastQuestion='',remaining=Number(pet.querySelector('[data-pet-remaining]').textContent),failures=0,loading=false;
 let restUntil=Number(stored.restUntil)||0,lastTick=performance.now(),openedAt=Date.now(),lastHint=Number(stored.lastHint)||0,hintDay=Number(stored.hintDay)||0,hintCount=Number(stored.hintCount)||0;
 const clamp=(value,min,max)=>Math.max(min,Math.min(max,value));
 const fieldFocused=()=>document.activeElement?.matches('input,textarea,select,[contenteditable=true]');
 function viewport(){const v=window.visualViewport;return {x:v?.offsetLeft||0,y:v?.offsetTop||0,w:v?.width||innerWidth,h:v?.height||innerHeight};}
 function limits(){const v=viewport(),header=document.querySelector('.site-header')?.getBoundingClientRect();return {left:v.x+10,top:v.y+Math.min((header?.bottom||50)+6,v.h*.28),right:v.x+Math.max(10,v.w-size.w-10),bottom:v.y+Math.max(10,v.h-size.h-10)};}
 function bound(p){const b=limits();return {x:clamp(p.x,b.left,b.right),y:clamp(p.y,Math.min(b.top,b.bottom),b.bottom)};}
 function save(){const b=limits();try{localStorage.setItem(storageKey,JSON.stringify({x:(position.x-b.left)/Math.max(1,b.right-b.left),y:(position.y-b.top)/Math.max(1,b.bottom-b.top),restUntil,lastHint,hintDay,hintCount}));}catch(_){}
 }
 function place(p){position=bound(p);pet.style.left=Math.round(position.x)+'px';pet.style.top=Math.round(position.y)+'px';}
 function refreshObstacles(force=false){
  if(!force&&Date.now()-obstaclesAt<1000)return;obstaclesAt=Date.now();const v=viewport();obstacles=[];
  for(const node of document.querySelectorAll('a[href],button,input,textarea,select,summary,[role=button],[contenteditable=true],.workspace-sidebar,.site-header,.workspace-mobile-bar')){
   if(pet.contains(node)||!node.getClientRects().length)continue;
   const r=node.getBoundingClientRect();if(!r.width||!r.height||r.bottom<=v.y||r.top>=v.y+v.h||r.right<=v.x||r.left>=v.x+v.w)continue;
   const add=box=>obstacles.push({left:box.left-5,right:box.right+5,top:box.top-5,bottom:box.bottom+5});
   if(node.tagName==='A'&&!node.matches('.workspace-secondary-button,.sf-storage-add,[role=button]')){
    // A whole dashboard tile is a link. Protect its actual text, rather than
    // declaring all of the otherwise empty tile padding an impassable wall.
    const walker=document.createTreeWalker(node,NodeFilter.SHOW_TEXT);let text,seen=0;
    while((text=walker.nextNode())&&seen<6){if(!text.textContent.trim()||text.parentElement.closest('svg,[aria-hidden=true]'))continue;const range=document.createRange();range.selectNodeContents(text);for(const box of range.getClientRects())if(box.width&&box.height)add(box);seen++;}
   }else add(r);
   if(obstacles.length>=500)break;
  }
 }
 function safe(p){return !obstacles.some(r=>p.x<r.right&&p.x+size.w>r.left&&p.y<r.bottom&&p.y+size.h>r.top);}
 function segment(a,b){const n=Math.max(1,Math.ceil(Math.hypot(a.x-b.x,a.y-b.y)/22));for(let i=1;i<=n;i++)if(!safe({x:a.x+(b.x-a.x)*i/n,y:a.y+(b.y-a.y)*i/n}))return false;return true;}
 function path(point){if(segment(position,point))return [point];for(const middle of [{x:position.x,y:point.y},{x:point.x,y:position.y}])if(safe(middle)&&segment(position,middle)&&segment(middle,point))return [middle,point];return null;}
 function park(){
  refreshObstacles(true);const b=limits(),candidates=[{x:b.right,y:b.bottom},{x:b.left,y:b.bottom},{x:b.right,y:b.top},{x:b.left,y:b.top}];
  for(let y=b.bottom;y>=b.top;y-=28)for(let x=b.right;x>=b.left;x-=28)candidates.push({x,y});
  candidates.sort((a,c)=>Math.hypot(a.x-position.x,a.y-position.y)-Math.hypot(c.x-position.x,c.y-position.y));
  const found=candidates.find(p=>safe(p));if(found)place(found);
 }
 function roam(){
  refreshObstacles(true);if(!safe(position)){park();return;}
  const b=limits();
  for(let i=0;i<24;i++){
   let p=i<8?bound({x:position.x+Math.cos(i*Math.PI/4)*120,y:position.y+Math.sin(i*Math.PI/4)*120}):{x:b.left+Math.random()*(b.right-b.left),y:b.top+Math.random()*(b.bottom-b.top)};
   // Prefer margins and free space, but do not restrict the pet to a fixed dock.
   if(i>=8&&i<16){if(i%2)p.x=i%4===1?b.left:b.right;else p.y=i%4===0?b.top:b.bottom;}
   if(Math.hypot(p.x-position.x,p.y-position.y)<75||!safe(p))continue;
   const result=path(p);if(result){route=result;return;}
  }
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
 button.addEventListener('click',()=>{if(suppressClick){suppressClick=false;return;}if(panel.hidden)open();else close();});
 button.addEventListener('pointerenter',()=>{hover=true;route=[];sprite.set('alert');});
 button.addEventListener('pointerleave',()=>{hover=false;roamAt=Date.now()+1200;});
 button.addEventListener('pointerdown',event=>{
  if(event.button!==0)return;drag={id:event.pointerId,x:event.clientX,y:event.clientY,start:{...position},moved:false};button.setPointerCapture(event.pointerId);route=[];dismissTip();
 });
 button.addEventListener('pointermove',event=>{
  if(!drag||drag.id!==event.pointerId)return;const dx=event.clientX-drag.x,dy=event.clientY-drag.y;
  if(Math.hypot(dx,dy)>6)drag.moved=true;if(!drag.moved)return;
  sprite.set('alert');place({x:drag.start.x+dx,y:drag.start.y+dy});positionBubble(panel);
 });
 function release(event){
  if(!drag||drag.id!==event.pointerId)return;
  if(drag.moved){suppressClick=true;setTimeout(()=>{suppressClick=false;},400);refreshObstacles(true);if(!safe(position))park();button.blur();save();}
  drag=null;roamAt=Date.now()+2000;
 }
 button.addEventListener('pointerup',release);button.addEventListener('pointercancel',release);
 button.addEventListener('keydown',event=>{
  const directions={ArrowLeft:[-16,0],ArrowRight:[16,0],ArrowUp:[0,-16],ArrowDown:[0,16]};const d=directions[event.key];if(!d)return;
  event.preventDefault();const next=bound({x:position.x+d[0],y:position.y+d[1]});refreshObstacles(true);if(safe(next)){place(next);sprite.walk(...d);save();}route=[];
 });
 rest.addEventListener('click',()=>{restUntil=restUntil>Date.now()?0:Date.now()+600000;route=[];save();refreshRest();sprite.set(restUntil?'sleeping':'alert');status.textContent=restUntil?'Клевер отдыхает. Вопросы по-прежнему можно задавать.':'Клевер проснулся.';});
 function refreshRest(){rest.textContent=restUntil>Date.now()?'Разбудить':'Отдыхать 10 минут';}
 pet.querySelector('[data-pet-hide]').addEventListener('click',async event=>{
  const control=event.currentTarget;control.disabled=true;
  try{const body=new FormData();body.set('csrf_token',panel.dataset.csrf);const data=await jsonRequest(panel.dataset.hideUrl,{method:'POST',body});if(!data.ok)throw new Error();stop();pet.remove();document.querySelector('#main-content')?.focus({preventScroll:true});}
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
  return null;
 }
 function hint(now){
  if(!pet.dataset.hints||now-openedAt<45000||now-lastHint<2400000||now-lastActivity<12000||busy||drag||hover||!panel.hidden||!tip.hidden||obscured||fieldFocused()||document.querySelector('dialog[open]'))return;
  let shared={};try{shared=JSON.parse(localStorage.getItem(storageKey)||'{}')||{};}catch(_){}
  if(Number(shared.lastHint)>lastHint)lastHint=Number(shared.lastHint);if(Number(shared.hintDay)===Math.floor(now/86400000))hintCount=Math.max(hintCount,Number(shared.hintCount)||0);if(now-lastHint<2400000)return;
  const day=Math.floor(now/86400000);if(day!==hintDay){hintDay=day;hintCount=0;}if(hintCount>=3)return;
  const item=contextualHint();if(!item)return;
  pet.querySelector('[data-tip-text]').textContent=item.text;const link=pet.querySelector('[data-tip-link]');link.textContent=item.label;link.href=item.url||'#companion-panel';link.dataset.ask=item.ask?'1':'';
  lastHint=now;hintCount++;save();tip.hidden=false;route=[];sprite.set('alert');positionBubble(tip);tipTimer=setTimeout(dismissTip,14000);
 }
 pet.querySelector('[data-dismiss-tip]').addEventListener('click',dismissTip);
 pet.querySelector('[data-tip-link]').addEventListener('click',event=>{const ask=event.currentTarget.dataset.ask==='1';dismissTip();if(ask){event.preventDefault();form.elements.mode.value='cooking';open(true);}});
 function tick(){
  if(stopped||document.hidden)return;const timestamp=performance.now(),dt=Math.min(.2,(timestamp-lastTick)/1000);lastTick=timestamp;const now=Date.now();
  const outsideInput=fieldFocused()&&!pet.contains(document.activeElement);
  obscured=!!document.querySelector('dialog[open]')||(outsideInput&&innerWidth<=650);pet.classList.toggle('is-obscured',obscured);
  const held=obscured||!panel.hidden||!tip.hidden||hover||drag||pet.contains(document.activeElement)||outsideInput||busy;
  if(!held&&!reduced.matches&&pet.dataset.roaming&&restUntil<=now&&now-lastActivity<65000){
   if(route.length){refreshObstacles();const target=route[0],dx=target.x-position.x,dy=target.y-position.y,distance=Math.hypot(dx,dy),speed=Math.min(distance,58*dt);const next=bound({x:position.x+dx/(distance||1)*speed,y:position.y+dy/(distance||1)*speed});if(!safe(next)){route=[];roamAt=now+1800;}else{sprite.walk(dx,dy);place(next);if(distance<7){route.shift();if(!route.length){sprite.set('idle');roamAt=now+2500+Math.random()*4500;save();}}}}
   else if(now>=roamAt){if(now-openedAt>8000&&Math.random()<.22){sprite.set('scratchSelf');poseUntil=now+1600;roamAt=now+2200;}else{roam();roamAt=now+2500;}}
   else if(now>poseUntil){sprite.set(now-lastActivity>65000?'sleeping':'idle');}
  }else if(!drag){sprite.set(restUntil>now?'sleeping':busy?'alert':!panel.hidden||hover?'alert':now-lastActivity>65000?'sleeping':'idle');}
  sprite.tick(timestamp);if(!obscured)hint(now);if(!panel.hidden)positionBubble(panel);if(!tip.hidden)positionBubble(tip);refreshRest();
  movementTimer=setTimeout(tick,100);
 }
 function resize(){size={w:pet.offsetWidth,h:pet.offsetHeight};route=[];refreshObstacles(true);place(position);if(!panel.hidden)positionBubble(panel);if(!tip.hidden)positionBubble(tip);}
 function stop(){stopped=true;save();clearTimeout(movementTimer);clearTimeout(pollTimer);clearTimeout(tipTimer);}
 document.addEventListener('pointerdown',event=>{lastActivity=Date.now();if(!pet.contains(event.target))close();},{passive:true});
 document.addEventListener('pointermove',()=>{lastActivity=Date.now();},{passive:true});
 document.addEventListener('keydown',event=>{lastActivity=Date.now();if(event.key==='Escape'){dismissTip();if(!panel.hidden){close(true);event.stopPropagation();}}});
 document.addEventListener('focusin',event=>{if(!pet.contains(event.target))close();});
 document.addEventListener('sf-assistant-job',event=>{if(!stopped)watch(event.detail);});
 document.addEventListener('visibilitychange',()=>{clearTimeout(movementTimer);clearTimeout(pollTimer);route=[];if(!document.hidden&&!stopped){lastTick=performance.now();roamAt=Date.now()+1500;tick();if(activeId)poll(activeId,panel.hidden);}});
 window.addEventListener('resize',resize);window.visualViewport?.addEventListener('resize',resize);window.visualViewport?.addEventListener('scroll',resize);
 window.addEventListener('scroll',()=>{obstaclesAt=0;},{passive:true});
 window.addEventListener('pagehide',stop);window.addEventListener('pageshow',event=>{if(event.persisted){stopped=false;lastTick=performance.now();tick();if(activeId)poll(activeId,panel.hidden);}});
 reduced.addEventListener('change',()=>{sprite.reduce(reduced.matches);route=[];sprite.set('idle');});sprite.reduce(reduced.matches);
 resize();const b=limits();if(Number.isFinite(stored.x)&&Number.isFinite(stored.y))place({x:b.left+clamp(stored.x,0,1)*(b.right-b.left),y:b.top+clamp(stored.y,0,1)*(b.bottom-b.top)});else place({x:b.right,y:b.bottom});refreshObstacles(true);if(!safe(position))park();
 lock(busy);refreshRest();if(activeId){updateState('pending');if(!document.querySelector('[data-assistant]'))pollTimer=setTimeout(()=>poll(activeId,true),4000);}
 tick();
})();
