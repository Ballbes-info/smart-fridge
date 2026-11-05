(() => {
 const pet=document.querySelector('.sf-companion');if(!pet)return;
 const button=pet.querySelector('.sf-companion-open'),panel=pet.querySelector('.sf-companion-panel');
 const current=pet.querySelector('[data-pet-current]'),copy=pet.querySelector('[data-pet-copy]'),dot=pet.querySelector('.sf-companion-dot');
 let timer,jobId='',failures=0,stopped=false;
 function close(focus=false){panel.hidden=true;button.setAttribute('aria-expanded','false');if(focus)button.focus();}
 function state(status){
  pet.dataset.state=status;current.hidden=false;
  copy.textContent=status==='pending'?'Готовлю ответ. Он сохранится, пока вы занимаетесь другими делами.':status==='used'?'Ответ готов. Можно вернуться к разговору.':'Не удалось получить ответ. Можно повторить вопрос в разговоре.';
  dot.hidden=status!=='used';button.setAttribute('aria-label','Клевер: открыть ИИ-помощника'+(status==='used'?'. Ответ готов':''));
 }
 async function poll(){
  if(!jobId||stopped||document.hidden)return;
  const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),10000);
  try{
   const response=await fetch('/assistant/jobs/'+encodeURIComponent(jobId),{cache:'no-store',headers:{Accept:'application/json'},signal:controller.signal});
   if(!response.ok||response.redirected){jobId='';return;}
   const data=await response.json();state(data.status);failures=0;
   if(data.status==='pending')timer=setTimeout(poll,8000);else jobId='';
  }catch(_){if(++failures<4)timer=setTimeout(poll,15000);}finally{clearTimeout(timeout);}
 }
 function watch(detail){
  if(!detail||!['chat','cooking'].includes(detail.mode))return;
  current.href='/home/assistant/'+detail.mode;state(detail.status);clearTimeout(timer);
  jobId=detail.status==='pending'?detail.id:'';failures=0;
  // The chat already polls its own job; elsewhere the companion only checks status.
  if(jobId&&!document.querySelector('[data-assistant]'))timer=setTimeout(poll,4000);
 }
 button.addEventListener('click',()=>{if(panel.hidden){panel.hidden=false;button.setAttribute('aria-expanded','true');}else close();});
 pet.querySelector('[data-pet-close]').addEventListener('click',()=>close(true));
 pet.querySelector('[data-pet-hide]').addEventListener('click',async event=>{
  const control=event.currentTarget;control.disabled=true;const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),15000);
  try{
   const body=new FormData();body.set('csrf_token',panel.dataset.csrf);
   const response=await fetch(panel.dataset.hideUrl,{method:'POST',body,headers:{Accept:'application/json'},signal:controller.signal});
   if(!response.ok||response.redirected)throw new Error();const data=await response.json();if(!data.ok)throw new Error();
   stopped=true;clearTimeout(timer);pet.remove();document.querySelector('#main-content')?.focus({preventScroll:true});
  }catch(_){pet.querySelector('[data-pet-status]').textContent='Не удалось скрыть. Повторите или выключите в настройках.';control.disabled=false;}
  finally{clearTimeout(timeout);}
 });
 document.addEventListener('pointerdown',event=>{if(!pet.contains(event.target))close();});
 document.addEventListener('keydown',event=>{if(event.key==='Escape'&&!panel.hidden){close(true);event.stopPropagation();}});
 document.addEventListener('focusin',event=>{if(!pet.contains(event.target))close();});
 document.addEventListener('sf-assistant-job',event=>{if(!stopped)watch(event.detail);});
 document.addEventListener('visibilitychange',()=>{clearTimeout(timer);if(!document.hidden&&jobId&&!document.querySelector('[data-assistant]'))poll();});
 window.addEventListener('pagehide',()=>{stopped=true;clearTimeout(timer);});
 window.addEventListener('pageshow',event=>{if(event.persisted){stopped=false;if(jobId&&!document.querySelector('[data-assistant]'))poll();}});
 if(pet.dataset.activeJob)watch({id:pet.dataset.activeJob,mode:pet.dataset.activeMode,status:'pending'});
})();
