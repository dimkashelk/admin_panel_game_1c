"""Native XBSL tests of order generation and creation with a fake OData transport.

No ERP writes. UI/DB concurrency needs two clients of a compiled application.
"""
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1] / 'dimkashelk' / 'Игра'


def method(path, name):
    code = path.read_text()
    return code[code.index('метод ' + name + '('):].split('\n@')[0]


def native(source):
    home = Path(os.environ['ELEMENT_SCRIPT_HOME'])
    with tempfile.TemporaryDirectory(prefix='player-market-native-') as temp:
        script = Path(temp) / 'Test.sbsl'
        script.write_text(source)
        args = [os.environ.get('ELEMENT_SCRIPT_JAVA', 'java'), '--add-opens', 'java.base/java.lang=ALL-UNNAMED',
                '--add-opens', 'java.base/java.nio=ALL-UNNAMED', f'-Dlogs.root={temp}/logs',
                f'-Dexecutor.location={home}', f'-Dlogback.configurationFile={home}/config/logback.xml',
                '-cp', f'{home}/lib/*', 'com.e1c.g5rt.executor.boot.ExecutorBootstrap', '-c', '10.0', str(script)]
        result = subprocess.run(args, capture_output=True, text=True, timeout=60)
        if result.returncode or 'PASS' not in result.stdout:
            raise AssertionError(result.stdout + result.stderr)


@unittest.skipUnless(os.environ.get('ELEMENT_SCRIPT_HOME'), 'Native Element Script runtime not configured')
class NativePlayerMarketTests(unittest.TestCase):
    def test_generator(self):
        source = method(ROOT / 'Данные/ПроверкиДанных.xbsl', 'СтабильныйПриоритет')
        source += '\n' + method(ROOT / 'Обмен/РынокЗаказов.xbsl', 'ПараметрыПакета')
        source = source.replace('ПроверкиДанных.', '')
        source += '''
метод Требовать(Условие: Булево, Текст: Строка)
    если не Условие
        выбросить новый ИсключениеНедопустимоеСостояние(Текст)
    ;
;
метод Скрипт(): Строка
    знч Модели = <Число>[]
    пер Номер = 1
    пока Номер <= 1000
        знч А = ПараметрыПакета(42, 2, Номер, 4)
        знч Б = ПараметрыПакета(42, 2, Номер, 4)
        Требовать(А[0] == Б[0] и А[1] == Б[1] и А[2] == Б[2], "Нестабильный заказ")
        Требовать(А[0] >= 0 и А[0] < 4 и А[1] >= 1 и А[1] <= 5 и А[2] >= 30 и А[2] <= 90, "Диапазон")
        если не Модели.Содержит(А[0])
            Модели.Добавить(А[0])
        ;
        Номер = Номер + 1
    ;
    Требовать(Модели.Размер() == 4, "Пропущена модель")
    возврат "PASS: 1000 deterministic batches and parameter bounds"
;
'''
        native(source)

    def test_native_odata_create_and_retry(self):
        base = ROOT / 'Обмен'
        source = (base / 'ПротоколERP.xbsl').read_text().split('@ВПроекте\nметод Ответ(')[0]
        source += '\n' + (base / 'КонтрактERP.xbsl').read_text()
        create = method(base / 'НастройкаБазOData.xbsl', 'СоздатьЗаказРынкаOData')
        create = create.replace('ДатаДокумента: Строка):', 'ДатаДокумента: Строка, Тест: Соответствие<Строка, Объект?>):')
        create = re.sub(r'    знч Соединение = КлиентHttp\..*?\n    знч Маркер', '    знч Соединение = Тест\n    знч Маркер', create, flags=re.S)
        source += '\n' + create.replace('ПрофильНастройкиOData.Получить()', 'ПрофильТеста()')
        source += '\n' + method(base / 'НастройкаБазOData.xbsl', 'ПровестиДокумент').replace('Соединение: КлиентHttp', 'Соединение: Соответствие<Строка, Объект?>')
        source += '\n' + method(base / 'НастройкаБазOData.xbsl', 'Совпадает')
        source = source.replace('импорт Данные\n', '').replace('@ВПроекте', '@Глобально').replace('ПротоколERP.', '').replace('КонтрактERP.', '')
        source += '''
метод ПрофильТеста(): Строка
    знч Узлы = <Объект?>[]
    для Ключ из ["organization", "finished_goods", "customer", "counterparty_customer", "agreement_customer", "rub", "vat", "pcs", "CityRide"]
        Узлы.Добавить(<Строка, Объект?>{"key": Ключ, "entity": Ключ, "match": <Строка, Объект?>{:}})
    ;
    возврат Json(<Строка, Объект?>{"nodes": Узлы})
;
метод Разрешить(Значение: Объект?, Ссылки: Соответствие<Строка, Строка>): Объект?
    возврат Значение
;
метод Найти(Тест: Соответствие<Строка, Объект?>, Ключ: Строка, Отбор: Соответствие<Строка, Объект?>): Соответствие<Строка, Объект?>?
    если Ключ == "Document_ЗаказКлиента"
        возврат Тест.ПолучитьИлиНеопределено("doc") как Соответствие<Строка, Объект?>?
    ;
    знч Запись: Соответствие<Строка, Объект?> = {"Ref_Key": Ключ, "DeletionMark": Ложь}
    если Ключ == "agreement_customer"
        Запись.Вставить("Статус", "Действует")
        для Пара из <Строка, Строка>{"organization": "Организация_Key", "finished_goods": "Склад_Key", "customer": "Партнер_Key", "counterparty_customer": "Контрагент_Key", "rub": "Валюта_Key"}
            Запись.Вставить(Пара.Значение, Пара.Ключ)
        ;
    ;
    возврат Запись
;
метод ПрочитатьВсе(Тест: Соответствие<Строка, Объект?>, Путь: Строка): Массив<Объект?>
    возврат <Объект?>[Тест]
;
метод ОстатокПродукта(Остатки: Массив<Объект?>, Ид: Строка, Склад: Строка, Обязательства: Массив<Объект?>): Соответствие<Строка, Объект?>
    возврат <Строка, Объект?>{"availableQuantity": ОбъектJson(Остатки[0]).ПолучитьИлиУмолчание("stock", 10)}
;
метод ЗапросOData(Тест: Соответствие<Строка, Объект?>, Метод: Строка, Путь: Строка,
    Параметры: Соответствие<Строка, Объект?> = {:}, Тело: Объект? = Неопределено): Соответствие<Строка, Объект?>
    если Метод == "POST" и Путь == "Document_ЗаказКлиента"
        знч Документ = ОбъектJson(Тело)
        Документ.Вставить("Posted", Ложь)
        Документ.Вставить("Number", "0000-000001")
        Документ.Вставить("DeletionMark", Ложь)
        Тест.Вставить("doc", Документ)
        Тест.Вставить("creates", (Тест.ПолучитьИлиУмолчание("creates", 0) как Число) + 1)
        если Тест.ПолучитьИлиНеопределено("lostCreate") == Истина
            Тест.Вставить("lostCreate", Ложь)
            выбросить новый Ошибка("Потерян ответ POST", 503, "LOST_RESPONSE")
        ;
        возврат Документ
    ;
    знч Документ = ОбъектJson(Тест.Получить("doc"))
    если Метод == "POST"
        Требовать(Путь.ЗаканчиваетсяНа("/Post()") и Тело == Неопределено, "TEST", "Неверное проведение")
        Тест.Вставить("posts", (Тест.ПолучитьИлиУмолчание("posts", 0) как Число) + 1)
        если Тест.ПолучитьИлиНеопределено("refuse") != Истина
            Документ.Вставить("Posted", Истина)
            Тест.Вставить("doc", Документ)
        ;
        если Тест.ПолучитьИлиНеопределено("lostPost") == Истина
            Тест.Вставить("lostPost", Ложь)
            выбросить новый Ошибка("Потерян ответ Post()", 503, "LOST_RESPONSE")
        ;
    ;
    возврат Документ
;
метод Проверить(Тест: Соответствие<Строка, Объект?>): Соответствие<Строка, Объект?>
    возврат СоздатьЗаказРынкаOData("unused", "test", "test", "ДвижжОК ООО",
        "11111111-1111-4111-8111-111111111111", "CityRide", 3, 40000, "2027-01-15", Тест)
;
метод Отказ(Тест: Соответствие<Строка, Объект?>)
    пер БылОтказ = Ложь
    попытка
        Проверить(Тест)
    поймать ОшибкаERP: Исключение
        БылОтказ = Истина
    ;
    Требовать(БылОтказ, "TEST", "Ожидаемый отказ не произошёл")
;
метод Скрипт(): Строка
    знч Тест: Соответствие<Строка, Объект?> = {:}
    Требовать(Проверить(Тест).Получить("posted") == Истина, "TEST", "Нет проведения")
    Требовать(Проверить(Тест).Получить("alreadyPosted") == Истина и Тест.Получить("creates") == 1 и Тест.Получить("posts") == 1, "TEST", "Дубликат")
    знч Документ = ОбъектJson(Тест.Получить("doc"))
    Документ.Вставить("СуммаДокумента", 1)
    Тест.Вставить("doc", Документ)
    Отказ(Тест)
    Документ.Вставить("СуммаДокумента", 120000)
    Документ.Вставить("DeletionMark", Истина)
    Тест.Вставить("doc", Документ)
    Отказ(Тест)
    Документ.Вставить("DeletionMark", Ложь)
    знч Товар = ОбъектJson(МассивJson(Документ.Получить("Товары"))[0])
    Товар.Вставить("Количество", 2)
    Документ.Вставить("Товары", <Объект?>[Товар])
    Тест.Вставить("doc", Документ)
    Отказ(Тест)
    для Признак из ["lostCreate", "lostPost", "refuse"]
        знч Сбой: Соответствие<Строка, Объект?> = {:}
        Сбой.Вставить(Признак, Истина)
        Отказ(Сбой)
        Сбой.Вставить(Признак, Ложь)
        Требовать(Проверить(Сбой).Получить("posted") == Истина и Сбой.Получить("creates") == 1, "TEST", "Дубликат после сбоя")
    ;
    знч НетЗапаса: Соответствие<Строка, Объект?> = {"stock": 0}
    Отказ(НетЗапаса)
    Требовать(не НетЗапаса.СодержитКлюч("creates"), "TEST", "Запись без запаса")
    возврат "PASS: create, post, refusal, conflicts, stock and lost responses"
;
'''
        native(source)


if __name__ == '__main__':
    unittest.main()
