"""Market reads are read-only; exercise time, dimensions and ERP price priority."""
from decimal import Decimal
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from types import SimpleNamespace

import read_erp_market as m
from setup_erp_odata import SetupError, ZERO
from test_erp_protocol import BASE, literal
from test_xbsl_logic import load_module, calculate, offer, XArray


def price(value=33800, period='2027-01-15T00:00:00', **changes):
    return dict(Active=True, Period=period, Номенклатура_Key='bike', ВидЦены_Key='sale',
                ХарактеристикаЦО_Key=ZERO, УпаковкаЦО_Key=ZERO, СерияЦО_Key=ZERO,
                МаркетинговоеМероприятие_Key=ZERO, Упаковка_Key=ZERO,
                Валюта_Key='rub', Цена=value, **changes)


class MarketReadTests(unittest.TestCase):
    def test_initial_market_uses_erp_price_in_both_validation_and_calculation(self):
        source = (BASE.parent / 'Управление/УправлениеИгрой.xbsl').read_text()
        method = source[source.index('метод ПараметрыДляПродукта('):].split('\n@')[0]
        product = SimpleNamespace(ЗагрузитьОбъект=lambda: SimpleNamespace(Код='CityRide'))
        scenario = SimpleNamespace(Продукт=product, БазовыйСпрос=Decimal(120), РеферентнаяЦена=Decimal(52000),
                                   МинимальнаяЦена=Decimal(35000), МаксимальнаяЦена=Decimal(75000),
                                   Эластичность=Decimal(1), ЧувствительностьКЦене=Decimal('1.35'), Тренд=Decimal(1))
        game = SimpleNamespace(ПараметрыРынка=XArray([scenario]), ТрендыПоРаундам=XArray(), Демонстрационная=False,
                               ТекущийРаунд=SimpleNamespace(ЗагрузитьОбъект=lambda: SimpleNamespace(НомерРаунда=0)))
        settings = SimpleNamespace(Подготовка=lambda g: {'CityRide': Decimal(33800)}, НачальнаяЦена=lambda p, code: p[code])
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'Parameters.xbsl'
            path.write_text(method)
            module = load_module(path, {'НастройкиERP': settings})
        rules = module.ПараметрыДляПродукта(game, product)
        self.assertEqual((rules.МинимальнаяЦена, rules.МаксимальнаяЦена), (33800, 75000))
        result = calculate([offer('ERP', 33800, 15)], rules=rules)
        self.assertEqual(result.Заказы[0].Заказано, 15)
        game.ТекущийРаунд = SimpleNamespace(ЗагрузитьОбъект=lambda: SimpleNamespace(НомерРаунда=1))
        rules = module.ПараметрыДляПродукта(game, product)
        self.assertEqual((rules.МинимальнаяЦена, rules.МаксимальнаяЦена), (35000, 75000))
        with self.assertRaises(ValueError):
            calculate([offer('ERP', 33800, 15)], rules=rules, round_number=1)

    def test_latest_price_ignores_future_inactive_and_other_dimensions(self):
        rows = [price(), price(40000, '2027-02-01T00:00:00'),
                {**price(50000), 'Active': False}, {**price(60000), 'ВидЦены_Key': 'purchase'},
                {**price(70000), 'ХарактеристикаЦО_Key': 'characteristic'},
                price(39123, '2027-01-16T00:00:00')]
        for sequence in [rows, list(reversed(rows))]:
            actual = m.latest_price([{'RecordSet': sequence}], 'bike', 'sale', 'rub', '2027-01-31T23:59:59')
            self.assertEqual(actual, (Decimal('39123'), '2027-01-16T00:00:00'))

    def test_missing_conflicting_currency_and_pack_prices_block(self):
        for rows in [[], [price(), price(1)], [{**price(), 'Валюта_Key': 'usd'}],
                     [{**price(), 'Упаковка_Key': 'box'}], [price(0)]]:
            with self.subTest(rows=rows), self.assertRaises(SetupError):
                m.latest_price([{'RecordSet': rows}], 'bike', 'sale', 'rub', '2027-01-31T23:59:59')

    def test_old_conflicting_prices_do_not_override_latest(self):
        rows = [price(1), price(2), price(3, '2027-01-16T00:00:00')]
        self.assertEqual(m.latest_price([{'RecordSet': rows}], 'bike', 'sale', 'rub', '2027-01-31T23:59:59')[0], 3)

    def test_stock_excludes_other_warehouse_characteristics_and_assignments(self):
        row = dict(Номенклатура_Key='bike', Склад_Key='finished', Характеристика_Key=ZERO,
                   Назначение_Key=ZERO, ВНаличииBalance=15, КОтгрузкеBalance=3)
        rows = [row, {**row, 'Склад_Key': 'other'}, {**row, 'Назначение_Key': 'reserved'},
                {**row, 'Характеристика_Key': 'char'}]
        self.assertEqual(m.available_stock(rows, 'bike', 'finished'), (15, 3, 12))
        self.assertEqual(m.available_stock([], 'bike', 'finished'), (0, 0, 0))
        with self.assertRaises(SetupError):
            m.available_stock([{**row, 'ВНаличииBalance': 15.5}], 'bike', 'finished')

    def test_all_pages_are_read_without_writes(self):
        class Client:
            def request(self, path, method='GET', params=None):
                self.assert_read = method == 'GET'
                return {'value': list(range(params['$skip'], min(params['$skip'] + 100, 205)))}
        client = Client()
        self.assertEqual(m.read_all(client, 'prices'), list(range(205)))
        self.assertTrue(client.assert_read)

    def test_posted_orders_reduce_available_without_reducing_physical_stock(self):
        row = dict(Номенклатура_Key='bike', Склад_Key='finished', Характеристика_Key=ZERO,
                   Назначение_Key=ZERO, ВНаличииBalance=15, КОтгрузкеBalance=0)
        order = {k: row[k] for k in ['Номенклатура_Key', 'Склад_Key', 'Характеристика_Key', 'Назначение_Key']}
        order.update(КОтгрузкеBalance=15, ВРезервеBalance=0)
        self.assertEqual(m.available_stock([row], 'bike', 'finished', [order]), (15, 15, 0))
        self.assertEqual(m.available_stock([{**row, 'КОтгрузкеBalance': 15}], 'bike', 'finished', [order]), (15, 15, 0))

    @unittest.skipUnless(os.environ.get('ELEMENT_SCRIPT_HOME'), 'Native Element Script runtime not configured')
    def test_original_xbsl_reads_and_profile_validation(self):
        protocol = (BASE / 'ПротоколERP.xbsl').read_text().split('@ВПроекте\nметод Ответ(')[0]
        source = protocol + '\n' + (BASE / 'КонтрактERP.xbsl').read_text()
        for filename, names in [('НастройкиERP.xbsl', ['ПроверитьПодготовку', 'ПроверитьНастройки', 'ПроверитьБалансы', 'Единица', 'ПримерПрофиля', 'НачальнаяЦена', 'ПроверитьЦенуПредложения']),
                                ('НастройкаБазOData.xbsl', ['ПоследняяЦена', 'ОстатокПродукта'])]:
            text = (BASE / filename).read_text()
            for name in names:
                source += '\n' + text[text.index('метод ' + name + '('):].split('\n@')[0]
        source = source.replace('импорт Данные\n', '').replace('@ВПроекте', '@Глобально').replace('ПротоколERP.', '').replace('КонтрактERP.', '')
        source += '''
метод Скрипт(): Строка
    знч Профиль = Прочитать(ПримерПрофиля())
    знч Настройки = ОбъектJson(Профиль.Получить("settings"))
    знч Подготовка = ОбъектJson(Профиль.Получить("prepare"))
    знч НачальныеЦены = МассивJson(Подготовка.Получить("initialPrices"))
    знч НачальнаяЦена = ОбъектJson(НачальныеЦены[0])
    НачальнаяЦена.Вставить("price", "39123.00")
    НачальныеЦены[0] = НачальнаяЦена
    Подготовка.Вставить("initialPrices", НачальныеЦены)
    знч Балансы = МассивJson(Подготовка.Получить("balances"))
    знч Баланс = ОбъектJson(Балансы[0])
    Баланс.Вставить("quantity", "13")
    Баланс.Вставить("unitCost", "30001.00")
    Балансы[0] = Баланс
    Подготовка.Вставить("balances", Балансы)
    Подготовка.Вставить("initialCash", "123.45")
    ПроверитьПодготовку(Подготовка, Настройки)
    ПроверитьЦенуПредложения(Настройки, Подготовка, 0, "CityRide", 39123)
    ПроверитьЦенуПредложения(Настройки, Подготовка, 0, "SportDrive", 40600)
    ПроверитьЦенуПредложения(Настройки, Подготовка, 1, "SportDrive", 82000)
    пер Отказы = 0
    попытка
        ПроверитьЦенуПредложения(Настройки, Подготовка, 0, "CityRide", 52000)
    поймать Отказ: Ошибка
        Отказы = Отказы + 1
    ;
    попытка
        ПроверитьЦенуПредложения(Настройки, Подготовка, 1, "SportDrive", 40600)
    поймать Отказ: Ошибка
        Отказы = Отказы + 1
    ;
    Требовать(Отказы == 2, "TEST", "Начальная цена или границы следующих раундов не проверены")
    знч Данные = Прочитать(DATA_LITERAL)
    знч Цена = ПоследняяЦена(МассивJson(Данные.Получить("prices")), "bike", "sale", "rub", "2027-01-31T23:59:59")
    Требовать(ЧислоJson(Цена, "Цена") == 39123, "TEST", "Выбрана не последняя продажная цена ERP")
    знч Остаток = ОстатокПродукта(МассивJson(Данные.Получить("stock")), "bike", "finished")
    Требовать(ЧислоJson(Остаток, "availableQuantity") == 12, "TEST", "Складской остаток рассчитан неверно")
    знч Занятый = ОстатокПродукта(МассивJson(Данные.Получить("stock")), "bike", "finished", МассивJson(Данные.Получить("commitments")))
    Требовать(ЧислоJson(Занятый, "physicalQuantity") == 15 и ЧислоJson(Занятый, "availableQuantity") == 0,
        "TEST", "Заказ не вычтен из свободного остатка или вычтен из физического")
    возврат "PASS:ERP market reads and variable initial profile"
;
'''
        row = dict(Номенклатура_Key='bike', Склад_Key='finished', Характеристика_Key=ZERO, Назначение_Key=ZERO, ВНаличииBalance=15, КОтгрузкеBalance=3)
        fixture = {'prices': [{'RecordSet': [price(1), price(2), price(39123, '2027-01-16T00:00:00'), price(99999, '2027-02-01T00:00:00')]}],
                   'stock': [row, {**row, 'Склад_Key': 'other'}, {**row, 'Назначение_Key': 'other'}],
                   'commitments': [{**row, 'КОтгрузкеBalance': 15, 'ВРезервеBalance': 0}]}
        source = source.replace('DATA_LITERAL', literal(json.dumps(fixture, ensure_ascii=False)))
        home = Path(os.environ['ELEMENT_SCRIPT_HOME'])
        with tempfile.TemporaryDirectory(prefix='erp-market-native-') as temp:
            script = Path(temp) / 'Market.sbsl'
            script.write_text(source)
            args = [os.environ.get('ELEMENT_SCRIPT_JAVA', 'java'), '--add-opens', 'java.base/java.lang=ALL-UNNAMED', '--add-opens', 'java.base/java.nio=ALL-UNNAMED',
                    f'-Dlogs.root={temp}/logs', f'-Dexecutor.location={home}', f'-Dlogback.configurationFile={home}/config/logback.xml', '-cp', f'{home}/lib/*',
                    'com.e1c.g5rt.executor.boot.ExecutorBootstrap', '-c', '10.0', str(script)]
            result = subprocess.run(args, capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('PASS:ERP market reads and variable initial profile', result.stdout)


if __name__ == '__main__':
    unittest.main()
