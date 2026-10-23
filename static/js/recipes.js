/* Failed remote photos leave the local illustration visible, including offline. */
for (const image of document.querySelectorAll('[data-recipe-photo]')) {
  const hide = () => { image.hidden = true; };
  image.addEventListener('error', hide, {once: true});
  if (image.complete && image.naturalWidth === 0) hide();
}
