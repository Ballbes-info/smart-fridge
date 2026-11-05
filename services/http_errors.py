"""Consistent recovery screens and JSON errors without exposing server internals."""
from urllib.parse import urlsplit, unquote
from flask import jsonify, render_template, request, session, url_for
from werkzeug.exceptions import default_exceptions

TITLES = {400: 'Проверьте запрос', 403: 'Нет доступа', 404: 'Страница не найдена',
          409: 'Данные уже изменились', 413: 'Слишком большой файл или форма',
          429: 'Слишком много запросов', 500: 'Не удалось выполнить действие'}
MESSAGES = {
    400: 'Обновите страницу и повторите действие. Данные в форме можно проверить перед отправкой.',
    403: 'Для этого действия нет доступа. Проверьте подписку или права на общий холодильник.',
    404: 'Возможно, запись удалена или ссылка устарела. Вернитесь в раздел и выберите запись заново.',
    409: 'Запись изменена в другой вкладке или другим участником. Обновите её перед повторным сохранением.',
    413: 'Файл или форма превышают допустимый размер. Ограничение указано рядом с полем загрузки.',
    429: 'Подождите перед повторной попыткой. Частые повторные нажатия не ускоряют обработку.',
    500: 'Действие не завершилось. Вернитесь к форме и проверьте результат перед повтором.'}


def register_errors(app):
    def handle(error):
        code = error.code
        message = MESSAGES[code]
        # Custom validation messages are useful; default Werkzeug text and exception
        # details are not. Never render the original exception behind a 500 response.
        if code not in (404, 500) and error.description != default_exceptions[code].description:
            message = str(error.description)
        if request.accept_mimetypes.best == 'application/json':
            response = jsonify(ok=False, error=message, message=message)
            response.status_code = code
            response.headers['Cache-Control'] = 'no-store'
            return response
        back = None
        if request.referrer:
            try:
                source = urlsplit(request.referrer)
                decoded = unquote(source.path)
                if (source.netloc == request.host and decoded.startswith('/') and not decoded.startswith('//')
                        and '\\' not in decoded and not any(ord(c)<32 for c in decoded)
                        and source.path != request.path and len(request.referrer) <= 2048):
                    back = source.path + ('?' + source.query if source.query else '')
            except ValueError:
                pass
        home = url_for('workspace.home') if session.get('user_id') else url_for('index')
        response = app.make_response((render_template('http_error.html', error_code=code,
            error_title=TITLES[code], error_message=message, error_back=back, error_home=home, workspace_error=True), code))
        response.headers['Cache-Control'] = 'no-store'
        return response

    for code in TITLES:
        app.register_error_handler(code, handle)
