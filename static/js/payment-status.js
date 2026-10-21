(() => {
  const element = document.getElementById('payment-status');
  if (!element) return;
  let attempts = 0;
  async function poll() {
    if (++attempts > 30) return;
    try {
      const response = await fetch(element.dataset.url, {credentials: 'same-origin', headers: {Accept: 'application/json'}});
      if (response.ok) {
        const result = await response.json();
        if (!['created', 'pending'].includes(result.status)) { window.location.reload(); return; }
      }
    } catch (_) { /* The manual status link remains available. */ }
    window.setTimeout(poll, 5000);
  }
  window.setTimeout(poll, 5000);
})();
