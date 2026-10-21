document.querySelectorAll('[data-plan-form]').forEach(form => {
  const card = form.closest('.billing-card');
  function update() {
    const year = form.elements.interval.value === 'year';
    const extra = Math.max(0, Math.min(95, Number(form.elements.extra_seats.value) || 0));
    const base = Number(form.dataset[year ? 'year' : 'month']);
    const seats = extra * Number(form.dataset.seat) * (year ? 10 : 1);
    card.querySelector('[data-price]').textContent = (base + seats).toLocaleString('ru-RU');
    card.querySelector('[data-unit]').textContent = year ? '/год' : '/месяц';
    card.querySelector('[data-saving]').textContent = year
      ? '12 месяцев по цене 10'
      : `Первый месяц — ${Math.round(base * 0.7) + seats} ₽`;
  }
  form.addEventListener('input', update);
  update();
});
