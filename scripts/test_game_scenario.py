"""Execute production scenario-to-ERP mapping in Element Script, without network access."""
import argparse
from pathlib import Path
import re
import subprocess
import tempfile
from test_erp_pull import EXCHANGE, method
from test_erp_setup_xbsl import setup_source

DRIVER = r'''
метод ТребоватьПроверку(Условие: Булево, Текст: Строка)
    если не Условие
        выбросить новый ИсключениеНедопустимоеСостояние(Текст)
    ;
;
метод Скрипт(): Число
    знч Профиль = Прочитать(ПолучитьПрофиль())
    знч Условия = ОбъектJson(Профиль.Получить("initial"))
    ПрименитьУсловияКПрофилю(Профиль, Условия)
    ТребоватьПроверку(Совпадает(Профиль, Прочитать(ПолучитьПрофиль())), "Default scenario changed template fields")
    знч НовыеОстатки = <Объект?>[]
    для Элемент из МассивJson(Условия.Получить("balances"))
        знч Остаток = ОбъектJson(Элемент)
        если Остаток.Получить("warehouseId") != "workshop"
            Остаток.Вставить("quantity", "3")
            Остаток.Вставить("unitCost", "125.50")
        ;
        НовыеОстатки.Добавить(Остаток)
    ;
    Условия.Вставить("balances", НовыеОстатки)
    знч НовыеЦены = <Объект?>[]
    для Элемент из МассивJson(Условия.Получить("initialPrices"))
        знч Цена = ОбъектJson(Элемент)
        Цена.Вставить("price", "43210.00")
        НовыеЦены.Добавить(Цена)
    ;
    Условия.Вставить("initialPrices", НовыеЦены)
    ПрименитьУсловияКПрофилю(Профиль, Условия)
    пер Проверено = 0
    для Элемент из МассивJson(Профиль.Получить("nodes"))
        знч Узел = ОбъектJson(Элемент)
        знч Ключ = СтрокаJson(Узел, "key")
        если не ["initial_stock", "initial_components", "initial_supplier_prices", "initial_prices"].Содержит(Ключ)
            продолжить
        ;
        знч Поля = ОбъектJson(Узел.Получить("fields"))
        для Товар из МассивJson(Поля.Получить(Ключ == "initial_prices" ? "Товары2_5" : "Товары"))
            знч Строка = ОбъектJson(Товар)
            если Ключ == "initial_prices" и Строка.Получить("ВидЦены_Key") == "@sales_price"
                ТребоватьПроверку(Строка.Получить("Цена") == 43210, "Scenario sales price was ignored")
            иначе
                ТребоватьПроверку(Строка.Получить("Цена") == 125.5, "Scenario cost was ignored")
                если Ключ == "initial_stock" или Ключ == "initial_components"
                    ТребоватьПроверку(Строка.Получить("Количество") == 3 и Строка.Получить("КоличествоУпаковок") == 3
                        и Строка.Получить("Сумма") == 376.5, "Scenario quantity or amount was ignored")
                ;
            ;
            Проверено = Проверено + 1
        ;
    ;
    ТребоватьПроверку(Проверено == 56, "Incomplete scenario mapping")
    // Профиль и условия намеренно разделяют initial, как при создании игры.
    знч Частичный = Прочитать(ПолучитьПрофиль())
    знч ЧастичныеУсловия = ОбъектJson(Частичный.Получить("initial"))
    ЧастичныеУсловия.Вставить("balances", <Объект?>[
        {"itemCode": "CityRide", "warehouseId": "finished_goods", "quantity": "7", "unitCode": "pcs", "unitCost": "125.50"},
        {"itemCode": "FRAME_CARGO", "warehouseId": "purchases", "quantity": "2.5", "unitCode": "pcs", "unitCost": "200.00"},
        {"itemCode": "BAT_300", "warehouseId": "purchases", "quantity": "0", "unitCode": "pcs", "unitCost": "100.00"}])
    ПрименитьУсловияКПрофилю(Частичный, ЧастичныеУсловия)
    ТребоватьПроверку(МассивJson(ЧастичныеУсловия.Получить("balances")).Размер() == 26, "Partial scenario broke exchange contract")
    пер Номенклатура = 0
    пер СтрокОстатков = 0
    для Элемент из МассивJson(Частичный.Получить("nodes"))
        знч Узел = ОбъектJson(Элемент)
        если Узел.Получить("entity") == "Catalog_Номенклатура"
            Номенклатура = Номенклатура + 1
        ;
        знч Ключ = СтрокаJson(Узел, "key")
        если ["initial_stock", "initial_components"].Содержит(Ключ)
            для Товар из МассивJson(ОбъектJson(Узел.Получить("fields")).Получить("Товары"))
                знч Строка = ОбъектJson(Товар)
                ТребоватьПроверку(["@CityRide", "@FRAME_CARGO"].Содержит(СтрокаJson(Строка, "Номенклатура_Key")), "Template stock leaked into scenario")
                ТребоватьПроверку(Строка.Получить("Количество") == (Ключ == "initial_stock" ? 7 : 2.5), "Partial quantity ignored")
                СтрокОстатков = СтрокОстатков + 1
            ;
        ;
    ;
    ТребоватьПроверку(Номенклатура == 15 и СтрокОстатков == 2, "Nomenclature depends on opening stock")
    // Ни компоненты, ни готовая продукция не обязательны в стартовом запасе.
    для Код из ["CityRide", "FRAME_CARGO", ""]
        знч Пустой = Прочитать(ПолучитьПрофиль())
        знч ПустыеУсловия = ОбъектJson(Пустой.Получить("initial"))
        знч Выбранные = <Объект?>[]
        для Элемент из МассивJson(ПустыеУсловия.Получить("balances"))
            если ОбъектJson(Элемент).Получить("itemCode") == Код
                Выбранные.Добавить(Элемент)
            ;
        ;
        ПустыеУсловия.Вставить("balances", Выбранные)
        ПрименитьУсловияКПрофилю(Пустой, ПустыеУсловия)
        для Элемент из МассивJson(Пустой.Получить("nodes"))
            знч Узел = ОбъектJson(Элемент)
            знч Ключ = СтрокаJson(Узел, "key")
            ТребоватьПроверку(Ключ != (Код == "CityRide" ? "initial_components" : "initial_stock"), "Empty stock document created")
            ТребоватьПроверку(Код != "" или Ключ != "initial_components", "Empty components document created")
        ;
        ТребоватьПроверку(МассивJson(ПустыеУсловия.Получить("balances")).Размер() == 26, "Missing zero balances")
    ;
    Условия.Вставить("balances", <Объект?>[{"itemCode": "UNKNOWN", "warehouseId": "purchases"}])
    пер Отклонено = Ложь
    попытка
        ПрименитьУсловияКПрофилю(Профиль, Условия)
    поймать Ошибка: Исключение
        Отклонено = Истина
    ;
    ТребоватьПроверку(Отклонено, "Unknown initial resource accepted")
    Консоль.Записать("PASS: full/partial/empty scenario, all nomenclature, exact positive stock, zero exchange balances and unknown resource rejection")
    возврат 0
;
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', type=Path, required=True)
    parser.add_argument('--java', default='java')
    args = parser.parse_args()
    body = method((EXCHANGE / 'НастройкиERP.xbsl').read_text(), 'ПрименитьУсловияКПрофилю')
    body = re.sub(r'^@[^\n]+\n', '', body, flags=re.M).replace('ПротоколERP.', '')
    body = body.replace('ПроверкиДанных.Требовать', 'ТребоватьПроверку')
    body = body.replace('ПрофильНастройкиOData.Получить()', 'ПолучитьПрофиль()')
    with tempfile.TemporaryDirectory(prefix='game-scenario-') as temp:
        directory = Path(temp)
        script = directory / 'Сценарий.sbsl'
        script.write_text(setup_source() + '\n' + body + DRIVER)
        command = [args.java, '-Xmx256m', '--add-opens', 'java.base/java.lang=ALL-UNNAMED',
                   '--add-opens', 'java.base/java.nio=ALL-UNNAMED', '-Dfile.encoding=UTF-8',
                   f'-Djava.io.tmpdir={directory}', f'-Dlogs.root={directory / "logs"}',
                   f'-Dexecutor.location={args.runtime}', f'-Dlogback.configurationFile={args.runtime / "config/logback.xml"}',
                   '-cp', str(args.runtime / 'lib/*'), 'com.e1c.g5rt.executor.boot.ExecutorBootstrap',
                   '-c', '10.0', str(script)]
        result = subprocess.run(command, cwd=directory, capture_output=True, text=True, timeout=60)
        print(result.stdout)
        if result.returncode:
            print(result.stderr)
            raise SystemExit(result.returncode)


if __name__ == '__main__':
    main()
