"""Execute production market aggregation and VAT handling in Element Script."""
import argparse
from pathlib import Path
import re
import subprocess
import tempfile
import yaml
from test_erp_pull import ROOT, EXCHANGE, method


def analytics_source():
    source = (EXCHANGE.parent / 'Аналитика/АналитикаРынка.xbsl').read_text()
    fields = yaml.safe_load((EXCHANGE.parent / 'Аналитика/СтрокаАналитикиРынка.yaml').read_text())['Поля']
    structure = 'структура СтрокаАналитикиРынка\n' + '\n'.join(
        '    пер ' + f['Имя'] + ': ' + f['Тип'] for f in fields) + '\n;'
    protocol = (EXCHANGE / 'ПротоколERP.xbsl').read_text()
    body = '\n'.join([structure, *(method(protocol, n) for n in ('ОбъектJson', 'ЧислоJson', 'СтрокаJson', 'Требовать', 'МассивJson', 'Json', 'Прочитать', 'КаноническийJson')),
        method(source, 'Собрать'), method(source, 'Факт'),
        method((EXCHANGE / 'РынокЗаказов.xbsl').read_text(), 'СнимокСебестоимости')])
    body = body.replace('Игры.Объект', 'ИграАналитикиТеста').replace('УчастникиИгры.Ссылка', 'КомандаАналитикиТеста')
    body = body.replace('НастройкиERP.ПравилоПродукта(Игра, Код)', '<Строка, Объект?>{"vatRate": Игра.СтавкаНдс}')
    body = body.replace('ОбменERP.Продукт(Код).ЗагрузитьОбъект()!.Наименование', 'Код')
    body = body.replace('ПротоколERP.', '')
    body = re.sub(r'^импорт .+\n|^@[^\n]+\n', '', body, flags=re.M)
    return 'исключение Ошибка\n    знч Код: Строка\n    знч Поле: Строка\n    знч КодОтвета: Число\n;\n' + body


DRIVER = r'''
структура ИграАналитикиТеста
    пер СтавкаНдс: Строка = "22.00"
;
структура КомандаАналитикиТеста
    пер Ид: Ууид
;
метод Проверка(Условие: Булево, Текст: Строка)
    если не Условие
        выбросить новый ИсключениеНедопустимоеСостояние(Текст)
    ;
;
метод Скрипт(): Число
    знч Игра = новый ИграАналитикиТеста()
    знч Мы = новый КомандаАналитикиТеста(Ид = Ууид.Версия5("analytics:own"))
    знч Они = новый КомандаАналитикиТеста(Ид = Ууид.Версия5("analytics:other"))
    знч НашИд = Мы.Ид.ВСтроку()
    Проверка(Собрать(<Объект?>[], НашИд).Пусто(), "No sales must produce an empty table")
    знч Затраты: Соответствие<Строка, Объект?> = {"costs": <Строка, Объект?>{"CityRide": 61}, "includesVAT": Истина}
    знч Продажи = <Объект?>[
        Факт(Игра, 0, Мы, "CityRide", 1, 122, Затраты),
        Факт(Игра, 0, Мы, "CityRide", 3, 732, Затраты),
        Факт(Игра, 0, Они, "CityRide", 6, 2196, Затраты),
        Факт(Игра, 1, Они, "CityRide", 2, 244, Затраты),
        Факт(Игра, 1, Мы, "SportDrive", 2, 122, <Строка, Объект?>{"costs": <Строка, Объект?>{"SportDrive": 80}, "includesVAT": Ложь})]
    знч Итог = Собрать(Продажи, НашИд)
    Проверка(Итог.Размер() == 3 и Итог[0].Раунд == 1 и Итог[2].Раунд == 0,
        "Rounds/models mixed or history not sorted newest first")
    Проверка(СериализацияJson.ЗаписатьОбъект(Итог) == СериализацияJson.ЗаписатьОбъект(Собрать(Продажи, НашИд)),
        "Identical polls must serialize equally to preserve table source identity")
    знч Строка = Итог[2]
    Проверка(Строка.Продано == 4 и Строка.ПроданоНаРынке == 10 и Строка.Выручка == 854,
        "Own sales mixed with competitors")
    Проверка(Строка.ЦенаПродажи == 213.5 и Строка.СредняяЦенаРынка == 305,
        "Prices must be revenue divided by units, not mean of team or order prices")
    Проверка(Строка.Себестоимость == 50 и Строка.МаржаНаЕдиницу == 125
        и Строка.МаржаВсего == 500 и Строка.Маржинальность == 71.43,
        "VAT, unit margin, total margin or revenue-based margin percent is wrong")
    Проверка(Строка.ДоляРынка == 40 и Строка.ОтклонениеЦены == -30
        и Строка.МинимальнаяЦена == 122 и Строка.МаксимальнаяЦена == 366,
        "Price range, market share or price deviation is wrong")
    Проверка(Итог[0].Продано == 0 и Итог[0].ЦенаПродажи == Неопределено
        и Итог[0].МаржаВсего == Неопределено и Итог[0].СредняяЦенаРынка == 122,
        "Market-only model must not invent own prices/margins")
    Проверка(Итог[1].МаржаНаЕдиницу == -30 и Итог[1].Маржинальность == -60,
        "Losses were clamped or tax-exclusive cost was taxed twice")
    знч ДругаяКоманда = Собрать(Продажи, Они.Ид.ВСтроку())
    Проверка(ДругаяКоманда[2].ЦенаПродажи == 366 и ДругаяКоманда[2].СредняяЦенаРынка == 305,
        "Individual team selection changed market average")
    Продажи.Добавить(Факт(Игра, 0, Мы, "CityRide", 1, 122, <Строка, Объект?>{:}))
    знч Пробел = Собрать(Продажи, НашИд)[2]
    Проверка(Пробел.ЦенаПродажи == 195.2 и Пробел.МаржаВсего == Неопределено
        и Пробел.Маржинальность == Неопределено и Пробел.Себестоимость == Неопределено,
        "Missing cost for one sale must invalidate the full aggregate margin")
    знч НетНдс = Факт(Игра, 1, Мы, "CityRide", 1, 122,
        <Строка, Объект?>{"costs": <Строка, Объект?>{"CityRide": 50}})
    Проверка(НетНдс.Получить("unitCost") == Неопределено, "Unknown cost VAT treatment invented a margin")
    знч Нулевая = Факт(Игра, 2, Мы, "CityRide", 1, 0, Затраты)
    знч БезВыручки = Собрать(<Объект?>[Нулевая], НашИд)[0]
    Проверка(БезВыручки.МаржаНаЕдиницу == -50 и БезВыручки.Маржинальность == Неопределено
        и БезВыручки.ОтклонениеЦены == Неопределено, "Zero revenue divides by zero or invents margin percent")
    Нулевая.Вставить("quantity", 0)
    Проверка(Собрать(<Объект?>[Нулевая], НашИд).Пусто(), "Cancelled/zero sales were included")
    знч Кэш: Соответствие<Строка, Объект?> = {"costBasis": Затраты}
    знч Снимок = СнимокСебестоимости(Кэш)
    Затраты.Вставить("costs", <Строка, Объект?>{"CityRide": 999})
    Проверка(ЧислоJson(ОбъектJson(Прочитать(Снимок).Получить("costs")), "CityRide") == 61,
        "Historical order cost changed with live team cache")
    Проверка(СнимокСебестоимости(<Строка, Объект?>{:}) == "", "Legacy cache must preserve unknown cost")
    Консоль.Записать("PASS: production analytics: empty history, rounds/models, individual team prices, volume-weighted market price, VAT, negative/unknown/partial margins, zero revenue, cancelled sales and immutable cost snapshot")
    возврат 0
;
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', type=Path, required=True)
    parser.add_argument('--java', default='java')
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='market-analytics-') as temp:
        directory = Path(temp)
        script = directory / 'Аналитика.sbsl'
        script.write_text(analytics_source() + '\n' + DRIVER)
        command = [args.java, '-Xmx256m', '--add-opens', 'java.base/java.lang=ALL-UNNAMED',
                   '--add-opens', 'java.base/java.nio=ALL-UNNAMED', '-Dfile.encoding=UTF-8',
                   f'-Djava.io.tmpdir={directory}', f'-Dlogs.root={directory / "logs"}',
                   f'-Dexecutor.location={args.runtime}', f'-Dlogback.configurationFile={args.runtime / "config/logback.xml"}',
                   '-cp', str(args.runtime / 'lib/*'), 'com.e1c.g5rt.executor.boot.ExecutorBootstrap', '-c', '10.0', str(script)]
        result = subprocess.run(command, cwd=directory, capture_output=True, text=True, timeout=60)
        print(result.stdout)
        if result.returncode:
            Path('/private/tmp/market-analytics-failed.sbsl').write_text(script.read_text())
            print(result.stderr)
            raise SystemExit(result.returncode)


if __name__ == '__main__':
    main()
