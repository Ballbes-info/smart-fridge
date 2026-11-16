/* Expiry summary links open their native disclosure before scrolling. */
(() => {
  const reveal = () => {
    if (location.hash !== '#daily-expired') return;
    const group = document.getElementById('daily-expired');
    if (group) { group.open = true; group.scrollIntoView({block:'start'}); }
  };
  document.querySelectorAll('a[href="#daily-expired"]').forEach(link => link.addEventListener('click', () => {
    const group = document.getElementById('daily-expired');
    if (group) group.open = true;
  }));
  window.addEventListener('hashchange', reveal);
  reveal();
})();

/* Missing or unavailable recipe photos leave the text card intact. */
document.querySelectorAll('[data-daily-photo]').forEach(photo => {
  const hide = () => { photo.hidden = true; };
  photo.addEventListener('error', hide, {once:true});
  if (photo.complete && !photo.naturalWidth) hide();
});
