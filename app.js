const menu = document.querySelector('.menu-toggle');
const nav = document.querySelector('.navigation');
function closeMenu() { menu.setAttribute('aria-expanded', 'false'); menu.setAttribute('aria-label', 'Открыть меню'); nav.classList.remove('is-open'); }
menu.addEventListener('click', () => {
  const open = menu.getAttribute('aria-expanded') !== 'true';
  menu.setAttribute('aria-expanded', String(open));
  menu.setAttribute('aria-label', open ? 'Закрыть меню' : 'Открыть меню');
  nav.classList.toggle('is-open', open);
});
nav.querySelectorAll('a').forEach(link => link.addEventListener('click', closeMenu));
document.addEventListener('keydown', e => { if (e.key === 'Escape') closeMenu(); });
document.addEventListener('click', e => { if (!e.target.closest('.header')) closeMenu(); });
window.matchMedia('(min-width: 1051px)').addEventListener('change', closeMenu);
const dialog = document.querySelector('#plan-dialog');
document.querySelector('.plan-zoom').addEventListener('click', () => dialog.showModal());
dialog.querySelector('.dialog-close').addEventListener('click', () => dialog.close());
dialog.addEventListener('click', e => { if (e.target !== dialog) return; const r = dialog.getBoundingClientRect(); if (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom) dialog.close(); });
document.querySelector('[data-area]').addEventListener('click', e => { document.querySelector('#area').value = e.currentTarget.dataset.area; });
const form = document.querySelector('#request-form');
const fields = document.querySelector('#request-fields');
const submit = form.querySelector('[type="submit"]');
const submitLabel = form.querySelector('.submit-label');
const status = document.querySelector('#form-status');
const nameInput = form.elements.name;
const contactInput = form.elements.contact;
const emailDelivery = form.dataset.delivery === 'email';
if (emailDelivery) {
  submitLabel.textContent = 'Подготовить письмо';
  document.querySelector('#form-description').textContent = 'Заполните форму — подготовим письмо в отдел продаж. Затем откройте его в вашем почтовом приложении и отправьте.';
}
let sending = false;
function showStatus(message, state) {
  status.textContent = message;
  status.dataset.state = state;
  status.hidden = false;
}
[nameInput, contactInput].forEach(input => input.addEventListener('input', () => input.setCustomValidity('')));
form.addEventListener('input', () => { if (!sending) status.hidden = true; });
form.addEventListener('submit', async event => {
  event.preventDefault();
  if (sending) return;
  const data = new FormData(form);
  const name = String(data.get('name')).trim();
  const contact = String(data.get('contact')).trim();
  nameInput.setCustomValidity(name ? '' : 'Укажите ваше имя.');
  const validContact = /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(contact)
    || (/^\+?[\d ()-]+$/.test(contact) && contact.replace(/\D/g, '').length >= 10 && contact.replace(/\D/g, '').length <= 15);
  contactInput.setCustomValidity(validContact ? '' : 'Укажите корректный телефон или email.');
  if (!form.reportValidity()) return;
  const payload = {
    name, contact, area: data.get('area'), activity: data.get('activity'),
    details: data.get('details'), website: data.get('website'), consent: data.get('consent') === 'on',
  };
  if (emailDelivery) {
    const body = `Здравствуйте! Интересует размещение в парке «Элгрант Пестрецы».\n\nИмя: ${name}\nКонтакт: ${contact}\nПлощадь: ${payload.area}\nДеятельность: ${payload.activity || 'Не указана'}\n\n${payload.details || 'Прошу уточнить доступные участки и условия размещения.'}\n\nСогласен на обработку этих данных для ответа на обращение.`;
    showStatus('Письмо подготовлено. Откройте его в почтовом приложении и нажмите «Отправить». Если почтовое приложение не настроено, напишите на Iv08dem@yandex.ru или позвоните: +7 (993) 402-09-25.', 'info');
    const emailLink = document.createElement('a');
    emailLink.href = `mailto:${form.dataset.recipient}?subject=${encodeURIComponent('Заявка — Элгрант Пестрецы')}&body=${encodeURIComponent(body)}`;
    emailLink.textContent = 'Открыть почтовое приложение →';
    emailLink.target = '_blank';
    emailLink.rel = 'noopener';
    emailLink.className = 'text-link';
    status.append(emailLink);
    return;
  }
  sending = true;
  submit.disabled = true;
  fields.disabled = true;
  form.setAttribute('aria-busy', 'true');
  submitLabel.textContent = 'Отправляем…';
  showStatus('Отправляем вашу заявку…', 'loading');
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), 60000);
  try {
    const response = await fetch(form.action, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload), signal: controller.signal, credentials: 'same-origin',
    });
    const result = await response.json().catch(() => null);
    if (!response.ok || !result || typeof result.message !== 'string') {
      throw new Error(result?.message || 'Не удалось отправить заявку. Позвоните нам: +7 (993) 402-09-25.');
    }
    form.reset();
    showStatus(result.message, 'success');
  } catch (error) {
    const message = error instanceof TypeError || error.name === 'AbortError'
      ? 'Не удалось подтвердить отправку. Проверьте соединение или уточните получение заявки по телефону: +7 (993) 402-09-25.'
      : error.message;
    showStatus(message, 'error');
  } finally {
    window.clearTimeout(timeout);
    sending = false;
    submit.disabled = false;
    fields.disabled = false;
    form.removeAttribute('aria-busy');
    submitLabel.textContent = 'Отправить заявку';
  }
});
document.querySelector('#year').textContent = new Date().getFullYear();
