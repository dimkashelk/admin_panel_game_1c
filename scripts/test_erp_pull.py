"""Run the actual XBSL schema validator and OData builders in Element Script.

Usage: python3 scripts/test_erp_pull.py --runtime /path/to/script_u_10.0.2_1
No ERP requests are made. Only source extraction/fixtures are written to a temp dir.
"""
import argparse
import copy
import json
import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import os
from pathlib import Path
import re
import subprocess
import tempfile
import uuid

ROOT = Path(__file__).resolve().parents[1]
EXCHANGE = ROOT / 'dimkashelk/Игра/Обмен'


def method(source, name):
    start = source.index('метод ' + name + '(')
    end = source.index('\n;\n', start) + 3
    return source[start:end]


def uid(name):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, 'erp-pull-test/' + name))


def fixtures():
    common = dict(messageId=uid('message'), gameId=uid('game'), participantId=uid('team'),
                  phaseToken=uid('phase'), roundNumber=0, gameMonth='2026-01')
    empty = dict(contractVersion='1.0', salesEvents=[], executionEvents=[])
    snapshot = dict(**common, sourceVersion='freeze:1', capturedAt='2026-10-13T12:00:00Z',
                    kind='frozen', currency='RUB', freezeCommandId=uid('command'), balances=[],
                    offers=[dict(productCode=code, price='1000.00', availableQuantity=2)
                            for code in ('CityRide', 'SportDrive', 'CargoMax', 'TourPro')])
    receipt = dict(**common, status='completed', erpDocumentIds=[], snapshotId=common['messageId'])
    frozen = dict(**empty, commandId=uid('command'), receipt=receipt, snapshot=snapshot)
    cases = []

    def case(name, value, valid, schema='ERPResponse'):
        cases.append(dict(name=name, value=copy.deepcopy(value), valid=valid, schema=schema))

    initial = json.loads((ROOT / 'contracts/erp-odata-setup.json').read_text())['initial']
    case('default opening data still fits game creation contract', {**initial, 'settingsHash': 'a' * 64},
         True, 'PrepareGamePayload')
    case('empty state', empty, True)
    case('frozen snapshot plus receipt', frozen, True)
    bad = copy.deepcopy(frozen); del bad['commandId']
    case('receipt without command id', bad, False)
    case('command result requires receipt', {**empty, 'commandId': uid('command')}, False)
    bad = copy.deepcopy(frozen); del bad['snapshot']['freezeCommandId']
    case('frozen without command', bad, False)
    bad = copy.deepcopy(frozen); bad['snapshot']['kind'] = 'final'
    case('final without finances', bad, False)
    final = copy.deepcopy(bad)
    final['snapshot']['financials'] = dict(cash='1000.00', profit='-20.00', customerDebt='0.00',
                                          supplierDebt='0.00', monthClosed=True)
    case('final with signed finances', final, True)
    bad = copy.deepcopy(final); bad['snapshot']['financials']['cash'] = 1000
    case('numeric cash rejected', bad, False)
    case('unknown response field', {**empty, 'password': 'should-never-appear'}, False)
    case('unsupported version', {**empty, 'contractVersion': '2.0'}, False)
    case('readiness requires snapshot', {**empty, 'readiness': {**common, 'snapshotId': uid('snap'), 'ready': True}}, False)
    processing = dict(**empty, commandId=uid('command'), receipt=dict(**common, status='processing', erpDocumentIds=[]))
    case('asynchronous processing', processing, True)
    bad = copy.deepcopy(processing); bad['receipt']['status'] = 'failed'
    case('failure requires explanation', bad, False)
    bad['receipt'].update(errorCode='MONTH_NOT_CLOSED', errorMessage='Close the month')
    case('explained failure', bad, True)
    profile = json.loads((ROOT / 'profiles/erp-1538259/game-profile.json').read_text())
    case('prices read before every sales stage', profile['settings'], True, 'GameSettings')
    obsolete = copy.deepcopy(profile['settings']); obsolete['priceFixation'] = 'after_planning'
    case('planning price fixation no longer supported', obsolete, False, 'GameSettings')
    context = dict(contractVersion='1.0', gameId=uid('game'), participantId=uid('team'), roundNumber=0,
                   phaseCode='initialization', phaseToken=uid('phase'), stateVersion=1, gameState='preparing',
                   gameMonth='2026-01', documentDate='2026-01-01', deadline=None,
                   serverTime='2026-10-13T12:00:00Z', settingsHash='a' * 64, settings=profile['settings'],
                   capabilities=['consistentSnapshot', 'phaseGuard', 'idempotentCommands', 'salesFacts', 'finalFinancials'], phaseState='open')
    request = dict(contractVersion='1.0', context=context, organization='Учебная организация',
                   mappings=[dict(itemCode=f'Item{i}', refKey=uid(f'item{i}'), characteristicId='',
                                  unitCode='pcs', coefficient='1') for i in range(15)], orders=[], playerFinished=False, allocationReady=False)
    case('state uses ERP credentials without binding', request, True, 'ERPStateRequest')
    for field, value, valid in (
        ('documentDate', '2028-02-29', True),
        ('documentDate', '2026-02-30', False),
        ('serverTime', '2028-02-29T12:00:00+03:00', True),
        ('serverTime', '2026-02-30T12:00:00Z', False),
    ):
        dated = copy.deepcopy(request)
        dated['context'][field] = value
        case(f'calendar validation {field}={value}', dated, valid, 'ERPStateRequest')
    bad = copy.deepcopy(request); bad['context']['bindingId'] = uid('binding')
    case('binding forbidden in outgoing context', bad, False, 'ERPStateRequest')
    bad = copy.deepcopy(request); bad['mappings'].pop()
    case('incomplete mappings rejected', bad, False, 'ERPStateRequest')
    bad = copy.deepcopy(request); bad['mappings'][0]['characteristicId'] = 'not-a-guid'
    case('invalid ERP characteristic rejected', bad, False, 'ERPStateRequest')
    case('ack after commit', dict(contractVersion='1.0', gameId=uid('game'), participantId=uid('team'),
                                  messageIds=[uid('message')]), True, 'ERPAcknowledgement')
    case('duplicate ack id', dict(contractVersion='1.0', gameId=uid('game'), participantId=uid('team'),
                                  messageIds=[uid('message'), uid('message')]), False, 'ERPAcknowledgement')
    return cases


DRIVER = r'''
метод Скрипт(ФайлПроверок: Строка, АдресТеста: Строка): Число
    исп Поток = новый Файл(ФайлПроверок).ОткрытьПотокЧтения()
    знч Проверки = МассивJson(СериализацияJson.ПрочитатьОбъект<Объект?>(Поток.ПрочитатьКакСтроку()))
    пер Пройдено = 0
    для Значение из Проверки
        знч Проверка = ОбъектJson(Значение)
        пер Допустимо = Истина
        попытка
            ПроверитьСхему(Проверка.Получить("value"), Схема(СтрокаJson(Проверка, "schema")))
        поймать НеверныйКонтракт: Ошибка
            Допустимо = Ложь
        ;
        если Допустимо != БулевоJson(Проверка, "valid")
            Консоль.Записать("FAIL: " + СтрокаJson(Проверка, "name"))
            возврат 1
        ;
        Пройдено = Пройдено + 1
    ;
    знч Данные: Соответствие<Строка, Объект?> = {"snapshotId": "erp-message"}
    ПодставитьСнимок(Данные, "erp-message", "local-snapshot")
    Требовать(Данные.Получить("snapshotId") == "local-snapshot", "TEST", "Snapshot reference was not translated")
    Пройдено = Пройдено + 1
    знч ПравилаЦен: Соответствие<Строка, Объект?> = {"products": <Объект?>[
        {"productCode": "SportDrive", "minPrice": "60000.00", "maxPrice": "120000.00"}]}
    знч НачальныеЦены: Соответствие<Строка, Объект?> = {"initialPrices": <Объект?>[
        {"productCode": "SportDrive", "price": "45000.00"}]}
    ПроверитьЦенуПредложения(ПравилаЦен, НачальныеЦены, 0, "SportDrive", 65000)
    ПроверитьЦенуПредложения(ПравилаЦен, НачальныеЦены, 1, "SportDrive", 65000)
    Пройдено = Пройдено + 2
    пер Отклонено = Ложь
    попытка
        ПодставитьСнимок(<Строка, Объект?>{"snapshotId": "foreign-message"}, "erp-message", "local-snapshot")
    поймать ЧужойСнимок: Ошибка
        Отклонено = Истина
    ;
    Требовать(Отклонено, "TEST", "Foreign snapshot accepted")
    Пройдено = Пройдено + 1
    знч Строки = <Объект?>[{"productCode": "CityRide", "quantity": 2, "unitPrice": "1000.50"},
        {"productCode": "CargoMax", "quantity": 3, "unitPrice": "2000.25"}]
    знч Коды = ПроверитьСтрокиЗаказаERP(Строки)
    Требовать(Коды.Размер() == 2, "TEST", "Multiline order rejected")
    знч Ссылки = <Строка, Строка>{"CityRide": "city-id", "CargoMax": "cargo-id", "pcs": "unit-id", "vat": "vat-id", "finished_goods": "warehouse-id"}
    знч Товары = ТоварыЗаказаERP(Строки, Ссылки, "order-id", "2026-01-01T12:00:00", 22)
    знч Первый = ОбъектJson(Товары[0])
    знч Второй = ОбъектJson(Товары[1])
    Требовать(Товары.Размер() == 2 и Первый.Получить("Сумма") == 2001 и Второй.Получить("Сумма") == 6000.75,
        "TEST", "Wrong decimal totals in OData order")
    Требовать(Первый.Получить("LineNumber") == "1" и Второй.Получить("LineNumber") == "2"
        и Первый.Получить("Номенклатура_Key") == "city-id" и Второй.Получить("Номенклатура_Key") == "cargo-id", "TEST", "Wrong OData line mapping")
    Пройдено = Пройдено + 2
    знч Повтор = ТоварыЗаказаERP(Строки, Ссылки, "order-id", "2026-01-01T12:00:00", 22)
    Требовать(ОбъектJson(Повтор[0]).Получить("ИдентификаторСтроки") == Первый.Получить("ИдентификаторСтроки"), "TEST", "Retry changed line identity")
    Пройдено = Пройдено + 1
    знч ОднаСтрока = <Объект?>[Строки[0]]
    Требовать(ОбъектJson(ТоварыЗаказаERP(ОднаСтрока, Ссылки, "order-id", "2026-01-01T12:00:00", 22)[0]).Получить("ИдентификаторСтроки")
        == Ууид.Версия5("player-order-line:order-id").ВСтроку(), "TEST", "Existing single-line order identity changed")
    Пройдено = Пройдено + 1
    Отклонено = Ложь
    попытка
        ПроверитьСтрокиЗаказаERP(<Объект?>[Строки[0], Строки[0]])
    поймать ПовторПродукта: Ошибка
        Отклонено = Истина
    ;
    Требовать(Отклонено, "TEST", "Duplicate product accepted")
    Пройдено = Пройдено + 1
    знч База = новый БазаТеста(АдресHttpApi = АдресТеста)
    для Ресурс из ["ok", "processing", "ack", "denied", "broken", "oversize"]
        пер Успех = Истина
        попытка
            ЗапросHttp(База, Ресурс, <Строка, Объект?>{"probe": "native"}, Ууид.Версия5("http-probe").ВСтроку())
        поймать Сбой: Исключение
            Успех = Ложь
            если ["ok", "processing", "ack"].Содержит(Ресурс)
                Консоль.Записать("HTTP test error " + Ресурс + ": " + Сбой.Описание)
            ;
        ;
        Требовать(Успех == ["ok", "processing", "ack"].Содержит(Ресурс), "TEST", "Unexpected HTTP result: " + Ресурс)
        Пройдено = Пройдено + 1
    ;
    Консоль.Записать("PASS: %{Пройдено} native XBSL checks")
    возврат 0
;
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', type=Path, default=os.environ.get('ELEMENT_SCRIPT_HOME'))
    parser.add_argument('--java', default='java')
    args = parser.parse_args()
    if not args.runtime or not (args.runtime / 'lib').is_dir():
        parser.error('Specify --runtime with an installed Element Script distribution')
    protocol = (EXCHANGE / 'ПротоколERP.xbsl').read_text().split('@ВПроекте\nметод УникальныеПоля', 1)[0]
    contract = (EXCHANGE / 'КонтрактERP.xbsl').read_text()
    pull = (EXCHANGE / 'КлиентERP.xbsl').read_text()
    odata = (EXCHANGE / 'НастройкаБазOData.xbsl').read_text()
    checks = (EXCHANGE.parent / 'Данные/ПроверкиДанных.xbsl').read_text()
    body = '\n'.join([protocol, contract, method(pull, 'ПодставитьСнимок'),
                      method((EXCHANGE / 'НастройкиERP.xbsl').read_text(), 'ПроверитьЦенуПредложения'),
                      method(odata, 'ПроверитьСтрокиЗаказаERP'), method(odata, 'ТоварыЗаказаERP'),
                      method(pull, 'ЗапросHttp'), method(pull, 'АдресСервиса'), method(checks, 'Требовать'), DRIVER])
    body = re.sub(r'^импорт .+\n|^@[^\n]+\n', '', body, flags=re.M)
    body = body.replace('ПротоколERP.', '').replace('КонтрактERP.', '')
    body = body.replace('ПроверкиДанных.Требовать', 'Требовать')
    # Replace only application metadata types/transaction state; the HTTP implementation is original.
    body = body.replace('ПодключенияERP.Объект', 'БазаТеста')
    body = body.replace('Транзакции.ЕстьАктивная()', 'Ложь')
    body = body.replace('НастройкаБазOData.АдресПубликации(База.АдресOData)', 'База.АдресOData')
    body += '\nструктура БазаТеста\n    пер АдресHttpApi: Строка\n    пер АдресOData: Строка\n    пер ЛогинOData: Строка = \"test-user\"\n    пер ПарольOData: Строка? = \"test-password\"\n;\n'
    requests = []

    class MockERP(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'
        def log_message(self, *args):
            pass

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            requests.append((self.path, dict(self.headers), request))
            if self.path == '/ack':
                self.send_response(204); self.send_header('Content-Length', '0'); self.end_headers(); return
            if self.path == '/denied':
                self.send_response(401); self.send_header('Content-Length', '0'); self.end_headers(); return
            payload = json.dumps(dict(contractVersion='1.0', salesEvents=[], executionEvents=[])).encode()
            if self.path == '/broken':
                payload = b'{invalid'
            if self.path == '/oversize':
                payload = b' ' * 1048577
            self.send_response(202 if self.path == '/processing' else 200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    server = ThreadingHTTPServer(('127.0.0.1', 0), MockERP)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    with tempfile.TemporaryDirectory(prefix='erp-pull-test-') as temp:
        directory = Path(temp)
        script = directory / 'ПроверкаОбмена.sbsl'; script.write_text(body)
        data = directory / 'fixtures.json'; data.write_text(json.dumps(fixtures(), ensure_ascii=False))
        command = [args.java, '-Xmx256m', '--add-opens', 'java.base/java.lang=ALL-UNNAMED',
                   '--add-opens', 'java.base/java.nio=ALL-UNNAMED', '-Dfile.encoding=UTF-8',
                   f'-Djava.io.tmpdir={directory}', f'-Dlogs.root={directory / "logs"}',
                   f'-Dexecutor.location={args.runtime}', f'-Dlogback.configurationFile={args.runtime / "config/logback.xml"}',
                   '-cp', str(args.runtime / 'lib/*'), 'com.e1c.g5rt.executor.boot.ExecutorBootstrap',
                   '-c', '10.0', str(script), str(data), f'http://127.0.0.1:{server.server_port}/']
        result = subprocess.run(command, cwd=directory, capture_output=True, text=True, timeout=60)
        server.shutdown(); server.server_close(); thread.join(timeout=2)
        print(result.stdout)
        if result.returncode == 0:
            assert len(requests) == 6, requests
            for path, headers, payload in requests:
                assert headers.get('Authorization') == 'Basic ' + base64.b64encode(b'test-user:test-password').decode(), path
                assert headers.get('X-Game-Contract-Version') == '1.0', path
                assert headers.get('Idempotency-Key') and payload == {'probe': 'native'}, path
            print('PASS: HTTP auth, version, idempotency headers and request body verified')
        if result.returncode:
            print(result.stderr)
        raise SystemExit(result.returncode)


if __name__ == '__main__':
    main()
