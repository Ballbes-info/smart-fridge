(() => {
  const form=document.querySelector('[data-stock-operation]');
  if(form){
    form.querySelector('[data-use-all]')?.addEventListener('click',event=>{
      form.elements.quantity.value=event.currentTarget.dataset.quantity;
      form.elements.unit.value=event.currentTarget.dataset.unit;
      form.elements.quantity.focus();
    });
    form.addEventListener('submit',()=>{form.querySelector('[type=submit]').disabled=true;});
    window.addEventListener('pageshow',()=>{form.querySelector('[type=submit]').disabled=false;});
  }
  const edit=document.querySelector('[data-product-form]');
  if(edit && !document.querySelector('.product-scanner')){
    let dirty=false,submitted=false;
    edit.addEventListener('input',()=>{dirty=true;});
    edit.addEventListener('change',()=>{
      dirty=true;const warning=edit.querySelector('[data-expiry-warning]');
      warning.hidden=Boolean(edit.elements.expiry_date.value);
    });
    edit.addEventListener('submit',()=>{submitted=true;edit.querySelector('[type=submit]').disabled=true;});
    window.addEventListener('beforeunload',event=>{if(dirty&&!submitted){event.preventDefault();event.returnValue='';}});
    window.addEventListener('pageshow',()=>{submitted=false;edit.querySelector('[type=submit]').disabled=false;});
  }
  const error=document.querySelector('[data-product-errors]');if(error)error.focus();
})();
