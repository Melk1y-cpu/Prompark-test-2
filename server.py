"""Development server and same-origin contact endpoint. Python 3.12+, no dependencies."""
import json
import logging
import os
import re
import smtplib
import ssl
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from email.message import EmailMessage
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parent
EMAIL = re.compile(r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,63}\Z")
AREAS = {'Нужна консультация', '2 000–3 000 м²', '3 000–5 000 м²', '5 000–6 500 м²'}
MAX_BODY = 12_000
LOG = logging.getLogger('elgrant')


def is_email(value):
    return len(value) <= 254 and bool(EMAIL.fullmatch(value))


@dataclass(frozen=True)
class MailConfig:
    host: str = ''
    port: int = 587
    mode: str = 'starttls'
    sender: str = ''
    recipient: str = ''
    username: str = ''
    password: str = ''

    @classmethod
    def from_environment(cls):
        mode = os.environ.get('SMTP_MODE', 'starttls')
        return cls(
            host=os.environ.get('SMTP_HOST', '').strip(),
            port=int(os.environ.get('SMTP_PORT', '465' if mode == 'ssl' else '587')),
            mode=mode,
            sender=os.environ.get('SMTP_FROM_EMAIL', '').strip(),
            recipient=os.environ.get('CONTACT_TO_EMAIL', '').strip(),
            username=os.environ.get('SMTP_USERNAME', ''),
            password=os.environ.get('SMTP_PASSWORD', ''),
        )

    @property
    def ready(self):
        return (bool(self.host) and self.mode in {'ssl', 'starttls'}
                and 1 <= self.port <= 65535 and is_email(self.sender)
                and is_email(self.recipient) and bool(self.username) == bool(self.password))


def validate(payload):
    if not isinstance(payload, dict):
        raise ValueError('Ожидались данные формы.')
    fields = {}
    for key, limit in {'name': 100, 'contact': 254, 'area': 40,
                       'activity': 150, 'details': 1500, 'website': 200}.items():
        value = payload.get(key, '')
        if not isinstance(value, str) or len(value) > limit:
            raise ValueError('Проверьте длину и формат полей.')
        value = value.strip()
        if any(ord(char) < 32 and char not in '\n\r\t' for char in value):
            raise ValueError('В полях есть недопустимые символы.')
        fields[key] = value
    if not fields['name'] or not fields['contact']:
        raise ValueError('Укажите имя и телефон или email.')
    contact = fields['contact']
    phone = re.fullmatch(r'\+?[\d ()-]+', contact)
    if not is_email(contact) and not (phone and 10 <= len(re.sub(r'\D', '', contact)) <= 15):
        raise ValueError('Укажите корректный телефон или email.')
    if fields['area'] not in AREAS:
        raise ValueError('Выберите площадь участка из списка.')
    if payload.get('consent') is not True:
        raise ValueError('Для отправки необходимо согласие на обработку данных.')
    return fields


def send_email(config, fields):
    message = EmailMessage()
    message['Subject'] = 'Новая заявка с сайта «Элгрант Пестрецы»'
    message['From'] = config.sender
    message['To'] = config.recipient
    if is_email(fields['contact']):
        message['Reply-To'] = fields['contact']
    message.set_content(
        'Заявка с сайта промышленного парка «Элгрант Пестрецы»\n\n'
        f"Имя: {fields['name']}\nКонтакт: {fields['contact']}\n"
        f"Площадь: {fields['area']}\nДеятельность: {fields['activity'] or 'Не указана'}\n\n"
        f"Требования и сообщение:\n{fields['details'] or 'Не указаны'}\n\n"
        'Пользователь подтвердил согласие на обработку данных для ответа на обращение.\n'
    )
    context = ssl.create_default_context()
    if config.mode == 'ssl':
        connection = smtplib.SMTP_SSL(config.host, config.port, timeout=15, context=context)
    else:
        connection = smtplib.SMTP(config.host, config.port, timeout=15)
    try:
        if config.mode == 'starttls':
            connection.ehlo()
            connection.starttls(context=context)
            connection.ehlo()
        if config.username:
            connection.login(config.username, config.password)
        refused = connection.send_message(message)
        if refused:
            raise smtplib.SMTPRecipientsRefused(refused)
    finally:
        # QUIT can fail after DATA was accepted. Do not turn acceptance into an
        # apparent failure that would prompt the visitor to send a duplicate.
        connection.close()


class RateLimiter:
    def __init__(self, attempts=5, window=600):
        self.attempts, self.window = attempts, window
        self.clients = OrderedDict()
        self.lock = threading.Lock()

    def allow(self, client):
        now = time.monotonic()
        with self.lock:
            expired = [key for key, times in self.clients.items() if times[-1] <= now - self.window]
            for key in expired:
                del self.clients[key]
            times = [stamp for stamp in self.clients.pop(client, []) if stamp > now - self.window]
            allowed = len(times) < self.attempts
            if allowed:
                times.append(now)
            self.clients[client] = times
            if len(self.clients) > 10_000:
                self.clients.popitem(last=False)
            return allowed


def make_handler(config, deliver=send_email, public_origin=''):
    limiter = RateLimiter()

    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(ROOT), **kwargs)

        def log_message(self, format, *args):
            # Do not log query strings, form contents, client IPs or credentials.
            pass

        def end_headers(self):
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Referrer-Policy', 'strict-origin-when-cross-origin')
            self.send_header('X-Frame-Options', 'DENY')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'")
            super().end_headers()

        def respond(self, status, message, **extra):
            body = json.dumps({'message': message, **extra}, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            if status == 429:
                self.send_header('Retry-After', '600')
            self.end_headers()
            self.wfile.write(body)

        def static_allowed(self):
            path = unquote(urlsplit(self.path).path)
            if path in {'/', '/index.html', '/styles.css', '/app.js', '/privacy.html'}:
                return True
            target = (ROOT / path.lstrip('/')).resolve()
            return path.startswith('/assets/') and target.is_relative_to(ROOT / 'assets') and target.is_file()

        def do_GET(self):
            if not self.static_allowed():
                self.send_error(404)
                return
            super().do_GET()

        def do_HEAD(self):
            if not self.static_allowed():
                self.send_error(404)
                return
            super().do_HEAD()

        def do_POST(self):
            if self.path != '/api/contact':
                self.respond(404, 'Страница не найдена.')
                return
            # Forwarded headers are intentionally not trusted. Behind a proxy,
            # set PUBLIC_ORIGIN and enforce IP limits at the trusted proxy too.
            origin = self.headers.get('Origin')
            expected = public_origin.rstrip('/') or 'http://' + self.headers.get('Host', '')
            if origin and origin != expected:
                self.respond(403, 'Отправьте заявку со страницы сайта.')
                return
            if not limiter.allow(self.client_address[0]):
                self.respond(429, 'Слишком много обращений. Попробуйте через 10 минут или позвоните нам.')
                return
            if self.headers.get_content_type() != 'application/json':
                self.respond(415, 'Неподдерживаемый формат запроса.')
                return
            lengths = self.headers.get_all('Content-Length', [])
            if len(lengths) != 1 or not lengths[0].isdigit() or self.headers.get('Transfer-Encoding'):
                self.respond(400, 'Не удалось прочитать запрос.')
                return
            size = int(lengths[0])
            if size > MAX_BODY:
                self.respond(413, 'Сообщение слишком длинное.')
                return
            try:
                self.connection.settimeout(20)
                raw = self.rfile.read(size)
                if len(raw) != size:
                    raise ValueError('Неполный запрос.')
                fields = validate(json.loads(raw))
            except (ValueError, UnicodeDecodeError, TimeoutError):
                self.respond(400, 'Проверьте поля формы, телефон или email и согласие на обработку данных.')
                return
            if fields['website']:
                self.respond(400, 'Не удалось отправить заявку. Позвоните в отдел продаж.')
                return
            if not config.ready:
                self.respond(503, 'Отправка временно недоступна. Позвоните нам: +7 (993) 402-09-25.')
                return
            try:
                deliver(config, fields)
            except (smtplib.SMTPException, OSError):
                LOG.warning('Contact delivery failed; inspect mail service configuration.')
                self.respond(502, 'Не удалось подтвердить отправку. Позвоните нам или повторите попытку позже.')
                return
            self.respond(200, 'Спасибо! Заявка отправлена в отдел продаж. Мы свяжемся с вами по указанному контакту.')

    return Handler


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
    config = MailConfig.from_environment()
    if not config.ready:
        LOG.warning('Mail is not configured. The site works, but contact requests return 503.')
    server = ThreadingHTTPServer((os.environ.get('HOST', '127.0.0.1'), int(os.environ.get('PORT', '3000'))),
                                 make_handler(config, public_origin=os.environ.get('PUBLIC_ORIGIN', '')))
    server.daemon_threads = True
    LOG.info('Site server started.')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
