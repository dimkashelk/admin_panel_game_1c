"""Run the original game setup methods in Element Script against a local ERP double.

The real XBSL preflight, writes, retries and verification run unchanged; only
URL normalization is bypassed for the localhost transport. No real ERP is contacted.
"""
import argparse
import base64
import copy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import subprocess
import tempfile
import threading
from urllib.parse import parse_qs, unquote, urlsplit

from test_erp_pull import EXCHANGE, method
from test_erp_setup import ERP
from setup_erp_odata import SetupError


def setup_source():
    full_protocol = (EXCHANGE / 'ПротоколERP.xbsl').read_text()
    protocol = full_protocol.split('@ВПроекте\nметод Требовать', 1)[0]
    protocol += '\n'.join(method(full_protocol, n) for n in ('Требовать', 'ОбъектJson', 'МассивJson',
                            'СтрокаJson', 'ЧислоJson', 'Прочитать', 'Json', 'Деньги'))
    odata = (EXCHANGE / 'НастройкаБазOData.xbsl').read_text()
    names = ('Подготовить', 'ПроверитьЧистуюБазу', 'ПроверитьЧистыеРасчеты',
             'ПроверитьНачальныйДолг', 'ЭкспортироватьСсылки', 'НормализоватьАдрес',
             'ЗапросOData', 'Найти', 'Разрешить', 'Совпадает', 'СоединитьСтроки',
             'ПрочитатьВсе', 'ПровестиДокумент')
    profile = method((EXCHANGE / 'ПрофильНастройкиOData.xbsl').read_text(), 'Получить')
    profile = profile.replace('метод Получить(', 'метод ПолучитьПрофиль(')
    form = (EXCHANGE.parent / 'Управление/НастройкаИнформационныхБаз.xbsl').read_text()
    body = '\n'.join([protocol, profile, *(method(odata, n) for n in names), method(form, 'ПредставитьОтчет')])
    body = re.sub(r'^импорт .+\n|^@[^\n]+\n', '', body, flags=re.M)
    return body.replace('ПротоколERP.', '').replace('ПрофильНастройкиOData.Получить()', 'ПолучитьПрофиль()')


DRIVER = r'''
метод Скрипт(Адрес: Строка): Число
    знч План = Прочитать(Подготовить(Адрес, "test-user", "test-password", Ложь))
    Требовать(План.Получить("success") == Истина, "TEST", Json(План))
    знч Сбой = Прочитать(Подготовить(Адрес, "test-user", "test-password", Истина))
    Требовать(Сбой.Получить("success") == Ложь и Сбой.Получить("step") == "initial_supplier_debt", "TEST", Json(Сбой))
    знч Повтор = Прочитать(Подготовить(Адрес, "test-user", "test-password", Истина))
    Требовать(Повтор.Получить("success") == Истина, "TEST", Json(Повтор))
    знч Сверка = ОбъектJson(Повтор.Получить("verification"))
    Требовать(Сверка.Получить("supplierDebt") == "2801800.00", "TEST", "Opening debt mismatch")
    Требовать(Повтор.Получить("fullSetupReady") == Ложь, "TEST", "Partial setup claimed full readiness")
    знч Отчет = ПредставитьОтчет(Повтор)
    Требовать(Отчет.Содержит("2801800.00") и Отчет.Содержит("18%"), "TEST", "Setup report hides debt or remaining work")
    знч Третий = Прочитать(Подготовить(Адрес, "test-user", "test-password", Истина))
    Требовать(Третий.Получить("success") == Истина, "TEST", Json(Третий))
    Консоль.Записать("PASS: original XBSL setup, lost posting response, repeat without duplicate stock/cash/debt, register verification")
    возврат 0
;
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', type=Path, required=True)
    parser.add_argument('--java', default='java')
    args = parser.parse_args()
    erp = ERP()
    erp.fail_after_post = 'Document_ВводОстатков'
    requests = []

    class MockERP(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'
        def log_message(self, *args):
            pass

        def respond(self, payload, status=200):
            data = json.dumps(payload, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            self.handle_request()

        def do_POST(self):
            self.handle_request()

        def do_PATCH(self):
            self.handle_request()

        def handle_request(self):
            parts = urlsplit(self.path)
            path = unquote(parts.path).lstrip('/')
            params = {k: v[0] for k, v in parse_qs(parts.query).items()}
            raw = self.rfile.read(int(self.headers.get('Content-Length', 0)))
            data = json.loads(raw) if raw else None
            expected = 'Basic ' + base64.b64encode(b'test-user:test-password').decode()
            if self.headers.get('Authorization') != expected:
                self.respond({}, 401)
                return
            requests.append((self.command, path))
            entity = path.split('(')[0].split('/')[0]
            if self.command == 'GET' and '(guid' not in path and '/Balance(' not in path:
                rows = copy.deepcopy(erp.rows.get(entity, []))
                condition = params.get('$filter', '')
                marker = re.search(r"substringof\('((?:''|[^'])*)',Комментарий\)", condition)
                if marker:
                    rows = [r for r in rows if marker[1].replace("''", "'") in r.get('Комментарий', '')]
                else:
                    for key, val in re.findall(r"([\w]+) eq (?:guid)?'((?:''|[^'])*)'", condition):
                        rows = [r for r in rows if r.get(key) == val.replace("''", "'")]
                start = int(params.get('$skip', 0))
                self.respond({'value': rows[start:start + int(params.get('$top', len(rows)))]})
                return
            if self.command == 'POST' and path.endswith('/Post()'):
                assert data is None, 'Post() must have an empty body'
            try:
                params = {k: int(v) if k in ('$top', '$skip') else v for k, v in params.items()}
                self.respond(erp.request(path, method=self.command, body=data, params=params))
            except SetupError:
                self.respond({}, 503)

    server = ThreadingHTTPServer(('127.0.0.1', 0), MockERP)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory(prefix='erp-setup-xbsl-') as temp:
            directory = Path(temp)
            script = directory / 'Настройка.sbsl'
            body = setup_source().replace('НормализоватьАдрес(Адрес))', 'Адрес)')
            body = body.replace('знч Публикация = НормализоватьАдрес(Адрес)', 'знч Публикация = Адрес')
            script.write_text(body + '\n' + DRIVER)
            command = [args.java, '-Xmx256m', '--add-opens', 'java.base/java.lang=ALL-UNNAMED',
                       '--add-opens', 'java.base/java.nio=ALL-UNNAMED', '-Dfile.encoding=UTF-8',
                       f'-Djava.io.tmpdir={directory}', f'-Dlogs.root={directory / "logs"}',
                       f'-Dexecutor.location={args.runtime}', f'-Dlogback.configurationFile={args.runtime / "config/logback.xml"}',
                       '-cp', str(args.runtime / 'lib/*'), 'com.e1c.g5rt.executor.boot.ExecutorBootstrap',
                       '-c', '10.0', str(script), f'http://127.0.0.1:{server.server_port}/']
            result = subprocess.run(command, cwd=directory, capture_output=True, text=True, timeout=120)
            print(result.stdout)
            if result.returncode:
                print(result.stderr)
                raise SystemExit(result.returncode)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    for entity in ('Document_ВводОстатковТоваров', 'Document_ВводОстатковДенежныхСредств', 'Document_ВводОстатков'):
        for doc in erp.rows[entity]:
            assert sum(p[1:] == (doc['Ref_Key'], 'Post') for p in erp.posts) == 1, entity
    print(f'PASS: {len(requests)} native OData requests; opening documents posted exactly once')


if __name__ == '__main__':
    main()
