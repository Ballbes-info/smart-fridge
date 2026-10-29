/* Password visibility is per-field and never writes passwords to storage. */
for (const button of document.querySelectorAll('[data-password-toggle]')) {
  const input=document.getElementById(button.getAttribute('aria-controls'));
  if (!input) continue;
  button.addEventListener('click',()=>{
    const show=input.type==='password';input.type=show?'text':'password';
    button.setAttribute('aria-pressed',String(show));
    button.setAttribute('aria-label',`${show?'Скрыть':'Показать'} пароль: ${button.dataset.fieldLabel}`);
    button.querySelector('[data-eye-open]').hidden=show;
    button.querySelector('[data-eye-closed]').hidden=!show;
  });
  for (const event of ['keyup','keydown']) input.addEventListener(event,e=>{
    const hint=input.closest('.password-field').querySelector('[data-caps-lock]');
    hint.hidden=!(e.getModifierState && e.getModifierState('CapsLock'));
  });
  input.addEventListener('blur',()=>input.closest('.password-field').querySelector('[data-caps-lock]').hidden=true);
}
for (const confirmation of document.querySelectorAll('[data-password-match]')) {
  const original=document.getElementById(confirmation.dataset.passwordMatch);
  if (!original) continue;
  const validate=()=>confirmation.setCustomValidity(confirmation.value && original.value!==confirmation.value?'Пароли не совпадают.':'');
  original.addEventListener('input',validate);confirmation.addEventListener('input',validate);
}
window.addEventListener('pagehide',()=>{
  for(const button of document.querySelectorAll('[data-password-toggle]')) {
    const input=document.getElementById(button.getAttribute('aria-controls'));
    if(input)input.type='password';button.setAttribute('aria-pressed','false');
    button.setAttribute('aria-label',`Показать пароль: ${button.dataset.fieldLabel}`);
    button.querySelector('[data-eye-open]').hidden=false;button.querySelector('[data-eye-closed]').hidden=true;
  }
});
