/* Image processing is local in the browser; only the chosen crop goes to our OCR endpoint. */
(() => {
  const form = document.querySelector('[data-product-form]');
  if (!form) return;
  const scanner = document.querySelector('.product-scanner');
  const scannerToggle = scanner.querySelector('[data-toggle-scanner]');
  const scannerBody = scanner.querySelector('.product-scanner-body');
  function toggleScanner(open) {
    scannerBody.hidden = !open; scannerToggle.setAttribute('aria-expanded', String(open));
    scanner.classList.toggle('is-collapsed', !open);
  }
  toggleScanner(!window.matchMedia('(max-width:1200px)').matches);
  scannerToggle.addEventListener('click', () => toggleScanner(scannerBody.hidden));
  const $ = selector => scanner.querySelector(selector);
  const field = name => form.elements.namedItem(name);
  let dirty = Boolean(document.querySelector('[data-product-errors]')), submitting = false;
  const errorSummary = document.querySelector('[data-product-errors]');
  if (errorSummary) errorSummary.focus();
  form.addEventListener('input', () => { dirty = true; });
  form.addEventListener('change', () => { dirty = true; updateDates(); });
  form.addEventListener('submit', () => { submitting = true; form.querySelector('[type=submit]').disabled = true; stopCamera(); });
  window.addEventListener('beforeunload', event => { if (dirty && !submitting) { event.preventDefault(); event.returnValue = ''; } });
  window.addEventListener('pageshow', () => { submitting = false; form.querySelector('[type=submit]').disabled = false; });
  const dayFormat = new Intl.DateTimeFormat('ru-RU', { timeZone:'Europe/Moscow',year:'numeric',month:'2-digit',day:'2-digit' });
  function todayKey() { const parts = dayFormat.formatToParts(new Date()); const get = t => parts.find(p=>p.type===t).value; return `${get('year')}-${get('month')}-${get('day')}`; }
  function updateDates() {
    form.querySelector('[data-pack-hint]').hidden = field('unit').value !== 'pack';
    const warning = form.querySelector('[data-expiry-warning]'); let expiry = field('expiry_date').value;
    const manufactured = field('manufactured_date').value, length = Number(field('shelf_value').value);
    if (!expiry && manufactured && Number.isInteger(length) && length > 0) {
      const [year,month,day] = manufactured.split('-').map(Number); let calculated;
      if (field('shelf_unit').value === 'days') calculated = new Date(Date.UTC(year,month-1,day+length));
      else {
        const first = new Date(Date.UTC(year,month-1+length*(field('shelf_unit').value==='years'?12:1),1));
        calculated = new Date(Date.UTC(first.getUTCFullYear(),first.getUTCMonth(),Math.min(day,new Date(Date.UTC(first.getUTCFullYear(),first.getUTCMonth()+1,0)).getUTCDate())));
      }
      if (!Number.isNaN(calculated.getTime())) expiry = calculated.toISOString().slice(0,10);
    }
    warning.hidden = Boolean(expiry && expiry >= todayKey()); warning.classList.toggle('is-expired',Boolean(expiry && expiry < todayKey()));
    warning.textContent = expiry ? 'Указанный срок уже истёк. Проверьте дату перед сохранением.' : 'Срок не указан';
  }
  updateDates(); form.addEventListener('input', updateDates);
  const storage = field('storage_id');
  storage.addEventListener('change', () => {
    const summary = form.querySelector('.product-storage-choice summary');
    summary.replaceChildren(document.createTextNode('Место: '+storage.selectedOptions[0].textContent));
    const change = document.createElement('span');change.textContent='Изменить';summary.append(change);
    const listLink=form.querySelector('[data-product-list-link]');const url=new URL(listLink.href);url.searchParams.set('storage_id',storage.value);listLink.href=url;
  });
  const labels = {name:'Название',brand:'Марка',manufacturer:'Производитель',quantity:'Количество',unit:'Единица',ingredients:'Состав',calories:'Калории, ккал',protein:'Белки, г',fat:'Жиры, г',carbs:'Углеводы, г',nutrition_basis:'Основание КБЖУ',portion:'Порция',manufactured_date:'Дата изготовления',expiry_date:'Годен до',shelf_value:'Срок хранения',shelf_unit:'Единица срока'};
  const friendly = {g:'г',kg:'кг',ml:'мл',l:'л',pcs:'шт.',pack:'уп.','100g':'На 100 г','100ml':'На 100 мл',portion:'На порцию',days:'дней',months:'месяцев',years:'лет'};
  const images = []; let active = 0, crop = null, stream = null, cameraGeneration = 0, photoSequence = 0, busy = false, proposals = {}, recognized = new Map();
  const canvas = $('[data-crop-canvas]'), ctx = canvas.getContext('2d');
  function status(text, error = false) { $('[data-scan-status]').textContent = text; $('[data-scan-status]').classList.toggle('is-error',error); }
  function syncBusy(value) {
    busy=value;
    scanner.querySelectorAll('button,input[type=file]').forEach(button=>{button.disabled=value;});
    $('[data-recognize]').textContent=value?'Распознаём…':'Распознать текст';
    scanner.setAttribute('aria-busy',String(value));
  }
  function cropInputs(rect) {
    const r=rect||{x:0,y:0,w:canvas.width,h:canvas.height};
    for(const axis of ['x','y','w','h']) {const input=$(`[data-crop-${axis}]`);input.value=Math.round(r[axis]);input.max=['x','w'].includes(axis)?canvas.width:canvas.height;}
  }
  function paint() {
    if(!images[active]) return;
    const image=images[active].canvas;canvas.width=image.width;canvas.height=image.height;ctx.drawImage(image,0,0);
    if(crop){ctx.fillStyle='rgba(36,85,58,.18)';ctx.fillRect(crop.x,crop.y,crop.w,crop.h);ctx.strokeStyle='#214d35';ctx.lineWidth=Math.max(2,canvas.width/300);ctx.strokeRect(crop.x,crop.y,crop.w,crop.h);}
  }
  function select(index) {
    active=index;crop=null;$('.product-crop-area').hidden=!images.length;
    const tabs=$('[data-photo-tabs]');tabs.replaceChildren();images.forEach((image,i)=>{const button=document.createElement('button');button.type='button';button.textContent='Фото '+(i+1);button.setAttribute('aria-pressed',String(i===active));button.addEventListener('click',()=>select(i));tabs.append(button);});
    if(images.length){paint();cropInputs(null);} else ctx.clearRect(0,0,canvas.width,canvas.height);
    renderResults();
  }
  async function addFiles(files) {
    if(busy)return;
    const problems=[];
    for(const file of [...files]){
      if(images.length>=6){problems.push('Можно добавить до 6 снимков.');break;}
      if(file.size>8*1024*1024){problems.push('Снимок больше 8 МБ.');continue;}
      if(!['image/jpeg','image/png','image/webp'].includes(file.type)){problems.push('Поддерживаются JPEG, PNG и WebP.');continue;}
      const url=URL.createObjectURL(file);
      try{
        const image=new Image();image.src=url;await image.decode();
        if(image.naturalWidth*image.naturalHeight>20000000)throw new Error('Снимок больше 20 мегапикселей.');
        if(images.length>=6){problems.push('Можно добавить до 6 снимков.');continue;}
        const ratio=Math.min(1,2000/Math.max(image.naturalWidth,image.naturalHeight));const source=document.createElement('canvas');source.width=Math.round(image.naturalWidth*ratio);source.height=Math.round(image.naturalHeight*ratio);source.getContext('2d').drawImage(image,0,0,source.width,source.height);images.push({id:'photo-'+(++photoSequence),canvas:source});dirty=true;
      }catch(error){problems.push(error.message==='Снимок больше 20 мегапикселей.'?error.message:'Не удалось прочитать снимок. Попробуйте другое фото.');}finally{URL.revokeObjectURL(url);}
    }
    select(Math.max(0,images.length-1));status(problems.join(' '),Boolean(problems.length));
  }
  $('[data-photo-input]').addEventListener('change',event=>{addFiles(event.target.files);event.target.value='';});
  $('[data-photo-capture]').addEventListener('change',event=>{addFiles(event.target.files);event.target.value='';});
  const zone=$('[data-drop-zone]');
  zone.addEventListener('dragover',event=>{event.preventDefault();zone.classList.add('is-dragging');});
  zone.addEventListener('dragleave',()=>zone.classList.remove('is-dragging'));
  zone.addEventListener('drop',event=>{event.preventDefault();zone.classList.remove('is-dragging');addFiles(event.dataTransfer.files);});
  let start=null;
  function point(event){const bounds=canvas.getBoundingClientRect();return {x:Math.max(0,Math.min(canvas.width,(event.clientX-bounds.left)*canvas.width/bounds.width)),y:Math.max(0,Math.min(canvas.height,(event.clientY-bounds.top)*canvas.height/bounds.height))};}
  canvas.addEventListener('pointerdown',event=>{if(busy||!images.length)return;start=point(event);canvas.setPointerCapture(event.pointerId);});
  canvas.addEventListener('pointermove',event=>{if(!start)return;const p=point(event);crop={x:Math.min(start.x,p.x),y:Math.min(start.y,p.y),w:Math.abs(start.x-p.x),h:Math.abs(start.y-p.y)};paint();});
  canvas.addEventListener('pointerup',()=>{if(!start)return;start=null;if(crop&&(crop.w<30||crop.h<30))crop=null;paint();cropInputs(crop);});
  canvas.addEventListener('pointercancel',()=>{start=null;crop=null;paint();cropInputs(null);});
  $('[data-clear-crop]').addEventListener('click',()=>{crop=null;paint();cropInputs(null);});
  $('[data-apply-crop]').addEventListener('click',()=>{
    const r=Object.fromEntries(['x','y','w','h'].map(axis=>[axis,Number($(`[data-crop-${axis}]`).value)]));
    if(Object.values(r).some(n=>!Number.isFinite(n)||n<0)||r.w<30||r.h<30||r.x+r.w>canvas.width||r.y+r.h>canvas.height){status('Область должна находиться внутри снимка и быть не меньше 30 × 30 пикселей.',true);return;}
    crop=r;paint();status('Область выделена.');
  });
  $('[data-rotate-photo]').addEventListener('click',()=>{const image=images[active];if(!image)return;const rotated=document.createElement('canvas');rotated.width=image.canvas.height;rotated.height=image.canvas.width;const c=rotated.getContext('2d');c.translate(rotated.width,0);c.rotate(Math.PI/2);c.drawImage(image.canvas,0,0);image.canvas=rotated;crop=null;paint();cropInputs(null);});
  $('[data-remove-photo]').addEventListener('click',()=>{const removed=images.splice(active,1)[0];if(removed) for(const key of recognized.keys()) if(key.startsWith(removed.id+':'))recognized.delete(key);select(Math.max(0,active-1));});
  async function stopCamera(){cameraGeneration++;if(stream){stream.getTracks().forEach(track=>track.stop());stream=null;}const video=$('video');video.srcObject=null;$('.product-camera').hidden=true;}
  $('[data-start-camera]').addEventListener('click',async()=>{
    if(!navigator.mediaDevices?.getUserMedia){status('Камера доступна на HTTPS или localhost. Можно загрузить готовое фото.',true);return;}
    try{await stopCamera();const generation=cameraGeneration;const candidate=await navigator.mediaDevices.getUserMedia({video:{facingMode:{ideal:'environment'}},audio:false});if(generation!==cameraGeneration||document.hidden){candidate.getTracks().forEach(track=>track.stop());return;}stream=candidate;$('video').srcObject=stream;$('.product-camera').hidden=false;status('Наведите камеру на этикетку и сделайте снимок.');}catch{status('Нет доступа к камере. Проверьте разрешение браузера или загрузите фото.',true);}
  });
  $('[data-stop-camera]').addEventListener('click',stopCamera);
  $('[data-take-photo]').addEventListener('click',()=>{const video=$('video');if(!video.videoWidth)return;const shot=document.createElement('canvas');shot.width=video.videoWidth;shot.height=video.videoHeight;shot.getContext('2d').drawImage(video,0,0);shot.toBlob(async blob=>{if(blob)await addFiles([new File([blob],'camera.jpg',{type:'image/jpeg'})]);stopCamera();},'image/jpeg',.92);});
  document.addEventListener('visibilitychange',()=>{if(document.hidden)stopCamera();});window.addEventListener('pagehide',stopCamera);
  function renderResults(){
    proposals={};const texts=[],dates=[];for(const result of recognized.values()){Object.assign(proposals,result.proposals);texts.push(result.text);dates.push(...result.dates);}
    const area=$('.product-ocr-results');area.hidden=!recognized.size;$('[data-ocr-text]').value=texts.join('\n\n');const container=$('[data-ocr-proposals]');container.replaceChildren();
    for(const [key,value] of Object.entries(proposals)){if(!labels[key]||!field(key))continue;const label=document.createElement('label');label.className='product-proposal';const check=document.createElement('input');check.type='checkbox';check.dataset.proposal=key;check.checked=!field(key).value;const span=document.createElement('span'),title=document.createElement('strong');title.textContent=labels[key];span.append(title,document.createTextNode(friendly[value]||value));label.append(check,span);container.append(label);}
    $('[data-apply-ocr]').disabled=!Object.keys(proposals).length;
    $('[data-found-dates]').textContent=dates.length?'Найденные даты: '+[...new Set(dates)].map(value=>new Intl.DateTimeFormat('ru-RU',{timeZone:'UTC'}).format(new Date(value+'T00:00:00Z'))).join(', ')+'. Проверьте на упаковке, что означает каждая дата.':'';
  }
  $('[data-recognize]').addEventListener('click',async()=>{
    const image=images[active];if(!image||busy)return;const rect=crop||{x:0,y:0,w:image.canvas.width,h:image.canvas.height};const output=document.createElement('canvas');output.width=Math.round(rect.w);output.height=Math.round(rect.h);output.getContext('2d').drawImage(image.canvas,rect.x,rect.y,rect.w,rect.h,0,0,output.width,output.height);
    syncBusy(true);status('Распознаём текст этикетки…');
    try{const blob=await new Promise(resolve=>output.toBlob(resolve,'image/jpeg',.95));if(!blob)throw new Error('Не удалось подготовить снимок.');const body=new FormData();body.append('image',blob,'label.jpg');body.append('csrf_token',field('csrf_token').value);const response=await fetch('/products/ocr',{method:'POST',body,signal:AbortSignal.timeout(45000)});if(!response.headers.get('content-type')?.includes('application/json'))throw new Error('Сеанс истёк или сервер недоступен. Обновите страницу, предварительно сохранив введённые данные.');const data=await response.json();if(!response.ok)throw new Error(data.error||'Не удалось распознать снимок.');recognized.set(image.id+':'+[rect.x,rect.y,rect.w,rect.h].map(Math.round).join(','),data);renderResults();status('Текст распознан. Выберите поля и проверьте значения.');}
    catch(error){status(error.name==='TimeoutError'?'Распознавание не ответило вовремя. Попробуйте меньшую область.':error.message,true);}
    finally{syncBusy(false);renderResults();}
  });
  $('[data-apply-ocr]').addEventListener('click',()=>{
    const chosen=[...scanner.querySelectorAll('[data-proposal]:checked')];if(!chosen.length){status('Выберите значения для переноса.',true);return;}
    const keys=new Set(chosen.map(input=>input.dataset.proposal));
    for(const [a,b] of [['quantity','unit'],['shelf_value','shelf_unit']]) { if(proposals[a]&&proposals[b]&&keys.has(a)!==keys.has(b)){status('Количество и единицу, а также срок хранения и его единицу нужно переносить вместе.',true);return;} }
    const hasNutrients=['calories','protein','fat','carbs'].some(key=>keys.has(key));
    if(hasNutrients&&proposals.nutrition_basis&&!keys.has('nutrition_basis')&&field('nutrition_basis').value!==proposals.nutrition_basis){status('Выберите основание КБЖУ вместе со значениями, чтобы не смешать данные на 100 г, 100 мл и порцию.',true);return;}
    for(const input of chosen){const target=field(input.dataset.proposal);target.value=proposals[input.dataset.proposal];const details=target.closest('details');if(details)details.open=true;}
    field('ocr_used').value='yes';const confirm=form.querySelector('[data-ocr-confirm]');confirm.hidden=false;confirm.querySelector('input').required=true;confirm.querySelector('input').checked=false;dirty=true;updateDates();const missingBasis=['calories','protein','fat','carbs'].some(key=>field(key).value)&&!field('nutrition_basis').value;status(missingBasis?'Проверьте КБЖУ: укажите в форме, на 100 г, 100 мл или порцию приведены значения.':'Значения перенесены. Проверьте форму и подтвердите данные этикетки.');
  });
})();
