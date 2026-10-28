document.addEventListener('DOMContentLoaded', () => {
  const toggle = document.querySelector('[data-nav-toggle]');
  const close = () => { document.body.classList.remove('nav-open'); if (toggle) { toggle.setAttribute('aria-expanded', 'false'); toggle.setAttribute('aria-label', 'Открыть меню'); } };
  toggle?.addEventListener('click', () => { const open = document.body.classList.toggle('nav-open'); toggle.setAttribute('aria-expanded', String(open)); toggle.setAttribute('aria-label', open ? 'Закрыть меню' : 'Открыть меню'); });
  document.querySelector('[data-nav-close]')?.addEventListener('click', close);
  document.addEventListener('keydown', e => { if (e.key === 'Escape') close(); });
  document.querySelector('[data-select-all]')?.addEventListener('change', event => {
    document.querySelectorAll('input[name="members"]').forEach(input => { input.checked = event.target.checked; });
  });
});
