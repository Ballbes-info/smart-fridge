/* Local pet behavior and explicitly submitted questions. No inference in movement/hints. */
(() => {
 const pet=document.querySelector('.sf-companion');if(!pet||!window.SFBunnySprite)return;
 const button=pet.querySelector('.sf-companion-open'),panel=pet.querySelector('.sf-companion-panel'),tip=pet.querySelector('[data-pet-tip]');
 const form=pet.querySelector('[data-pet-form]'),question=form.elements.question,send=pet.querySelector('[data-pet-send]');
 const status=pet.querySelector('[data-pet-status]'),copy=pet.querySelector('[data-pet-copy]'),current=pet.querySelector('[data-pet-current]');
 const answer=pet.querySelector('[data-pet-answer]'),reply=pet.querySelector('[data-pet-reply]'),dot=pet.querySelector('.sf-companion-dot');
 const reconnect=pet.querySelector('[data-pet-reconnect]'),rest=pet.querySelector('[data-pet-rest]');
 const reduced=matchMedia('(prefers-reduced-motion: reduce)'),sprite=new SFBunnySprite(pet.querySelector('.sf-companion-sprite'));
 const chatButton=pet.querySelector('[data-pet-open-chat]'),speech=pet.querySelector('[data-pet-reaction]'),shadow=pet.querySelector('.sf-companion-shadow');
 const storageKey='smartfridge-pet-'+pet.dataset.scope;
 let stored={};try{stored=JSON.parse(localStorage.getItem(storageKey)||'{}')||{};}catch(_){}
 let position={x:0,y:0},size={w:88,h:124},route=[],roamAt=Date.now()+1100,lastActivity=Date.now(),airborne=false,velocity={x:0,y:0},landUntil=0,nextHop=Date.now()+22000,keyboardMode=false,held=false;
 let movementTimer,animationFrame,pollTimer,tipTimer,speechTimer,drag=null,hover=false,hoverUntil=0,reading=false,suppressClick=false,stopped=false,obscured=false;
 let actionUntil=0,actionClip='idle',actionEmotion='content',nextBehavior=Date.now()+5500,lastBehavior='',tapCount=0,tappedAt=0,dropHeight=0,impact=0,lastAnnouncement=0;
 let motionOverride=false;
 let activeId=pet.dataset.activeJob||'',lastId=activeId||pet.dataset.lastJob||'',activeMode=pet.dataset.activeMode||form.elements.mode.value;
 let busy=!!activeId,ownRequest=false,lastQuestion='',remaining=Number(pet.querySelector('[data-pet-remaining]').textContent),failures=0,loading=false;
 let restUntil=[2,3].includes(stored.version)?Math.min(Date.now()+600000,Number(stored.restUntil)||0):0,lastTick=performance.now(),openedAt=Date.now(),lastHint=Number(stored.lastHint)||0,hintDay=Number(stored.hintDay)||0,hintCount=Number(stored.hintCount)||0,greeted=!!stored.greeted,ambientCount=Number(stored.ambientCount)||0;
 const clamp=(value,min,max)=>Math.max(min,Math.min(max,value));
 motionOverride=stored.motionOverride===true;
 const motionReduced=()=>reduced.matches&&!motionOverride;
 function face(value){sprite.face(value);pet.dataset.emotion=value;}
 function say(text,duration=2300){
  if(!panel.hidden||obscured)return;pet.querySelector('[data-reaction-text]').textContent=text;speech.hidden=false;clearTimeout(speechTimer);positionBubble(speech);speechTimer=setTimeout(()=>{speech.hidden=true;},duration);
  if(Date.now()-lastAnnouncement>1500){pet.querySelector('[data-pet-announcement]').textContent=text;lastAnnouncement=Date.now();}
 }
 function act(clip,emotion,duration=2000,words=''){route=[];actionClip=clip;actionEmotion=emotion;actionUntil=Date.now()+duration;sprite.set(clip);face(emotion);pet.dataset.motion=clip;if(words)say(words);}
 function wake(){restUntil=0;refreshRest();save();}
 function interact(kind='stroke'){
  wake();dismissTip();const now=Date.now();tapCount=now-tappedAt<1000?tapCount+1:1;tappedAt=now;sprite.tap();
  if(kind==='jump'){if(motionReduced())act('wave','happy',1600,'Хоп!');else jump(true);return;}
  if(kind==='dance'||tapCount>=3){act(tapCount>=6?'celebrate':'dance','excited',2600,tapCount>=6?'Вот это заряд бодрости!':'Танец маленьких лап!');tapCount=tapCount>=6?0:tapCount;return;}
  act(tapCount===2?'wave':'groom','happy',1800,tapCount===2?'Ещё разок?':'Мр-р… спасибо!');
 }
 function autonomous(now){
  const options=(now-lastActivity>65000?['look','groom','stretch','peek','wave','dance','sleep']:['look','groom','stretch','peek','wave','dance']).filter(v=>v!==lastBehavior);const choice=options[Math.floor(Math.random()*options.length)];lastBehavior=choice;nextBehavior=now+8500+Math.random()*7000;
  act(choice,choice==='sleep'?'sleepy':choice==='dance'?'happy':['look','peek'].includes(choice)?'curious':'content',choice==='sleep'?4500:choice==='dance'?2400:1800);
 }
 const fieldFocused=()=>document.activeElement?.matches('input,textarea,select,[contenteditable=true]');
 function viewport(){const v=window.visualViewport;return {x:v?.offsetLeft||0,y:v?.offsetTop||0,w:v?.width||innerWidth,h:v?.height||innerHeight};}
 function limits(){const v=viewport();return {left:v.x+8,top:v.y+8,right:v.x+Math.max(8,v.w-size.w-16),bottom:v.y+Math.max(8,v.h-size.h-8)};}
 function bound(p){const b=limits();return {x:clamp(p.x,b.left,b.right),y:clamp(p.y,b.top,b.bottom)};}
 function save(){const b=limits();try{localStorage.setItem(storageKey,JSON.stringify({version:3,motionOverride,x:(position.x-b.left)/Math.max(1,b.right-b.left),restUntil,lastHint,hintDay,hintCount,greeted,ambientCount}));}catch(_){} }
 function place(p){
  position=bound(p);pet.style.left=position.x.toFixed(2)+'px';pet.style.top=position.y.toFixed(2)+'px';
  const floor=limits().bottom,height=Math.max(0,floor-position.y),scale=clamp(1-height/1000,.45,1);
  shadow.style.left=(position.x+size.w/2-26)+'px';shadow.style.top=(floor+size.h-9)+'px';shadow.style.transform=`scale(${scale},${scale})`;shadow.style.opacity=String(.2*scale);
 }
 function roam(){
  const b=limits(),distance=130+Math.random()*180;let direction=position.x-b.left<60?1:b.right-position.x<60?-1:Math.random()<.5?-1:1;
  let x=clamp(position.x+direction*distance,b.left,b.right);if(Math.abs(x-position.x)<35)x=clamp(position.x-direction*distance,b.left,b.right);route=[{x,y:b.bottom}];
 }
 function jump(manual=false){
  if(airborne||drag||motionReduced())return;airborne=true;dropHeight=0;velocity={x:manual?0:(Math.random()-.5)*80,y:manual?-440:-330};route=[];face('excited');sprite.set('jump');pet.dataset.motion='jump';if(manual)say('Лови мой хоп!');
 }
 function fall(){
  route=[];dropHeight=Math.max(0,limits().bottom-position.y);
  if(motionReduced()){place({x:position.x,y:limits().bottom});airborne=false;sprite.set('idle');face('content');pet.dataset.motion='idle';return;}
  airborne=dropHeight>.5;velocity.y=0;sprite.set(airborne?'fall':'idle');face(airborne?'surprised':'happy');pet.dataset.motion=airborne?'fall':'idle';if(airborne)say(dropHeight>160?'Ой-ой, лечу!':'Сейчас приземлюсь…',1600);
 }
 function animate(timestamp){
  if(stopped||document.hidden)return;const dt=Math.min(.034,Math.max(0,(timestamp-lastTick)/1000));lastTick=timestamp;const now=Date.now();
  if(airborne&&!drag&&!obscured){
   velocity.y=Math.min(1050,velocity.y+1280*dt);const floor=limits().bottom,b=limits(),nextY=position.y+velocity.y*dt;let nextX=position.x+velocity.x*dt;
   if(nextX<b.left||nextX>b.right){nextX=clamp(nextX,b.left,b.right);velocity.x*=-.25;}
   place({x:nextX,y:Math.min(floor,nextY)});sprite.set(velocity.y<0?'jump':'fall');pet.dataset.motion=velocity.y<0?'jump':'fall';
   sprite.physics({vertical:velocity.y,tilt:clamp(velocity.x/1200,-.15,.15)});
   if(velocity.y>0&&dropHeight>180)face('worried');
   if(nextY>=floor){
    impact=clamp(velocity.y/1000,.12,1);airborne=false;velocity={x:0,y:0};landUntil=now+600;actionUntil=landUntil;actionClip='land';actionEmotion=dropHeight>250?'dizzy':'proud';sprite.set('land');sprite.physics({impact,tilt:0});face(actionEmotion);pet.dataset.motion='land';roamAt=now+1100;nextBehavior=now+6500;save();
    if(dropHeight>70)say(dropHeight>250?'Уф! Земля на месте.':'Хоп — и я на лапках!',1900);dropHeight=0;
   }
  }else if(!drag&&!obscured){
   if(now<actionUntil){sprite.set(actionClip);face(actionEmotion);pet.dataset.motion=actionClip;}
   else if(!held&&!motionReduced()&&pet.dataset.roaming==='1'&&restUntil<=now){
    if(route.length){const dx=route[0].x-position.x,speed=58+Math.min(18,Math.abs(dx)*.04),step=Math.sign(dx)*Math.min(Math.abs(dx),speed*dt);sprite.walk(dx);face(busy?'curious':'content');sprite.physics({speed,impact:0,tilt:0});place({x:position.x+step,y:limits().bottom});pet.dataset.motion='walk';if(Math.abs(dx)<1.5){route=[];roamAt=now+700+Math.random()*1400;save();}}
    else if(now>=nextBehavior){autonomous(now);}
    else if(now>=roamAt){roam();roamAt=now+2500;}
    else{sprite.set('idle');face('content');pet.dataset.motion='idle';}
    if(now>=nextHop){nextHop=now+32000+Math.random()*22000;jump();}
   }else if(now>=landUntil){sprite.set(restUntil>now?'sleeping':busy?'look':'idle');face(restUntil>now?'sleepy':busy?'curious':'content');pet.dataset.motion=restUntil>now?'sleep':'idle';}
  }
  sprite.tick(timestamp);if(airborne&&!speech.hidden)positionBubble(speech);animationFrame=requestAnimationFrame(animate);
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
  dot.hidden=value!=='used'||!panel.hidden;chatButton.setAttribute('aria-label',value==='used'?'Спросить Клевера. Ответ готов.':'Спросить Клевера');if(value==='used')act('wave','proud',1600);else if(value==='error')act('look','worried',1400);
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
 function dismissTip(){reading=false;tip.hidden=true;clearTimeout(tipTimer);roamAt=Date.now()+2500;}
 function close(focus=false){panel.hidden=true;chatButton.setAttribute('aria-expanded','false');if(focus)chatButton.focus({preventScroll:true});if(!keyboardMode)chatButton.blur();actionUntil=0;roamAt=Date.now()+700;}
 async function open(focus=false){
  dismissTip();speech.hidden=true;route=[];actionUntil=0;panel.hidden=false;chatButton.setAttribute('aria-expanded','true');sprite.set('alert');face('curious');positionBubble(panel);
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
 chatButton.addEventListener('click',()=>{if(panel.hidden)open(true);else close();});
 pet.querySelectorAll('[data-pet-play]').forEach(control=>control.addEventListener('click',()=>{const kind=control.dataset.petPlay;close();interact(kind);}));
 button.addEventListener('click',()=>{if(suppressClick){suppressClick=false;return;}interact();});
 button.addEventListener('contextmenu',event=>event.preventDefault());
 button.addEventListener('dragstart',event=>event.preventDefault());
 button.addEventListener('pointerenter',()=>{hover=true;hoverUntil=Date.now()+1000;});
 button.addEventListener('pointerleave',()=>{hover=false;roamAt=Math.min(roamAt,Date.now()+500);});
 for(const bubble of [tip,speech]){bubble.addEventListener('pointerenter',()=>{reading=true;});bubble.addEventListener('pointerleave',()=>{reading=false;});}
 button.addEventListener('pointerdown',event=>{
  if(event.button!==0)return;event.preventDefault();drag={id:event.pointerId,x:event.clientX,y:event.clientY,start:{...position},moved:false,samples:[{x:event.clientX,t:performance.now()}]};button.setPointerCapture(event.pointerId);route=[];dismissTip();speech.hidden=true;
 });
 window.addEventListener('pointermove',event=>{
  if(!drag||drag.id!==event.pointerId)return;const dx=event.clientX-drag.x,dy=event.clientY-drag.y;
  if(Math.hypot(dx,dy)>6)drag.moved=true;if(!drag.moved)return;
  if(drag.samples.length>4)drag.samples.shift();drag.samples.push({x:event.clientX,t:performance.now()});
  close();actionUntil=0;airborne=false;sprite.set('drag');face('surprised');const samples=drag.samples,dt=Math.max(16,samples.at(-1).t-samples[0].t);sprite.physics({tilt:clamp((samples.at(-1).x-samples[0].x)/dt*.06,-.2,.2)});pet.dataset.motion='drag';place({x:drag.start.x+dx,y:drag.start.y+dy});
 });
 function release(event){
  if(!drag||drag.id!==event.pointerId)return;
  if(drag.moved){
   const first=drag.samples[0],last=drag.samples.at(-1);velocity.x=clamp((last.x-first.x)/Math.max(30,last.t-first.t)*250,-260,260);
   suppressClick=true;setTimeout(()=>{suppressClick=false;},400);fall();button.blur();save();
  }
  drag=null;hover=false;roamAt=Date.now()+1000;
 }
 window.addEventListener('pointerup',release);window.addEventListener('pointercancel',release);
 window.addEventListener('blur',()=>{if(drag)release({pointerId:drag.id});});
 button.addEventListener('keydown',event=>{
  if(event.key==='ArrowUp'){event.preventDefault();interact('jump');return;}
  if(!['ArrowLeft','ArrowRight','ArrowDown'].includes(event.key))return;event.preventDefault();route=[];
  if(event.key==='ArrowDown')fall();else{const dx=event.key==='ArrowLeft'?-18:18;place({x:position.x+dx,y:airborne?position.y:limits().bottom});sprite.walk(dx);save();}
 });
 rest.addEventListener('click',()=>{restUntil=restUntil>Date.now()?0:Date.now()+600000;route=[];save();refreshRest();actionUntil=0;sprite.set(restUntil?'sleeping':'alert');face(restUntil?'sleepy':'happy');status.textContent=restUntil?'Клевер отдыхает. Вопросы по-прежнему можно задавать.':'Клевер проснулся.';});
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
  const messages=['Я гуляю здесь, внизу. Для вопроса нажмите кнопку разговора рядом со мной.','Можно задать мне вопрос о продуктах или приготовлении. Я отвечу рядом с вами.','Если хотите тишины, подсказки можно выключить в настройках питомца.'];return {text:messages[ambientCount%messages.length],label:'Задать вопрос →',ask:true};
 }
 function hint(now){
  if(pet.dataset.hints!=='1'||now-openedAt<(greeted?20000:10000)||now-lastHint<180000||busy||drag||hover||!panel.hidden||!tip.hidden||obscured||fieldFocused()||document.querySelector('dialog[open]'))return;
  let shared={};try{shared=JSON.parse(localStorage.getItem(storageKey)||'{}')||{};}catch(_){}
  if(Number(shared.lastHint)>lastHint)lastHint=Number(shared.lastHint);if(Number(shared.hintDay)===Math.floor(now/86400000))hintCount=Math.max(hintCount,Number(shared.hintCount)||0);if(now-lastHint<180000)return;
  const day=Math.floor(now/86400000);if(day!==hintDay){hintDay=day;hintCount=0;}if(hintCount>=8)return;
  const item=greeted?contextualHint():{text:'Привет! Я Клевер. Погладьте меня или нажмите несколько раз — покажу танец. А ещё меня можно поднять и отпустить.',label:'Спросить Клевера →',ask:true};if(!item)return;
  pet.querySelector('[data-tip-text]').textContent=item.text;const link=pet.querySelector('[data-tip-link]');link.textContent=item.label;link.href=item.url||'#companion-panel';link.dataset.ask=item.ask?'1':'';
  lastHint=now;hintCount++;greeted=true;ambientCount++;save();tip.hidden=false;act('wave','happy',1700);positionBubble(tip);tipTimer=setTimeout(dismissTip,12000);
 }
 pet.querySelector('[data-dismiss-tip]').addEventListener('click',dismissTip);
 pet.querySelector('[data-tip-link]').addEventListener('click',event=>{const ask=event.currentTarget.dataset.ask==='1';dismissTip();if(ask){event.preventDefault();if(form.elements.recipe_key.value)form.elements.mode.value='cooking';open(true);}});
 function tick(){
  if(stopped||document.hidden)return;const now=Date.now(),outsideInput=fieldFocused()&&!pet.contains(document.activeElement);
  obscured=!!document.querySelector('dialog[open]')||(outsideInput&&innerWidth<=650);pet.classList.toggle('is-obscured',obscured);
  held=obscured||!panel.hidden||reading||(hover&&now<hoverUntil)||!!drag||(keyboardMode&&pet.contains(document.activeElement));
  if(!obscured&&!airborne)hint(now);if(!panel.hidden)positionBubble(panel);if(!tip.hidden)positionBubble(tip);if(!speech.hidden)positionBubble(speech);refreshRest();movementTimer=setTimeout(tick,180);
 }
 function resize(){size={w:pet.offsetWidth,h:pet.offsetHeight};route=[];place({x:position.x,y:airborne||drag?position.y:limits().bottom});if(!panel.hidden)positionBubble(panel);if(!tip.hidden)positionBubble(tip);}
 function stop(){stopped=true;save();clearTimeout(movementTimer);cancelAnimationFrame(animationFrame);clearTimeout(pollTimer);clearTimeout(tipTimer);clearTimeout(speechTimer);}
 document.addEventListener('pointerdown',event=>{keyboardMode=false;lastActivity=Date.now();if(!pet.contains(event.target))close();},{passive:true});
 document.addEventListener('pointermove',()=>{lastActivity=Date.now();},{passive:true});
 document.addEventListener('keydown',event=>{keyboardMode=true;lastActivity=Date.now();if(event.key==='Escape'){dismissTip();if(!panel.hidden){close(true);event.stopPropagation();}}});
 document.addEventListener('focusin',event=>{if(!pet.contains(event.target))close();});
 document.addEventListener('sf-assistant-job',event=>{if(!stopped)watch(event.detail);});
 document.addEventListener('visibilitychange',()=>{clearTimeout(movementTimer);cancelAnimationFrame(animationFrame);clearTimeout(pollTimer);route=[];if(!document.hidden&&!stopped){lastTick=performance.now();roamAt=Date.now()+700;tick();animationFrame=requestAnimationFrame(animate);if(activeId)poll(activeId,panel.hidden);}});
 window.addEventListener('resize',resize);window.visualViewport?.addEventListener('resize',resize);window.visualViewport?.addEventListener('scroll',resize);

 window.addEventListener('pagehide',stop);window.addEventListener('pageshow',event=>{if(event.persisted){stopped=false;lastTick=performance.now();tick();animationFrame=requestAnimationFrame(animate);if(activeId)poll(activeId,panel.hidden);}});
 function motionPreference(){
  const limited=motionReduced();sprite.reduce(limited);pet.dataset.reduced=String(limited);route=[];actionUntil=0;sprite.set('idle');face('content');
  const choice=pet.querySelector('[data-pet-motion-choice]'),note=pet.querySelector('[data-pet-motion-note]');choice.hidden=!reduced.matches;note.hidden=!limited&&pet.dataset.roaming==='1';note.textContent=limited?'На устройстве включено уменьшение движения.':'Самостоятельные прогулки выключены в настройках питомца.';
  chatButton.title=limited?'Движения ограничены устройством. Открыть Клевера':'Спросить Клевера';
  choice.textContent=motionOverride?'Учитывать настройку устройства':'Разрешить движения питомца';choice.setAttribute('aria-pressed',String(motionOverride));roamAt=Date.now()+400;
 }
 pet.querySelector('[data-pet-motion-choice]').addEventListener('click',()=>{motionOverride=!motionOverride;save();motionPreference();});
 reduced.addEventListener('change',motionPreference);motionPreference();
 resize();const b=limits();place({x:Number.isFinite(stored.x)?b.left+clamp(stored.x,0,1)*(b.right-b.left):b.right-30,y:b.bottom});
 lock(busy);refreshRest();if(activeId){updateState('pending');if(!document.querySelector('[data-assistant]'))pollTimer=setTimeout(()=>poll(activeId,true),4000);}
 tick();animationFrame=requestAnimationFrame(animate);
})();
