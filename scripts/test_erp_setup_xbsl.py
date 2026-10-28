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
from test_erp_setup import ERP, PROFILE
from setup_erp_odata import SetupError


def setup_source():
    full_protocol = (EXCHANGE / 'ПротоколERP.xbsl').read_text()
    protocol = full_protocol.split('@ВПроекте\nметод Требовать', 1)[0]
    protocol += '\n'.join(method(full_protocol, n) for n in ('Требовать', 'ОбъектJson', 'МассивJson',
                            'СтрокаJson', 'ЧислоJson', 'Прочитать', 'Json', 'Деньги'))
    odata = (EXCHANGE / 'НастройкаБазOData.xbsl').read_text()
    names = ('Подготовить', 'ПроверитьЧистуюБазу', 'ПроверитьЧистыеРасчеты',
             'ПроверитьНачальныйДолг', 'ЭкспортироватьСсылки', 'НормализоватьАдрес',
             'ЗапросOData', 'Найти', 'МожноДополнитьНастройку', 'ПроверяемыеПоляНастройки', 'РасхожденияНастройки', 'Разрешить', 'Совпадает', 'СоединитьСтроки',
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
    Требовать(ПредставитьОтчет(План).Содержит("ИспользоватьПооперационноеУправление"), "TEST", "Settings changes missing from preview")
    знч СбойНастройки = Прочитать(Подготовить(Адрес, "test-user", "test-password", Истина))
    Требовать(СбойНастройки.Получить("success") == Ложь и СбойНастройки.Получить("step") == "department", "TEST", Json(СбойНастройки))
    знч Сбой = Прочитать(Подготовить(Адрес, "test-user", "test-password", Истина))
    Требовать(Сбой.Получить("success") == Ложь и Сбой.Получить("step") == "initial_supplier_debt", "TEST", Json(Сбой))
    знч Повтор = Прочитать(Подготовить(Адрес, "test-user", "test-password", Истина))
    Требовать(Повтор.Получить("success") == Истина, "TEST", Json(Повтор))
    знч Сверка = ОбъектJson(Повтор.Получить("verification"))
    Требовать(Сверка.Получить("supplierDebt") == "2801800.00", "TEST", "Opening debt mismatch")
    Требовать(Повтор.Получить("fullSetupReady") == Ложь, "TEST", "Partial setup claimed full readiness")
    знч Отчет = ПредставитьОтчет(Повтор)
    Требовать(Отчет.Содержит("2801800.00") и Отчет.Содержит("Схема обеспечения комплектующих"), "TEST", "Setup report hides debt or remaining work")
    знч Третий = Прочитать(Подготовить(Адрес, "test-user", "test-password", Истина))
    Требовать(Третий.Получить("success") == Истина, "TEST", Json(Третий))
    знч ПроверкаГотовой = Прочитать(Подготовить(Адрес, "test-user", "test-password", Ложь))
    Требовать(ПроверкаГотовой.Получить("success") == Истина и ПроверкаГотовой.Получить("requiresChanges") == Ложь,
        "TEST", "Ready ERP still requires changes: " + Json(ПроверкаГотовой))
    Консоль.Записать("PASS: original XBSL settings alignment, preview, lost PATCH and posting responses, repeat without duplicate stock/cash/debt, register verification")
    возврат 0
;
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', type=Path, required=True)
    parser.add_argument('--java', default='java')
    parser.add_argument('--scenario-stock', choices=('full', 'partial', 'empty'), default='full')
    args = parser.parse_args()
    erp = ERP()
    item = erp.add('Catalog_Номенклатура', {'Description': 'Городской велосипед', 'Артикул': 'CITY-001',
        'НаименованиеПолное': 'Велосипед городской учебный',
        'ВидНоменклатуры_Key': '00000000-0000-0000-0000-000000000000',
        'ИспользованиеХарактеристик': 'ИндивидуальныеДляНоменклатуры'})
    department = erp.add('Catalog_СтруктураПредприятия', {'Description': 'Цех сборки',
        'ПроизводственноеПодразделение': False, 'ИспользоватьПооперационноеУправление': True,
        'ИспользоватьПооперационноеПланирование': True, 'ИнтервалПланирования': 'Неделя',
        'Комментарий': 'Существующий учебный цех'})
    warehouse = erp.add('Catalog_Склады', {'Description': 'Закупки',
        'ИспользоватьОрдернуюСхемуПриПоступлении': True, 'ЦеховаяКладовая': True})
    spec_name = next(n['fields']['Description'] for n in PROFILE['nodes'] if n['key'] == 'spec_CityRide')
    spec = erp.add('Catalog_РесурсныеСпецификации', {'Description': spec_name,
        'Описание': 'Настроено вручную', 'МатериалыИУслуги': [], 'Статус': 'НеДействует'})
    agreement = erp.add('Catalog_СоглашенияСКлиентами', {'Description': 'Продажи велосипедов',
        'Комментарий': 'Настроено вручную', 'Типовое': False, 'ЦенаВключаетНДС': False})
    erp.fail_after_patch = 'Catalog_СтруктураПредприятия'
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
            driver = DRIVER
            if args.scenario_stock != 'full':
                from test_game_scenario import DRIVER as SCENARIO_TESTS
                mapper = method((EXCHANGE / 'НастройкиERP.xbsl').read_text(), 'ПрименитьУсловияКПрофилю')
                mapper = re.sub(r'^@[^\n]+\n', '', mapper, flags=re.M).replace('ПротоколERP.', '')
                mapper = mapper.replace('ПроверкиДанных.Требовать', 'ТребоватьПроверку')
                mapper = mapper.replace('ПрофильНастройкиOData.Получить()', 'ПолучитьПрофиль()')
                body += '\n' + SCENARIO_TESTS.split('метод Скрипт()', 1)[0] + mapper
                codes = '["CityRide", "FRAME_CARGO"]' if args.scenario_stock == 'partial' else '<Строка>[]'
                prepare = '''
    знч Профиль = Прочитать(ПолучитьПрофиль())
    знч Условия = ОбъектJson(Профиль.Получить("initial"))
    знч Остатки = <Объект?>[]
    для Элемент из МассивJson(Условия.Получить("balances"))
        если КОДЫ.Содержит(СтрокаJson(ОбъектJson(Элемент), "itemCode"))
            Остатки.Добавить(Элемент)
        ;
    ;
    Условия.Вставить("balances", Остатки)
    ПрименитьУсловияКПрофилю(Профиль, Условия)
    знч ТекстПрофиля = Json(Профиль)
'''.replace('КОДЫ', codes)
                driver = driver.replace('метод Скрипт(Адрес: Строка): Число', 'метод Скрипт(Адрес: Строка): Число' + prepare)
                for apply in ('Истина', 'Ложь'):
                    call = f'Подготовить(Адрес, "test-user", "test-password", {apply}'
                    driver = driver.replace(call + ')', call + ', "ДвижжОК ООО", "2026-10-15T00:00:00", ТекстПрофиля)')
            script.write_text(body + '\n' + driver)
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
        for doc in erp.rows.get(entity, []):
            assert sum(p[1:] == (doc['Ref_Key'], 'Post') for p in erp.posts) == 1, entity
    saved_item = erp.read('Catalog_Номенклатура', item['Ref_Key'])
    assert saved_item['Артикул'] == 'CITY-001'
    assert saved_item['НаименованиеПолное'] == 'Велосипед городской учебный'
    assert saved_item['ИспользованиеХарактеристик'] == 'НеИспользовать'
    saved_department = erp.read('Catalog_СтруктураПредприятия', department['Ref_Key'])
    assert saved_department['ИспользоватьПооперационноеУправление'] is False
    assert saved_department['ИспользоватьПооперационноеПланирование'] is False
    assert saved_department['ИнтервалПланирования'] == 'День'
    assert saved_department['Комментарий'] == 'Существующий учебный цех'
    assert sum(p[:2] == ('Catalog_СтруктураПредприятия', department['Ref_Key']) for p in erp.patches) == 1
    assert erp.read('Catalog_Склады', warehouse['Ref_Key'])['ИспользоватьОрдернуюСхемуПриПоступлении'] is False
    assert len(erp.read('Catalog_РесурсныеСпецификации', spec['Ref_Key'])['МатериалыИУслуги']) == 5
    assert erp.read('Catalog_СоглашенияСКлиентами', agreement['Ref_Key'])['Типовое'] is True
    stock = erp.rows.get('Document_ВводОстатковТоваров', [])
    if args.scenario_stock != 'full':
        assert len(erp.rows['Catalog_Номенклатура']) == 15, 'Missing nomenclature without opening stock'
        assert sum(len(doc['Товары']) for doc in stock) == (2 if args.scenario_stock == 'partial' else 0)
        assert all(row['Количество'] > 0 for doc in stock for row in doc['Товары'])
        assert len(stock) == (2 if args.scenario_stock == 'partial' else 0), 'Empty stock documents'
    print(f'PASS: {len(requests)} native OData requests; opening documents posted exactly once')


if __name__ == '__main__':
    main()
