/* One quota map shared by chat and pet; each request keeps its own selected mode. */
(()=>{
 const states=new WeakMap(),roots=[...document.querySelectorAll('[data-ai-modes]')];
 const descriptions={standard:'Повседневные вопросы и рецепты; больше доступных обращений.',advanced:'Сложные вопросы и рецепты с несколькими ограничениями; отдельный меньший лимит.'};
 function selected(root){return root.querySelector('[name=ai_mode]')?.value||'standard';}
 function quota(root){return states.get(root)?.[selected(root)]||{remaining:0,limit:0};}
 function render(root){const q=quota(root);root.querySelectorAll('[data-ai-remaining],[data-remaining],[data-pet-remaining]').forEach(n=>n.textContent=q.remaining);root.querySelectorAll('[data-ai-limit]').forEach(n=>n.textContent=q.limit);root.querySelectorAll('[data-ai-description]').forEach(n=>n.textContent=descriptions[selected(root)]||'');root.dispatchEvent(new CustomEvent('sf-ai-mode-change',{detail:{ai_mode:selected(root),quota:q}}));}
 function replace(kind,quotas){if(!quotas||typeof quotas!=='object')return;for(const root of roots)if(root.dataset.aiModes===kind){const old=states.get(root)||{};for(const mode of ['standard','advanced']){const q=quotas[mode];if(q&&Number.isFinite(q.remaining)&&Number.isFinite(q.limit))old[mode]={...q};}states.set(root,old);render(root);}}
 function updateRemaining(kind,mode,value){if(!Number.isFinite(value))return;for(const root of roots)if(root.dataset.aiModes===kind){const all=states.get(root)||{};if(all[mode])all[mode].remaining=Math.max(0,value);render(root);}}
 function reserve(kind,mode){for(const root of roots)if(root.dataset.aiModes===kind){const all=states.get(root)||{},q=all[mode];if(q)q.remaining=Math.max(0,q.remaining-1);render(root);}}
 for(const root of roots){let data={};try{data=JSON.parse(root.dataset.aiQuotas||'{}')}catch(_){}states.set(root,data);root.querySelector('[name=ai_mode]')?.addEventListener('change',()=>render(root));render(root);}
 window.SmartFridgeAIModes={selected,quota,replace,reserve,updateRemaining};
})();
