import http.client
import json
import smtplib
import threading
import unittest
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from unittest.mock import patch

from server import MAX_BODY, MailConfig, RateLimiter, make_handler, send_email, validate

VALID = {'name': 'Тестовый посетитель', 'contact': '+7 (999) 123-45-67',
         'area': '2 000–3 000 м²', 'activity': 'Производство', 'details': '200 кВт',
         'website': '', 'consent': True}
CONFIG = MailConfig(host='smtp.example.com', sender='site@example.com', recipient='sales@example.com')


@contextmanager
def running(config=CONFIG, deliver=lambda *_: None, public_origin=''):
    server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(config, deliver, public_origin))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def request(port, payload=VALID, path='/api/contact', origin=None, method='POST', headers=None):
    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
    body = json.dumps(payload).encode() if payload is not None else None
    request_headers = {'Content-Type': 'application/json', **(headers or {})}
    if origin:
        request_headers['Origin'] = origin
    connection.request(method, path, body, request_headers)
    response = connection.getresponse()
    data = response.read()
    status = response.status
    connection.close()
    return status, data


class ContactTests(unittest.TestCase):
    def test_accepts_and_delivers_valid_message(self):
        sent = []
        with running(deliver=lambda config, fields: sent.append(fields)) as port:
            status, body = request(port)
        self.assertEqual(status, 200)
        self.assertEqual(sent[0]['details'], '200 кВт')
        self.assertIn('Заявка отправлена', json.loads(body)['message'])

    def test_missing_configuration_never_reports_success(self):
        with running(config=MailConfig()) as port:
            self.assertEqual(request(port)[0], 503)

    def test_mail_failure_keeps_submission_unsuccessful(self):
        def fail(*_):
            raise smtplib.SMTPAuthenticationError(535, b'Authentication failed')
        with running(deliver=fail) as port:
            status, body = request(port)
        self.assertEqual(status, 502)
        self.assertNotIn(b'Authentication failed', body)

    def test_invalid_input_does_not_deliver(self):
        variants = [{'consent': False}, {'name': '   '}, {'contact': '123'},
                    {'contact': 'user@example.com\r\nBcc: someone@example.com'},
                    {'area': 'arbitrary'}, {'details': 'x' * 1501}, {'website': 'spam.example'}]
        for variant in variants:
            with self.subTest(variant=variant), running(deliver=lambda *_: self.fail('Invalid form delivered')) as port:
                self.assertEqual(request(port, {**VALID, **variant})[0], 400)

    def test_rejects_non_object_json(self):
        with running() as port:
            self.assertEqual(request(port, ['invalid'])[0], 400)

    def test_cross_origin_rejected(self):
        with running() as port:
            self.assertEqual(request(port, origin='https://other.example')[0], 403)

    def test_public_origin_allows_https_reverse_proxy(self):
        with running(public_origin='https://park.example') as port:
            self.assertEqual(request(port, origin='https://park.example')[0], 200)

    def test_rate_limit(self):
        with running() as port:
            for _ in range(5):
                self.assertEqual(request(port)[0], 200)
            self.assertEqual(request(port)[0], 429)

    def test_limiter_recovers_after_window(self):
        limiter = RateLimiter(attempts=1, window=10)
        with patch('server.time.monotonic', return_value=100):
            self.assertTrue(limiter.allow('client'))
            self.assertFalse(limiter.allow('client'))
        with patch('server.time.monotonic', return_value=111):
            self.assertTrue(limiter.allow('client'))

    def test_large_payload_rejected(self):
        with running() as port:
            self.assertEqual(request(port, {'details': 'x' * MAX_BODY})[0], 413)

    def test_non_json_rejected(self):
        with running() as port:
            self.assertEqual(request(port, headers={'Content-Type': 'text/plain'})[0], 415)

    def test_static_allowlist(self):
        with running() as port:
            for path in ['/', '/app.js', '/styles.css', '/privacy.html', '/assets/elgrant-pestretsy.pdf']:
                self.assertEqual(request(port, None, path, method='GET')[0], 200, path)
            for path in ['/server.py', '/.env', '/.git/config', '/README.md', '/assets/../server.py', '/assets/']:
                self.assertEqual(request(port, None, path, method='GET')[0], 404, path)

    def test_smtp_starttls_and_email_headers(self):
        fields = validate({**VALID, 'contact': 'visitor@example.com'})
        config = MailConfig(host='smtp.example.com', sender='site@example.com', recipient='sales@example.com', username='site', password='test-only')
        with patch('server.smtplib.SMTP') as client:
            client.return_value.send_message.return_value = {}
            send_email(config, fields)
            client.return_value.starttls.assert_called_once()
            client.return_value.login.assert_called_once_with('site', 'test-only')
            message = client.return_value.send_message.call_args.args[0]
            self.assertEqual(message['To'], 'sales@example.com')
            self.assertEqual(message['Reply-To'], 'visitor@example.com')
            self.assertIn('200 кВт', message.get_content())
            self.assertTrue(client.return_value.starttls.call_args.kwargs['context'].check_hostname)
            client.return_value.close.assert_called_once()

    def test_smtp_ssl_does_not_use_plaintext(self):
        config = MailConfig(host='smtp.example.com', port=465, mode='ssl', sender='site@example.com', recipient='sales@example.com')
        with patch('server.smtplib.SMTP_SSL') as client:
            client.return_value.send_message.return_value = {}
            send_email(config, validate(VALID))
            self.assertTrue(client.call_args.kwargs['context'].check_hostname)
            client.return_value.starttls.assert_not_called()

    def test_smtp_recipient_refused_is_failure(self):
        with patch('server.smtplib.SMTP') as client:
            client.return_value.send_message.return_value = {'sales@example.com': (550, b'Rejected')}
            with self.assertRaises(smtplib.SMTPRecipientsRefused):
                send_email(CONFIG, validate(VALID))

    def test_plaintext_mode_or_partial_auth_not_ready(self):
        self.assertFalse(MailConfig(host='x', mode='plain', sender='a@example.com', recipient='b@example.com').ready)
        self.assertFalse(MailConfig(host='x', sender='a@example.com', recipient='b@example.com', username='only-user').ready)


if __name__ == '__main__':
    unittest.main()
