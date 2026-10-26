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
    Условия.Вставить("balances", <Объект?>[])
    пер Отклонено = Ложь
    попытка
        ПрименитьУсловияКПрофилю(Профиль, Условия)
    поймать Ошибка: Исключение
        Отклонено = Истина
    ;
    ТребоватьПроверку(Отклонено, "Incomplete scenario silently used template data")
    Консоль.Записать("PASS: scenario quantities, costs, amounts, supplier/sales prices and missing-resource rejection before ERP writes")
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
