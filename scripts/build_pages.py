"""Build a static-only GitHub Pages artifact; no backend or credentials included."""
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'dist'


def build():
    if OUTPUT.is_symlink():
        raise RuntimeError('Refusing to replace a symlink at dist/')
    if OUTPUT.exists():
        shutil.rmtree(OUTPUT)
    OUTPUT.mkdir()
    for name in ('index.html', 'privacy.html', 'styles.css', 'app.js'):
        shutil.copy2(ROOT / name, OUTPUT / name)
    shutil.copytree(ROOT / 'assets', OUTPUT / 'assets')
    page = OUTPUT / 'index.html'
    source = page.read_text()
    original = 'id="request-form" action="/api/contact"'
    if source.count(original) != 1:
        raise RuntimeError('Expected exactly one contact form to configure')
    source = source.replace(original, 'id="request-form" data-delivery="email" data-recipient="Iv08dem@yandex.ru" action="mailto:Iv08dem@yandex.ru"')
    source = source.replace('Отдел продаж свяжется с вами по указанному контакту.', 'Передайте заявку отделу продаж через ваше почтовое приложение.')
    page.write_text(source)
    privacy = OUTPUT / 'privacy.html'
    source = privacy.read_text().replace(
        'Данные передаются в отдел продаж по электронной почте.',
        'Эта версия сайта подготавливает письмо в вашем почтовом приложении. Сайт сам не отправляет данные формы на сервер. Данные передаются в отдел продаж, когда вы отправляете подготовленное письмо из приложения.'
    )
    privacy.write_text(source)
    (OUTPUT / '.nojekyll').touch()
    print('Static GitHub Pages artifact created in dist/. Backend files are excluded.')


if __name__ == '__main__':
    build()
