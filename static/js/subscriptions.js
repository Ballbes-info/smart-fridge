(() => {
  const period = document.querySelector('[data-billing-period]');
  const planForms = document.querySelectorAll('[data-plan-form]');
  const money = value => value.toLocaleString('ru-RU');

  function updatePrices() {
    const year = period?.querySelector('input:checked')?.value === 'year';
    planForms.forEach(form => {
      const card = form.closest('.billing-card');
      const seatsInput = form.elements.extra_seats;
      const extra = Math.max(0, Math.min(95, Math.trunc(Number(seatsInput.value) || 0)));
      const base = Number(form.dataset[year ? 'year' : 'month']);
      const seats = extra * Number(form.dataset.seat) * (year ? 10 : 1);
      form.elements.interval.value = year ? 'year' : 'month';
      card.querySelector('[data-price]').textContent = money(base + seats);
      card.querySelector('[data-unit]').textContent = year ? '/год' : '/месяц';
      card.querySelector('[data-saving]').textContent = year
        ? '12 месяцев по цене 10'
        : `Первый месяц — ${money(Math.round(base * 0.7) + seats)} ₽`;
    });
    const note = document.querySelector('[data-period-note]');
    if (note) note.textContent = year
      ? 'Один платёж за год · два месяца в подарок'
      : 'Первый месяц — со скидкой 30%';
  }
  period?.addEventListener('change', updatePrices);
  planForms.forEach(form => form.addEventListener('input', updatePrices));
  window.addEventListener('pageshow', updatePrices);
  updatePrices();
})();
