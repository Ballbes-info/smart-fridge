/* Only protect edits to the storage form. No inventory operations. */
(() => {
  const form = document.querySelector('.storage-form:not(.storage-delete-form)');
  if (!form) return;
  const initiallyDisabled = new Set(form.querySelectorAll('button[type="submit"]:disabled'));
  let changed = Boolean(form.querySelector('[data-storage-errors]'));
  let submitting = false;
  form.addEventListener('input', () => { changed = true; });
  form.addEventListener('change', () => { changed = true; });
  form.addEventListener('submit', () => {
    submitting = true;
    form.querySelectorAll('button[type="submit"]').forEach(button => { button.disabled = true; });
  });
  window.addEventListener('beforeunload', event => {
    if (!changed || submitting) return;
    event.preventDefault(); event.returnValue = '';
  });
  window.addEventListener('pageshow', () => {
    submitting = false;
    form.querySelectorAll('button[type="submit"]').forEach(button => { button.disabled = initiallyDisabled.has(button); });
  });
})();
