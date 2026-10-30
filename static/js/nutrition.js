/* Progressive enhancements. Every operation also works as a native HTML form. */
(() => {
  const form=document.querySelector('[data-nutrition-form]');
  if(form){
    const preview=form.querySelector('[data-nutrition-preview]');
    const get=name=>form.elements.namedItem(name)?.value||'';
    const update=()=>{
      const qty=Number(get('quantity').replace(',','.')),basis=get('basis'),unit=get('unit');
      const denominator=basis==='100g'||basis==='100ml'?100:basis==='serving'&&(unit==='g'||unit==='ml')?Number(get('basis_quantity').replace(',','.')):1;
      const ratio=basis==='total'?1:qty/denominator;
      if(!Number.isFinite(ratio)||ratio<=0){preview.textContent='';return;}
      const labels={calories:'ккал',protein:'г белка',fat:'г жиров',carbs:'г углеводов'};
      preview.textContent='На съеденное: '+Object.entries(labels).map(([key,label])=>{
        const value=get(key);return value===''?'— '+label:(Number(value.replace(',','.'))*ratio).toLocaleString('ru-RU',{maximumFractionDigits:1})+' '+label;
      }).join(' · ');
    };form.addEventListener('input',update);update();
    const date=form.elements.namedItem('day');if(date)date.max=new Intl.DateTimeFormat('sv-SE',{timeZone:'Europe/Moscow'}).format(new Date());
  }
  const plan=document.querySelector('.nt-plan-form');
  const slots=[...document.querySelectorAll('[data-plan-slot]')];
  const apply=slot=>{if(!plan)return;plan.elements.namedItem('day').value=slot.dataset.day;plan.elements.namedItem('meal').value=slot.dataset.meal;plan.elements.namedItem('revision').value=slot.dataset.revision;};
  slots.forEach(slot=>slot.addEventListener('click',()=>{
    try{sessionStorage.setItem('nutrition-plan-slot',JSON.stringify({day:slot.dataset.day,meal:slot.dataset.meal}));}catch(_){}
    apply(slot);if(plan)plan.querySelector('button').focus({preventScroll:true});
  }));
  if(plan){
    try{const saved=JSON.parse(sessionStorage.getItem('nutrition-plan-slot'));const slot=slots.find(s=>s.dataset.day===saved?.day&&s.dataset.meal===saved?.meal);if(slot)apply(slot);}catch(_){}
    const refresh=()=>{const slot=slots.find(s=>s.dataset.day===plan.elements.namedItem('day').value&&s.dataset.meal===plan.elements.namedItem('meal').value);plan.elements.namedItem('revision').value=slot?.dataset.revision||'0';};
    plan.addEventListener('change',refresh);refresh();
  }
  const mode=document.getElementById('goal-mode');if(mode)mode.addEventListener('change',()=>{const details=document.querySelector('.nt-parameters');if(mode.value==='auto'&&details)details.open=true;});
  // Prevent double clicks without disabling successful submit controls or their values.
  document.querySelectorAll('.nt-app form[method="post"]').forEach(form=>form.addEventListener('submit',event=>{
    if(form.dataset.submitting){event.preventDefault();return;}form.dataset.submitting='1';
  }));
  window.addEventListener('pageshow',()=>document.querySelectorAll('.nt-app form').forEach(f=>delete f.dataset.submitting));
})();
