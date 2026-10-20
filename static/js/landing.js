(() => {
  const buttons = document.querySelectorAll('[data-billing]');
  const format = value => new Intl.NumberFormat('ru-RU').format(Number(value));
  buttons.forEach(button => button.addEventListener('click', () => {
    const yearly = button.dataset.billing === 'year';
    buttons.forEach(item => item.setAttribute('aria-pressed', String(item === button)));
    document.querySelectorAll('.plan-price strong').forEach(price => {
      price.textContent = format(yearly ? price.dataset.year : price.dataset.month);
    });
    document.querySelectorAll('.plan-period').forEach(period => period.textContent = yearly ? '/год' : '/месяц');
    document.querySelectorAll('[data-equivalent]').forEach(note => {
      note.textContent = yearly ? `≈${format(note.dataset.equivalent)} ₽/мес. · одним платежом за год` : 'Оплата за месяц';
    });
    document.getElementById('billing-note').textContent = yearly ? 'За год списывается полная указанная сумма. Экономия — около 16,7%.' : 'При годовой оплате — 12 месяцев по цене 10.';
  }));
})();
