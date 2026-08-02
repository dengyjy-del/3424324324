"""
Диагностическая функция. Никаких импортов проекта и зависимостей.

Нужна, чтобы за один запрос понять, где проблема:

    /api/ping отвечает   → Python-функции на Vercel работают,
                           значит дело в коде или переменных приложения
    /api/ping даёт 404   → Vercel вообще не собрал ни одной функции,
                           значит файлы лежат не там, где он их ищет
"""

from http.server import BaseHTTPRequestHandler


class handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(
            b'{"ok": true, "note": "Python functions are deployed correctly"}'
        )
