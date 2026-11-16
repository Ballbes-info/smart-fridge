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
