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
  let method='manual';
  const methodInputs=[...document.querySelectorAll('[name=entry_method]')];
  function setMethod(value){
    method=value;scanner.hidden=value==='manual';
    if(value==='manual')void stopCamera();
    document.querySelector('.product-entry-layout').classList.toggle('is-manual',value==='manual');
    if(value!=='manual')toggleScanner(true);
    $('#label-scanner-title').textContent=value==='gigachat'?'Распознать через GigaChat':'Сканировать этикетку';
    $('[data-method-hint]').textContent=value==='gigachat'?'Загрузите фото и нажмите «Распознать через GigaChat». Один снимок — одно обращение.':'OCR читает текст на сервере. Сложные этикетки могут потребовать исправлений.';
    $('[data-giga-quota]').hidden=value!=='gigachat';
    $('[data-scan-privacy]').textContent=value==='gigachat'?'При нажатии кнопки выбранное фото отправляется в GigaChat. После обработки сервер запрашивает удаление загруженного файла.':'Фото OCR обрабатывается на сервере SmartFridge. В Open Food Facts передаётся только штрихкод.';
    $('[data-recognize]').textContent=value==='gigachat'?'Распознать через GigaChat':'Заполнить по фото';
    if(value==='gigachat' && $('[data-giga-quota]').dataset.configured!=='yes')status('GigaChat ещё не подключён: нужен ключ API в .env на сервере.',true);
    else status('');
  }
  methodInputs.forEach(input=>input.addEventListener('change',()=>{if(!busy)setMethod(input.value);}));

  let dirty = Boolean(document.querySelector('[data-product-errors]')), submitting = false;
  const touched = new Set(), autoValues = new Map(), resolved = new Map();
  const defaults = {quantity:'1',unit:'pcs',shelf_unit:'days'};
  form.addEventListener('input', event => { if (event.target.name) touched.add(event.target.name); });
  form.addEventListener('change', event => { if (event.target.name) touched.add(event.target.name); });
  const errorSummary = document.querySelector('[data-product-errors]');
  if (errorSummary) errorSummary.focus();
  form.addEventListener('input', () => { dirty = true; });
  form.addEventListener('change', () => { dirty = true; updateDates(); });
  form.addEventListener('submit', () => { if(field('ocr_used').value==='yes')field('ocr_confirm').value='yes';submitting = true; form.querySelector('[type=submit]').disabled = true; stopCamera(); });
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
  const labels = {name:'Название',brand:'Марка',manufacturer:'Производитель',quantity:'Количество',unit:'Единица',ingredients:'Состав',calories:'Калории, ккал',protein:'Белки, г',fat:'Жиры, г',carbs:'Углеводы, г',nutrition_basis:'Основание КБЖУ',portion:'Порция',manufactured_date:'Дата изготовления',expiry_date:'Годен до',shelf_value:'Срок хранения',shelf_unit:'Единица срока',other_info:'Другие сведения',barcode:'Штрихкод',amount:'Количество'};
  const friendly = {g:'г',kg:'кг',ml:'мл',l:'л',pcs:'шт.',pack:'уп.','100g':'На 100 г','100ml':'На 100 мл',portion:'На порцию',days:'дней',months:'месяцев',years:'лет'};
  const images = []; let active = 0, crop = null, stream = null, cameraGeneration = 0, photoSequence = 0, busy = false, proposals = {}, recognized = new Map();
  const canvas = $('[data-crop-canvas]'), ctx = canvas.getContext('2d');
  function status(text, error = false) { $('[data-scan-status]').textContent = text; $('[data-scan-status]').classList.toggle('is-error',error); }
  function syncBusy(value) {
    busy=value;methodInputs.forEach(input=>{input.disabled=value;});
    scanner.querySelectorAll('button,input[type=file]').forEach(button=>{button.disabled=value;});
    $('[data-recognize]').textContent=value?'Распознаём…':method==='gigachat'?'Распознать через GigaChat':'Заполнить по фото';
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
  async function addFiles(files,append=false) {
    if(busy)return;
    const firstNew=append?images.length:0;const pending=[];const problems=[];
    for(const file of [...files]){
      if((append?images.length:0)+pending.length>=6){problems.push('Можно добавить до 6 снимков.');break;}
      if(file.size>8*1024*1024){problems.push('Снимок больше 8 МБ.');continue;}
      if(!['image/jpeg','image/png','image/webp'].includes(file.type)){problems.push('Поддерживаются JPEG, PNG и WebP.');continue;}
      const url=URL.createObjectURL(file);
      try{
        const image=new Image();image.src=url;await image.decode();
        if(image.naturalWidth*image.naturalHeight>20000000)throw new Error('Снимок больше 20 мегапикселей.');
        if((append?images.length:0)+pending.length>=6){problems.push('Можно добавить до 6 снимков.');continue;}
        const ratio=Math.min(1,2400/Math.max(image.naturalWidth,image.naturalHeight));const source=document.createElement('canvas');source.width=Math.round(image.naturalWidth*ratio);source.height=Math.round(image.naturalHeight*ratio);source.getContext('2d').drawImage(image,0,0,source.width,source.height);pending.push({id:'photo-'+(++photoSequence),canvas:source});dirty=true;
      }catch(error){problems.push(error.message==='Снимок больше 20 мегапикселей.'?error.message:'Не удалось прочитать снимок. Попробуйте другое фото.');}finally{URL.revokeObjectURL(url);}
    }
    if(pending.length){
      if(!append){images.length=0;recognized.clear();resolved.clear();}
      images.push(...pending);
    }
    select(Math.max(0,images.length-1));status(problems.join(' '),Boolean(problems.length));
    if(pending.length && method==='ocr')void scan(images.length-firstNew>1,firstNew);
  }
  $('[data-photo-input]').addEventListener('change',event=>{addFiles(event.target.files);event.target.value='';});
  $('[data-photo-extra]').addEventListener('change',event=>{addFiles(event.target.files,true);event.target.value='';});
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
  $('[data-rotate-photo]').addEventListener('click',()=>{const image=images[active];if(!image)return;const rotated=document.createElement('canvas');rotated.width=image.canvas.height;rotated.height=image.canvas.width;const c=rotated.getContext('2d');c.translate(rotated.width,0);c.rotate(Math.PI/2);c.drawImage(image.canvas,0,0);image.canvas=rotated;for(const key of recognized.keys())if(key.startsWith(image.id+':'))recognized.delete(key);crop=null;paint();cropInputs(null);renderResults();});
  $('[data-remove-photo]').addEventListener('click',()=>{const removed=images.splice(active,1)[0];if(removed) for(const key of recognized.keys()) if(key.startsWith(removed.id+':'))recognized.delete(key);select(Math.max(0,active-1));});
  async function stopCamera(){cameraGeneration++;if(stream){stream.getTracks().forEach(track=>track.stop());stream=null;}const video=$('video');video.srcObject=null;$('.product-camera').hidden=true;}
  $('[data-start-camera]').addEventListener('click',async()=>{
    if(!navigator.mediaDevices?.getUserMedia){status('Камера доступна на HTTPS или localhost. Можно загрузить готовое фото.',true);return;}
    try{await stopCamera();const generation=cameraGeneration;const candidate=await navigator.mediaDevices.getUserMedia({video:{facingMode:{ideal:'environment'}},audio:false});if(generation!==cameraGeneration||document.hidden){candidate.getTracks().forEach(track=>track.stop());return;}stream=candidate;$('video').srcObject=stream;$('.product-camera').hidden=false;status('Наведите камеру на этикетку и сделайте снимок.');}catch{status('Нет доступа к камере. Проверьте разрешение браузера или загрузите фото.',true);}
  });
  $('[data-stop-camera]').addEventListener('click',stopCamera);
  $('[data-take-photo]').addEventListener('click',()=>{const video=$('video');if(!video.videoWidth)return;const shot=document.createElement('canvas');shot.width=video.videoWidth;shot.height=video.videoHeight;shot.getContext('2d').drawImage(video,0,0);shot.toBlob(async blob=>{if(blob)await addFiles([new File([blob],'camera.jpg',{type:'image/jpeg'})]);stopCamera();},'image/jpeg',.92);});
  document.addEventListener('visibilitychange',()=>{if(document.hidden)stopCamera();});window.addEventListener('pagehide',stopCamera);
  let scores = {}, conflicts = {};
  function aggregate() {
    proposals={}; scores={}; conflicts={}; const values = new Map();
    const chosenCode=resolved.get('barcode');
    const results=[...recognized.values()].filter(result=>!chosenCode || !result.proposals?.barcode || result.proposals.barcode.padStart(14,'0')===chosenCode.padStart(14,'0'));
    const amounts=new Map();
    const addAmount=(option,score)=>{if(!option?.quantity || !option?.unit)return;const key=option.quantity+'|'+option.unit;amounts.set(key,{...option,score:Math.max(amounts.get(key)?.score||0,score)});};
    for(const result of results){addAmount(result.proposals,Math.min(result.confidence?.quantity||0,result.confidence?.unit||0));for(const option of result.amount_options || [])addAmount(option,0);}
    for(const result of results) for(const [key,value] of Object.entries(result.proposals || {})) {
      if((!labels[key] && key!=='catalog_source') || !field(key))continue;
      if(!values.has(key))values.set(key,new Map());
      const variants=values.get(key), score=Number(result.confidence?.[key] || 0);
      variants.set(String(value),Math.max(variants.get(String(value)) || 0,score));
      for(const alternative of result.alternatives?.[key] || [])variants.set(String(alternative),0);
    }
    for(const [key,variants] of values) {
      if(variants.size===1) { const [value,score]=[...variants][0];proposals[key]=value;scores[key]=score; }
      else {
        conflicts[key]=[...variants.keys()];
        if(variants.has(resolved.get(key))) { proposals[key]=resolved.get(key);scores[key]=1; }
      }
    }
    if(amounts.size>1){
      delete conflicts.quantity;delete conflicts.unit;conflicts.amount=[...amounts.keys()];
      const selected=amounts.get(resolved.get('amount'));
      if(selected){proposals.quantity=selected.quantity;proposals.unit=selected.unit;scores.quantity=scores.unit=1;}
      else{delete proposals.quantity;delete proposals.unit;scores.quantity=scores.unit=0;}
    }
    // Never automatically mix numbers belonging to different bases/portions.
    if(conflicts.barcode && !resolved.get('barcode'))for(const key of Object.keys(scores))scores[key]=0;
    if(conflicts.nutrition_basis) for(const key of ['calories','protein','fat','carbs','nutrition_basis'])scores[key]=0;
    if((conflicts.amount && !resolved.get('amount')) || conflicts.quantity || conflicts.unit) scores.quantity=scores.unit=0;
    if(conflicts.shelf_value || conflicts.shelf_unit)scores.shelf_value=scores.shelf_unit=0;
    // Withdraw earlier automatic values once a later photo contradicts them.
    for(const [key,value] of autoValues){
      const dependent=(conflicts.barcode && !resolved.get('barcode')) || (conflicts.nutrition_basis && ['calories','protein','fat','carbs'].includes(key)) || (((conflicts.amount && !resolved.get('amount')) || conflicts.quantity||conflicts.unit) && ['quantity','unit'].includes(key));
      if((!(key in proposals)||dependent) && !touched.has(key) && field(key).value===value){
        field(key).value=({quantity:'',unit:'pcs',shelf_unit:'days'})[key]||'';autoValues.delete(key);
        field('ocr_confirm').value='';updateDates();
      }
    }
  }
  function renderResults(){
    aggregate();const texts=[],dates=[],warnings=[];
    for(const result of recognized.values()){texts.push(result.text);dates.push(...result.dates || []);warnings.push(...result.warnings || []);}
    const area=$('.product-ocr-results');area.hidden=!recognized.size;$('[data-ocr-text]').value=texts.join('\n\n');const container=$('[data-ocr-proposals]');container.replaceChildren();
    for(const [key,value] of Object.entries(proposals)){
      if(key==='catalog_source')continue;
      const label=document.createElement('label');label.className='product-proposal';const check=document.createElement('input');check.type='checkbox';check.dataset.proposal=key;
      check.checked=scores[key]>=.8 && ((!touched.has(key) && (!field(key).value || autoValues.get(key)===field(key).value)) || (!touched.has(key) && (Object.hasOwn(defaults,key) && field(key).value===defaults[key])));
      const span=document.createElement('span'),title=document.createElement('strong');title.textContent=labels[key];span.append(title,document.createTextNode(friendly[value]||value));
      if(scores[key]<.8){const hint=document.createElement('small');hint.textContent='Проверьте на упаковке';span.append(hint);label.classList.add('needs-review');}
      label.append(check,span);container.append(label);
    }
    for(const [key,variants] of Object.entries(conflicts)) {
      const label=document.createElement('label');label.className='product-ocr-conflict';label.append(document.createTextNode(labels[key]+': на снимках разные значения'));
      const select=document.createElement('select');const empty=document.createElement('option');empty.value='';empty.textContent='Выберите значение';select.append(empty);
      for(const value of variants){const option=document.createElement('option');option.value=value;option.textContent=key==='amount'?value.split('|')[0]+' '+(friendly[value.split('|')[1]]||value.split('|')[1]):friendly[value]||value;select.append(option);}
      select.value=resolved.get(key)||'';select.addEventListener('change',()=>{resolved.set(key,select.value);if(key==='amount' && select.value){const [quantity,unit]=select.value.split('|');field('quantity').value=quantity;field('unit').value=unit;touched.add('quantity');touched.add('unit');confirmNeeded();renderResults();}else autoFill();});label.append(select);container.append(label);
    }
    renderCatalog();renderDiagnostics();
    const details=$('[data-result-details]');if(details)details.open=Object.keys(conflicts).length>0 || Object.entries(scores).some(([key,score])=>score<.8 && key!=='catalog_source');
    $('[data-apply-ocr]').disabled=!Object.keys(proposals).length;
    $('[data-ocr-warnings]').textContent=[...new Set(warnings)].join(' ');
    $('[data-found-dates]').textContent=dates.length?'Найденные даты: '+[...new Set(dates)].map(value=>new Intl.DateTimeFormat('ru-RU',{timeZone:'UTC'}).format(new Date(value+'T00:00:00Z'))).join(', ')+'. Проверьте назначение даты на упаковке.':'';
  }
  function confirmNeeded(){
    field('ocr_used').value='yes';const confirm=form.querySelector('[data-ocr-confirm]');confirm.hidden=false;field('ocr_confirm').value='';form.querySelector('[type=submit]').textContent='Подтвердить и сохранить';dirty=true;updateDates();
  }
  function autoFill(){
    aggregate();const keys=new Set(Object.keys(proposals).filter(key=>scores[key]>=.8 && !touched.has(key) &&
      (!field(key).value || autoValues.get(key)===field(key).value || (Object.hasOwn(defaults,key) && field(key).value===defaults[key]))));
    for(const [a,b] of [['quantity','unit'],['shelf_value','shelf_unit']])if(keys.has(a)!==keys.has(b)){keys.delete(a);keys.delete(b);}
    // A shelf life alone is useful evidence, but cannot be submitted without a manufacture date.
    if(!field('manufactured_date').value && !keys.has('manufactured_date')){keys.delete('shelf_value');keys.delete('shelf_unit');}
    if(keys.has('nutrition_basis') && field('nutrition_basis').value!==proposals.nutrition_basis && ['calories','protein','fat','carbs'].some(key=>field(key).value && !keys.has(key)))keys.delete('nutrition_basis');
    const basis=keys.has('nutrition_basis')?proposals.nutrition_basis:field('nutrition_basis').value;
    if(!basis || (proposals.nutrition_basis && basis!==proposals.nutrition_basis))for(const key of ['calories','protein','fat','carbs'])keys.delete(key);
    for(const key of keys){field(key).value=proposals[key];autoValues.set(key,proposals[key]);}
    if(keys.size)confirmNeeded();renderResults();return keys.size;
  }
  async function jsonRequest(url,body,timeout){
    const response=await fetch(url,{method:'POST',body,signal:AbortSignal.timeout(timeout)});
    if(!response.headers.get('content-type')?.includes('application/json'))throw new Error('Сеанс истёк или сервер недоступен. Сохраните введённые данные и обновите страницу.');
    const data=await response.json();if(!response.ok)throw new Error(data.error||'Не удалось прочитать снимок.');return data;
  }
  function renderCatalog(){
    const container=$('[data-catalog-source]');container.replaceChildren();delete container.dataset.saved;container.hidden=true;
    const choices=$('[data-barcode-choices]');choices.replaceChildren();const codes=new Set();
    for(const result of recognized.values()){
      for(const code of result.barcodes || [])codes.add(code);
      if(result.proposals?.barcode)codes.add(result.proposals.barcode);
      if(result.catalog_state==='saved' && !container.dataset.saved) {container.hidden=false;container.dataset.saved='yes';container.append(document.createTextNode('Найдены данные из ваших продуктов. '));}
      if(result.catalog_url && /^https:\/\/world\.openfoodfacts\.org\/product\/[0-9]{8,14}$/.test(result.catalog_url)){
        if(container.querySelector('a'))continue;container.hidden=false;
        const link=document.createElement('a');link.href=result.catalog_url;link.target='_blank';link.rel='noopener noreferrer';link.textContent='Карточка Open Food Facts';container.append(link,document.createTextNode(' · '));
        const license=document.createElement('a');license.href='https://opendatacommons.org/licenses/odbl/1-0/';license.target='_blank';license.rel='noopener noreferrer';license.textContent='ODbL';container.append(license);
      }
    }
    if(codes.size>1){const hint=document.createElement('p');hint.textContent='Выберите штрихкод нужного продукта';choices.append(hint);for(const code of codes){const button=document.createElement('button');button.type='button';button.className='workspace-secondary-button';button.textContent=code;button.disabled=busy;button.addEventListener('click',()=>lookupCode(code));choices.append(button);}}
  }
  function renderDiagnostics(){
    const output=$('[data-scan-diagnostics]');const lines=[];
    for(const result of recognized.values()){
      if(result.engine)lines.push('Распознавание: '+(result.engine==='gigachat'?'GigaChat':'Tesseract'));
      const states={saved:'сохранённый продукт',found:'карточка найдена',cached:'карточка из кеша',not_found:'карточки нет',unavailable:'каталог недоступен',limited:'лимит обращений',offline:'без обращения в интернет'};
      if(result.catalog_state && states[result.catalog_state])lines.push('Справочник: '+states[result.catalog_state]);
      if(result.catalog_fields)lines.push('Поля внешней карточки: '+(result.catalog_fields.map(key=>labels[key]||key).join(', ')||'нет'));
      if(result.refined_fields?.length)lines.push('Повторно прочитаны: '+result.refined_fields.map(key=>labels[key]||key).join(', '));
    }
    output.textContent=[...new Set(lines)].join('\n');output.parentElement.hidden=!lines.length;
    const code=field('barcode').value;if(code && document.activeElement!==$('[data-catalog-code]'))$('[data-catalog-code]').value=code;
    else if(!code && !recognized.size)$('[data-catalog-code]').value='';
  }
  async function lookupCode(code){
    if(busy)return;syncBusy(true);status('Ищем продукт…');
    try{
      const body=new FormData();body.append('barcode',code);body.append('csrf_token',field('csrf_token').value);
      const data=await jsonRequest('/products/catalog',body,8000);recognized.set('catalog:manual',data);resolved.set('barcode',code);autoFill();
      const messages={not_found:'В каталоге нет карточки. Можно прочитать данные с этикетки.',unavailable:'Каталог недоступен. Можно прочитать данные с этикетки.',limited:'Каталог временно занят. Можно прочитать данные с этикетки.',offline:'Поиск без интернета: прочитайте этикетку или выберите сохранённый продукт.'};
      status(messages[data.catalog_state] || 'Данные внесены в форму. Проверьте упаковку перед сохранением.');
    }catch(error){status(error.name==='TimeoutError'?'Каталог не ответил. Сканирование этикетки остаётся доступным.':error.message,true);}
    finally{syncBusy(false);renderResults();}
  }
  $('[data-lookup-code]').addEventListener('click',()=>lookupCode($('[data-catalog-code]').value.trim()||field('barcode').value));
  $('[data-catalog-code]').addEventListener('keydown',event=>{if(event.key==='Enter'){event.preventDefault();lookupCode(event.target.value.trim()||field('barcode').value);}});
  field('barcode').addEventListener('input',()=>{field('catalog_source').value='';touched.add('catalog_source');});
  async function recognizeImage(image,rect){
    const output=document.createElement('canvas');output.width=Math.round(rect.w);output.height=Math.round(rect.h);output.getContext('2d').drawImage(image.canvas,rect.x,rect.y,rect.w,rect.h,0,0,output.width,output.height);
    const blob=await new Promise(resolve=>output.toBlob(resolve,'image/jpeg',.96));if(!blob)throw new Error('Не удалось подготовить снимок.');
    const key=image.id+':'+[rect.x,rect.y,rect.w,rect.h].map(Math.round).join(',');
    function body(phase){const data=new FormData();data.append('image',blob,'label.jpg');data.append('csrf_token',field('csrf_token').value);data.append('phase',phase);return data;}
    if(method==='gigachat'){
      const data=await jsonRequest('/products/gigachat',body('text'),240000);
      recognized.delete(key+':quick');recognized.set(key,data);resolved.clear();
      if(data.quota){const q=$('[data-giga-quota]');q.dataset.remaining=data.quota.remaining;q.textContent=`GigaChat: осталось ${data.quota.remaining} из ${data.quota.limit} фото в этом месяце.`;}
      return;
    }
    let code='';
    try{
      const identified=await jsonRequest('/products/ocr',body('identify'),8000);
      if(identified.barcodes?.length>1){recognized.set(key+':quick',identified);renderResults();throw new Error('На снимке несколько продуктов. Выберите один штрихкод или выделите одну упаковку.');}
      if(identified.proposals?.barcode){code=identified.proposals.barcode;recognized.set(key+':quick',identified);autoFill();if(!identified.proposals.quantity && !touched.has('quantity') && field('quantity').value===defaults.quantity)field('quantity').value='';status('Карточка обработана. Читаем данные с упаковки…');}
    }catch(error){if(error.message.startsWith('На снимке несколько продуктов'))throw error;}
    // A second side of the same package can omit the barcode.
    if(!code){const known=new Set([...recognized.values()].map(r=>r.proposals?.barcode).filter(Boolean));if(known.size===1)code=[...known][0];}
    const requestBody=body('text');if(code)requestBody.append('barcode',code);
    const data=await jsonRequest('/products/ocr',requestBody,95000);
    recognized.delete(key+':quick');recognized.set(key,data);resolved.clear();
  }
  async function scan(all=false,first=0){
    if(!images[active]||busy || method==='manual')return;
    if(method==='gigachat'){
      const quota=$('[data-giga-quota]');
      if(quota.dataset.configured!=='yes'){status('GigaChat ещё не подключён: нужен ключ API в .env на сервере.',true);return;}
      const needed=all?images.length-first:1;
      if(Number(quota.dataset.remaining)<needed){status('Недостаточно обращений GigaChat. Выберите один снимок или используйте OCR.',true);return;}
    }
    syncBusy(true);await stopCamera();
    const jobs=all?images.slice(first).map(image=>({image,rect:{x:0,y:0,w:image.canvas.width,h:image.canvas.height}})):
      [{image:images[active],rect:crop||{x:0,y:0,w:images[active].canvas.width,h:images[active].canvas.height}}];
    const problems=[];let done=0;
    for(const [index,job] of jobs.entries()) {
      status(all?`Читаем фото ${index+1} из ${jobs.length}…`:'Читаем этикетку…');
      try{await recognizeImage(job.image,job.rect);done++;renderResults();}
      catch(error){problems.push(error.name==='TimeoutError'?'Сканер не успел обработать фото. Попробуйте выделить область.':error.message);}
    }
    syncBusy(false);const filled=autoFill();
    status(problems.length?`${done?'Часть снимков прочитана. ':''}${problems.join(' ')}`:
      filled?'Данные внесены в форму. Проверьте их перед сохранением.':'Фото прочитано. Проверьте предложенные значения.',Boolean(problems.length));
  }
  $('[data-recognize]').addEventListener('click',()=>scan());
  $('[data-recognize-all]').addEventListener('click',()=>scan(true));
  $('[data-apply-ocr]').addEventListener('click',()=>{
    const chosen=[...scanner.querySelectorAll('[data-proposal]:checked')];if(!chosen.length){status('Выберите значения для переноса.',true);return;}
    const keys=new Set(chosen.map(input=>input.dataset.proposal));
    for(const [a,b] of [['quantity','unit'],['shelf_value','shelf_unit']]) { if(proposals[a]&&proposals[b]&&keys.has(a)!==keys.has(b)){status('Количество и единицу, а также срок хранения и его единицу нужно переносить вместе.',true);return;} }
    const hasNutrients=['calories','protein','fat','carbs'].some(key=>keys.has(key));
    if(hasNutrients&&proposals.nutrition_basis&&!keys.has('nutrition_basis')&&field('nutrition_basis').value!==proposals.nutrition_basis){status('Выберите основание КБЖУ вместе со значениями, чтобы не смешать данные на 100 г, 100 мл и порцию.',true);return;}
    if(keys.has('nutrition_basis') && field('nutrition_basis').value && field('nutrition_basis').value!==proposals.nutrition_basis && ['calories','protein','fat','carbs'].some(key=>field(key).value && !keys.has(key))){status('При смене основания КБЖУ замените все заполненные значения таблицы или очистите их.',true);return;}
    for(const input of chosen){const target=field(input.dataset.proposal);target.value=proposals[input.dataset.proposal];touched.add(input.dataset.proposal);const details=target.closest('details');if(details)details.open=true;}
    confirmNeeded();const missingBasis=['calories','protein','fat','carbs'].some(key=>field(key).value)&&!field('nutrition_basis').value;status(missingBasis?'Проверьте КБЖУ: укажите в форме, на 100 г, 100 мл или порцию приведены значения.':'Значения перенесены. Проверьте форму и подтвердите данные этикетки.');
  });
})();
