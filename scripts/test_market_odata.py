"""Execute original XBSL OData order methods against a local ERP double.

Usage: python3 scripts/test_market_odata.py --runtime /path/to/ElementScript
Only localhost is contacted. No real ERP documents are created.
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

from test_erp_pull import EXCHANGE, ROOT, method, uid
from test_erp_setup import ERP, PROFILE
from setup_erp_odata import apply

DRIVER = r'''
метод Скрипт(АдресТеста: Строка): Число
    ПроверитьПереходыРынка()
    // Полный режим продолжает требовать снимок; рынок использует готовность заказов.
    ТребоватьПроверку(не КомандаГотова(Истина, Неопределено, ""), "Full game accepted no snapshot")
    ТребоватьПроверку(КомандаГотова(Истина, Неопределено, "", Истина), "Market still requires snapshot")
    ТребоватьПроверку(не КомандаГотова(Истина, Неопределено, "ERP error", Истина), "Market ignored order error")
    ТребоватьПроверку(ПричинаОжидания(Истина, Неопределено, "", Истина) == "", "False market warning")
    ТребоватьПроверку(ПричинаОжидания(Ложь, Неопределено, "Orders pending", Истина) == "Orders pending", "Lost order reason")
    знч Строки = <Объект?>[{"productCode": "CityRide", "quantity": 2, "unitPrice": "39000.00"}]
    знч Первый = СоздатьЗаказПоСтрокамOData(АдресТеста, "test-user", "test-password", "ДвижжОК ООО",
        "%FIRST%", Строки, "2026-10-15")
    ТребоватьПроверку(Первый.Получить("posted") == Истина и Первый.Получить("alreadyPosted") == Ложь, "New order not posted")
    знч Повтор = СоздатьЗаказПоСтрокамOData(АдресТеста, "test-user", "test-password", "ДвижжОК ООО",
        "%FIRST%", Строки, "2026-10-15")
    ТребоватьПроверку(Повтор.Получить("refKey") == Первый.Получить("refKey") и Повтор.Получить("alreadyPosted") == Истина,
        "Repeated order did not reuse posted document")
    ОжидатьОтказ(АдресТеста, "%RECOVER%", Строки, "ODATA_HTTP")
    знч ПослеСбоя = СоздатьЗаказПоСтрокамOData(АдресТеста, "test-user", "test-password", "ДвижжОК ООО",
        "%RECOVER%", Строки, "2026-10-15")
    ТребоватьПроверку(ПослеСбоя.Получить("posted") == Истина, "Could not recover failed posting")
    ОжидатьОтказ(АдресТеста, "%LOST%", Строки, "ODATA_HTTP")
    знч ПослеПотериОтвета = СоздатьЗаказПоСтрокамOData(АдресТеста, "test-user", "test-password", "ДвижжОК ООО",
        "%LOST%", Строки, "2026-10-15")
    ТребоватьПроверку(ПослеПотериОтвета.Получить("posted") == Истина, "Could not recover lost create response")
    ОжидатьОтказ(АдресТеста, "%CONFLICT%", Строки, "ODATA_CONFLICT")
    ОжидатьОтказ(АдресТеста, "%DELETED%", Строки, "ODATA_DELETED")
    знч СлишкомМного = <Объект?>[{"productCode": "CityRide", "quantity": 999, "unitPrice": "39000.00"}]
    ОжидатьОтказ(АдресТеста, "%STOCK%", СлишкомМного, "ODATA_STOCK")
    Консоль.Записать("PASS: market readiness, OData create/post, retries, conflicts, deleted documents and stock checks")
    возврат 0
;
метод ТребоватьПроверку(Условие: Булево, Текст: Строка)
    если не Условие
        выбросить новый ИсключениеНедопустимоеСостояние(Текст)
    ;
;
метод ОжидатьОтказ(Адрес: Строка, Ид: Строка, Строки: Массив<Объект?>, Код: Строка)
    пер Отклонено = Ложь
    попытка
        СоздатьЗаказПоСтрокамOData(Адрес, "test-user", "test-password", "ДвижжОК ООО", Ид, Строки, "2026-10-15")
    поймать Сбой: Ошибка
        ТребоватьПроверку(Сбой.Код == Код, "Expected " + Код + ", got " + Сбой.Код)
        Отклонено = Истина
    ;
    ТребоватьПроверку(Отклонено, "Expected error: " + Код)
;
'''


# Persistence and completion reads are seams; phase/round mutation is original XBSL.
LIFECYCLE_DRIVER = r'''
метод ПроверитьПереходыРынка()
    знч Игра = новый ИграТеста()
    Игра.ТекущийРаунд = новый РаундТеста(НомерРаунда = 0)
    Игра.СрокиФаз.Добавить(новый СрокТеста(Фаза = "initial_sales", ДлительностьМинут = 10))
    Игра.СрокиФаз.Добавить(новый СрокТеста(Фаза = "sales", ДлительностьМинут = 20))
    ОткрытьФазу(Игра, "initial_sales")
    ТребоватьПроверку(Игра.Дедлайн != Неопределено и Игра.СледующийЗаказ != Неопределено,
        "Opening market did not schedule orders and deadline")
    знч ПервыйТокен = Игра.ТокенФазы
    Игра.ЗаказыПроведены = Ложь
    пер Отклонено = Ложь
    попытка
        СледующийРаундРынка(Игра, 0)
    поймать Сбой: Исключение
        Отклонено = Истина
    ;
    ТребоватьПроверку(Отклонено и Игра.ТекущийРаунд!.НомерРаунда == 0
        и Игра.ТокенФазы == ПервыйТокен и Игра.ТекущийРаунд!.Состояние == "open",
        "Pending orders did not block closing round")
    Игра.ЗаказыПроведены = Истина
    СледующийРаундРынка(Игра, 0)
    ТребоватьПроверку(Игра.ТекущийРаунд!.НомерРаунда == 1 и Игра.Фаза == "sales"
        и Игра.ТокенФазы != ПервыйТокен и Игра.ВерсияСостояния == 1,
        "Next market round did not open sales with a new token")
    СледующийРаундРынка(Игра, 1)
    ТребоватьПроверку(Игра.Статус == "finished" и Игра.СостояниеФазы == "closed"
        и Игра.Дедлайн == Неопределено и Игра.СледующийЗаказ == Неопределено
        и Игра.ВерсияСостояния == 2, "Final round did not stop market")
    Консоль.Записать("PASS: original XBSL market lifecycle with in-memory persistence, no HTTP phase commands")
;
метод ПроверитьЗавершениеТеста(Игра: ИграТеста)
    ТребоватьПроверку(Игра.ЗаказыПроведены, "Orders pending")
;
метод СоздатьРаундТеста(Игра: ИграТеста, Номер: Число)
    Игра.ТекущийРаунд = новый РаундТеста(НомерРаунда = Номер)
;
метод СохранитьПереходТеста(Игра: ИграТеста, Версия: Число, Вид: Строка, Причина: Строка)
    Игра.ВерсияСостояния = Версия + 1
;
метод ОткрытьУчастникамТеста(Игра: ИграТеста, Токен: Строка?, Возобновление: Булево, Версия: Число)
    выбросить новый ИсключениеНедопустимоеСостояние("Market attempted HTTP phase exchange")
;
структура ИграТеста
    пер ТолькоРынок: Булево = Истина
    пер ЧислоРаундов: Число = 1
    пер Статус: Строка = "running"
    пер Фаза: Строка = "preparation"
    пер СледующийЗаказ: Момент?
    пер НомерПакетаЗаказов: Число = 0
    пер СостояниеФазы: Строка = "open"
    пер ТокенФазы: Строка = ""
    пер Дедлайн: Момент?
    пер ТекущийРаунд: РаундТеста?
    пер ВерсияСостояния: Число = 0
    пер СрокиФаз: Массив<СрокТеста> = []
    пер ЗаказыПроведены: Булево = Истина
;
структура РаундТеста
    пер НомерРаунда: Число
    пер Фаза: Строка = ""
    пер ТокенФазы: Строка = ""
    пер Состояние: Строка = ""
    пер ВремяЗакрытия: Момент?
;
структура СрокТеста
    пер Фаза: Строка
    пер ДлительностьМинут: Число
;
'''


def lifecycle_source():
    source = (EXCHANGE.parent / 'Управление/УправлениеИгрой.xbsl').read_text()
    body = '\n'.join(method(source, name) for name in ('ОткрытьФазу', 'СледующийРаундРынка'))
    body = body.replace('Игры.Объект', 'ИграТеста').replace('Фаза: ФазыИгры', 'Фаза: Строка')
    body = body.replace('Игра.ТекущийРаунд!.ЗагрузитьОбъект(Истина)!', 'Игра.ТекущийРаунд!')
    body = body.replace('    Раунд.Записать()\n', '')
    body = body.replace('РынокЗаказов.ПроверитьЗавершение(', 'ПроверитьЗавершениеТеста(')
    body = body.replace('ОбменERP.ОткрытьУчастникам(', 'ОткрытьУчастникамТеста(')
    body = body.replace('СоздатьРаунд(', 'СоздатьРаундТеста(').replace('СохранитьПереход(', 'СохранитьПереходТеста(')
    for key, value in {'ФазыИгры.Подготовка': 'preparation', 'ФазыИгры.СтартоваяПродажа': 'initial_sales',
                       'ФазыИгры.Продажи': 'sales', 'СостоянияФазы.Открыта': 'open',
                       'СостоянияФазы.Закрыта': 'closed', 'СостоянияИгры.Завершена': 'finished'}.items():
        body = body.replace(key, '"' + value + '"')
    return body + LIFECYCLE_DRIVER


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', type=Path, required=True)
    parser.add_argument('--java', default='java')
    args = parser.parse_args()
    ids = {name: uid('market-' + name) for name in ('first', 'recover', 'lost', 'conflict', 'deleted', 'stock')}
    driver = DRIVER
    for key, value in ids.items():
        driver = driver.replace('%' + key.upper() + '%', value)
    odata = (EXCHANGE / 'НастройкаБазOData.xbsl').read_text()
    summary = (EXCHANGE.parent / 'Управление/СводкиИгры.xbsl').read_text()
    protocol = (EXCHANGE / 'ПротоколERP.xbsl').read_text().split('@ВПроекте\nметод УникальныеПоля', 1)[0]
    body = '\n'.join([protocol, (EXCHANGE / 'КонтрактERP.xbsl').read_text(),
                      method((EXCHANGE / 'ПрофильНастройкиOData.xbsl').read_text(), 'Получить').replace('метод Получить(', 'метод ПолучитьПрофиль('),
                      *(method(odata, name) for name in ('СоздатьЗаказПоСтрокамOData', 'ПроверитьСтрокиЗаказаERP',
                        'ТоварыЗаказаERP', 'Найти', 'Разрешить', 'Совпадает', 'ПровестиДокумент',
                        'ЗапросOData', 'ПрочитатьВсе', 'ОстатокПродукта', 'СоединитьСтроки')),
                      method(summary, 'КомандаГотова'), method(summary, 'ПричинаОжидания'), lifecycle_source(), driver])
    body = re.sub(r'^импорт .+\n|^@[^\n]+\n', '', body, flags=re.M)
    body = body.replace('ПротоколERP.', '').replace('КонтрактERP.', '')
    body = body.replace('ПрофильНастройкиOData.Получить()', 'ПолучитьПрофиль()')
    body = body.replace('Транзакции.ЕстьАктивная()', 'Ложь').replace('НормализоватьАдрес(Адрес)', 'Адрес')
    body = body.replace('ПроверкиДанных.Требовать(', 'ТребоватьПроверку(').replace('СнимкиERP.Ссылка?', 'Строка?')
    erp = ERP()
    apply(erp, PROFILE)
    erp.posts = []
    requests = []
    failed_post = False

    class MockERP(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def log_message(self, *args):
            pass

        def respond(self, data, code=200):
            payload = json.dumps(data, ensure_ascii=False).encode()
            self.send_response(code)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def do_GET(self):
            self.handle_request()

        def do_POST(self):
            self.handle_request()

        def handle_request(self):
            nonlocal failed_post
            parts = urlsplit(self.path)
            path = unquote(parts.path).lstrip('/')
            params = {k: v[0] for k, v in parse_qs(parts.query).items()}
            raw = self.rfile.read(int(self.headers.get('Content-Length', 0)))
            data = json.loads(raw) if raw else None
            requests.append((self.command, path, dict(self.headers), data))
            expected_auth = 'Basic ' + base64.b64encode(b'test-user:test-password').decode()
            if self.headers.get('Authorization') != expected_auth:
                self.respond({}, 401)
                return
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
                self.respond({'value': rows[:int(params.get('$top', len(rows)))]})
                return
            if self.command == 'POST' and path == 'Document_ЗаказКлиента':
                document = erp.add(entity, data)
                document.update(Posted=False, Number=f'TEST-{len(erp.rows[entity])}')
                if document['Комментарий'].endswith(ids['conflict']):
                    document['Товары'][0]['Количество'] += 1
                if document['Комментарий'].endswith(ids['deleted']):
                    document['DeletionMark'] = True
                erp.posts.append((entity, document['Ref_Key'], 'Create'))
                if document['Комментарий'].endswith(ids['lost']):
                    self.respond({}, 503)  # Document committed; response unavailable.
                else:
                    self.respond(document, 201)
                return
            if self.command == 'POST' and path.endswith('/Post()'):
                key = path.split("(guid'", 1)[1].split("'", 1)[0]
                document = erp.read(entity, key)
                if document['Комментарий'].endswith(ids['recover']) and not failed_post:
                    failed_post = True
                    self.respond({}, 503)
                    return
                assert data is None, 'Post() must have an empty body'
            params = {key: int(value) if key in ('$top', '$skip') else value for key, value in params.items()}
            self.respond(erp.request(path, method=self.command, body=data, params=params))

    server = ThreadingHTTPServer(('127.0.0.1', 0), MockERP)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory(prefix='market-odata-test-') as temp:
            directory = Path(temp)
            script = directory / 'РынокOData.sbsl'
            script.write_text(body)
            command = [args.java, '-Xmx256m', '--add-opens', 'java.base/java.lang=ALL-UNNAMED',
                       '--add-opens', 'java.base/java.nio=ALL-UNNAMED', '-Dfile.encoding=UTF-8',
                       f'-Djava.io.tmpdir={directory}', f'-Dlogs.root={directory / "logs"}',
                       f'-Dexecutor.location={args.runtime}', f'-Dlogback.configurationFile={args.runtime / "config/logback.xml"}',
                       '-cp', str(args.runtime / 'lib/*'), 'com.e1c.g5rt.executor.boot.ExecutorBootstrap',
                       '-c', '10.0', str(script), f'http://127.0.0.1:{server.server_port}/']
            result = subprocess.run(command, cwd=directory, capture_output=True, text=True, timeout=60)
            print(result.stdout)
            if result.returncode:
                print(result.stderr)
                raise SystemExit(result.returncode)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    documents = erp.rows['Document_ЗаказКлиента']
    assert len(documents) == 5, 'Retry created a duplicate or stock failure created a document'
    for name in ('first', 'recover', 'lost'):
        docs = [d for d in documents if d['Комментарий'] == 'BICYCLE_PLAYER/' + ids[name]]
        assert len(docs) == 1 and docs[0]['Posted'], name
        assert sum(event[1:] == (docs[0]['Ref_Key'], 'Post') for event in erp.posts) == 1, name
        assert docs[0]['СуммаДокумента'] == 78000
    assert not any(d.get('Posted') for d in documents if d['Комментарий'].endswith((ids['conflict'], ids['deleted'])))
    assert all(path.startswith(('Catalog_', 'Document_', 'AccumulationRegister_')) for _, path, _, _ in requests)
    print(f'PASS: {len(requests)} OData requests; 3 conducted orders, no retry duplicates or HTTP-extension calls')


if __name__ == '__main__':
    main()
