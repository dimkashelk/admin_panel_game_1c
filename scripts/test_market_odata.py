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
    ПроверитьДинамическийРынок()
    ПроверитьПубликациюЗаказов()
    ПроверитьЗакреплениеЗаказа()
    // Полный режим продолжает требовать снимок; рынок использует готовность заказов.
    ТребоватьПроверку(не КомандаГотова(Истина, Неопределено, ""), "Full game accepted no snapshot")
    ТребоватьПроверку(КомандаГотова(Истина, Неопределено, "", Истина), "Market still requires snapshot")
    ТребоватьПроверку(не КомандаГотова(Истина, Неопределено, "ERP error", Истина), "Market ignored order error")
    ТребоватьПроверку(ПричинаОжидания(Истина, Неопределено, "", Истина) == "", "False market warning")
    ТребоватьПроверку(ПричинаОжидания(Ложь, Неопределено, "Orders pending", Истина) == "Orders pending", "Lost order reason")
    знч ОстаткиРынка = ПрочитатьОстаткиРынкаOData(АдресТеста, "test-user", "test-password", "ДвижжОК ООО", "2026-10-15")
    знч ВсеОстатки = МассивJson(ОстаткиРынка.Получить("offers"))
    ТребоватьПроверку(КаноническийJson(ОстаткиРынка).Длина() <= 1000, "Market stock cache exceeds metadata string limit")
    ТребоватьПроверку(ВсеОстатки.Размер() == 4 и ОбъектJson(ВсеОстатки[0]).Получить("availableQuantity") == 15,
        "Stock reader did not return four product offers")
    знч Строки = <Объект?>[{"productCode": "CityRide", "quantity": 2, "unitPrice": "39000.00"}]
    знч Первый = СоздатьЗаказПоСтрокамOData(АдресТеста, "test-user", "test-password", "ДвижжОК ООО",
        "%FIRST%", Строки, "2026-10-15")
    ТребоватьПроверку(Первый.Получить("posted") == Истина и Первый.Получить("alreadyPosted") == Ложь, "New order not posted")
    знч Повтор = СоздатьЗаказПоСтрокамOData(АдресТеста, "test-user", "test-password", "ДвижжОК ООО",
        "%FIRST%", Строки, "2026-10-15")
    ТребоватьПроверку(Повтор.Получить("refKey") == Первый.Получить("refKey") и Повтор.Получить("alreadyPosted") == Истина,
        "Repeated order did not reuse posted document")
    знч СтарыйЗаказ = СоздатьЗаказПоСтрокамOData(АдресТеста, "test-user", "test-password", "ДвижжОК ООО",
        "%LEGACY%", Строки, "2026-10-15")
    ТребоватьПроверку(СтарыйЗаказ.Получить("alreadyPosted") == Истина, "Legacy posted order was not reused")
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


MARKET_DRIVER = r'''
метод ПроверитьДинамическийРынок()
    // Reference drivers: Модель рынка.xlsx, Рынок!B12:G15.
    для Параметры из [[120, 52000, 35000, 75000, 1, 1], [35, 82000, 60000, 120000, 1, 0.92],
        [95, 90000, 65000, 130000, 1, 1.05], [45, 88000, 65000, 125000, 1, 1.12]]
        знч Спрос = (Параметры[0] * Параметры[5]).Округлить(0, РежимОкругления.ПоловинаВверх)
        знч Равновесие = ДинамическаяЦена(Параметры[1], Параметры[2], Параметры[3], Параметры[4], Спрос, Спрос)
        знч Дефицит = ДинамическаяЦена(Параметры[1], Параметры[2], Параметры[3], Параметры[4], Спрос, Спрос / 2)
        знч Избыток = ДинамическаяЦена(Параметры[1], Параметры[2], Параметры[3], Параметры[4], Спрос, Спрос * 2)
        ТребоватьПроверку(Равновесие == Параметры[1] и Дефицит > Равновесие и Избыток < Равновесие
            и Дефицит <= Параметры[3] и Избыток >= Параметры[2], "Price does not react to demand/supply within Excel bounds")
    ;
    ТребоватьПроверку(ДинамическаяЦена(52000, 1, 1000000, 2, 120, 30) == 104000, "Inverse elasticity formula is wrong")
    ТребоватьПроверку(ДинамическаяЦена(52000, 35000, 75000, 0.001, 120, 1) == 75000, "Low elasticity overflowed price bounds")
    ТребоватьПроверку(ДинамическаяЦена(52000, 35000, 75000, 0, 120, 30) == 52000, "Zero elasticity has no defined reference fallback")
    ТребоватьПроверку(РазмерНовогоЗаказа(1000, 5000, 0, 5) == 5, "Market published entire demand as one order")
    ТребоватьПроверку(РазмерНовогоЗаказа(10, 4, 3, 5) == 1 и РазмерНовогоЗаказа(10, 4, 4, 5) == 0,
        "Queue exceeds available supply")
    ТребоватьПроверку(РазмерНовогоЗаказа(3, 100, 2, 5) == 1, "Queue exceeds remaining demand")
    ТребоватьПроверку(ВыбратьМодельПакета([0, 0, 0, 0], 17, 1, 1) == -1, "Selected exhausted market")
    пер Номер = 1
    пока Номер <= 100
        знч Пакет = ПараметрыПакета(17, 1, Номер, 4, 7, 11, 10, 20)
        знч Повтор = ПараметрыПакета(17, 1, Номер, 4, 7, 11, 10, 20)
        ТребоватьПроверку(Пакет[1] >= 7 и Пакет[1] <= 11 и Пакет[2] >= 10 и Пакет[2] <= 20,
            "Game size/interval ranges were ignored")
        ТребоватьПроверку(Пакет[0] == Повтор[0] и Пакет[1] == Повтор[1] и Пакет[2] == Повтор[2], "Regenerated different order from same seed")
        ТребоватьПроверку(ВыбратьМодельПакета([0, 0, 15, 0], 17, 1, Номер) == 2, "Selected model with no demand")
        Номер = Номер + 1
    ;
    знч Постоянный = ПараметрыПакета(17, 1, 1, 4, 3, 3, 45, 45)
    ТребоватьПроверку(Постоянный[1] == 3 и Постоянный[2] == 45, "Equal range bounds do not give fixed settings")
    для Пределы из [[0, 5, 30, 90], [1, 0, 30, 90], [5, 1, 30, 90], [1, 5, 90, 30], [1.5, 5, 30, 90], [1, 5, 0, 90]]
        пер Отклонено = Ложь
        попытка
            ПроверитьПараметрыЗаказов(Пределы[0], Пределы[1], Пределы[2], Пределы[3])
        поймать Сбой: Исключение
            Отклонено = Истина
        ;
        ТребоватьПроверку(Отклонено, "Invalid size/interval settings accepted")
    ;
    Консоль.Записать("PASS: Excel inverse-demand pricing, bounds, small batches, game-specific intervals and deterministic model selection")
;
метод ПроверитьЗакреплениеЗаказа()
    знч Игра = новый ИграРезерва()
    знч ПерваяКоманда = новый КомандаРезерва(Игра = Игра, Ид = "first")
    знч ДругаяКоманда = новый КомандаРезерва(Игра = Игра, Ид = "second")
    знч Заказ = новый ЗаказРезерва(Игра = Игра)
    знч ПерваяПопытка = РезервироватьТеста(ПерваяКоманда, Заказ, "phase")
    ТребоватьПроверку(Заказ.Участник == ПерваяКоманда и Заказ.Статус == "Отправляется", "Order was not reserved before ERP call")
    для Команда из [ДругаяКоманда, ПерваяКоманда]
        пер Отклонено = Ложь
        попытка
            РезервироватьТеста(Команда, Заказ, "phase")
        поймать Сбой: Исключение
            Отклонено = Истина
        ;
        ТребоватьПроверку(Отклонено и Заказ.Участник == ПерваяКоманда и Заказ.ЧислоПопыток == 1,
            "Concurrent claim or retry bypassed owner/lease")
    ;
    Заказ.НачалоПопытки = Момент.Сейчас() - 16м
    знч НоваяПопытка = РезервироватьТеста(ПерваяКоманда, Заказ, "phase")
    ПодтвердитьТеста(ПерваяКоманда, Заказ, ПерваяПопытка, Неопределено, "old response", Истина)
    ТребоватьПроверку(Заказ.Попытка == НоваяПопытка и Заказ.Статус == "Отправляется", "Stale response overwrote new attempt")
    ПодтвердитьТеста(ПерваяКоманда, Заказ, НоваяПопытка,
        <Строка, Объект?>{"posted": Истина, "refKey": "erp-id", "number": "ERP-1"}, "")
    ТребоватьПроверку(Заказ.Статус == "Проведен в ERP" и РезервироватьТеста(ПерваяКоманда, Заказ, "phase") == "",
        "Confirmed order can be sent again")
    знч ДругойЗаказ = новый ЗаказРезерва(Игра = Игра)
    знч ПопыткаОтказа = РезервироватьТеста(ПерваяКоманда, ДругойЗаказ, "phase")
    ПодтвердитьТеста(ПерваяКоманда, ДругойЗаказ, ПопыткаОтказа, Неопределено, "No stock", Истина)
    ТребоватьПроверку(ДругойЗаказ.Участник == Неопределено и ДругойЗаказ.Статус == "Свободен", "Proven pre-create failure did not release order")
    знч ПовторнаяПопытка = РезервироватьТеста(ДругаяКоманда, ДругойЗаказ, "phase")
    ПодтвердитьТеста(ДругаяКоманда, ДругойЗаказ, ПовторнаяПопытка, Неопределено, "Unknown HTTP result")
    ТребоватьПроверку(ДругойЗаказ.Участник == ДругаяКоманда, "Unknown ERP result released order to another team")
    Консоль.Записать("PASS: original reservation/confirmation methods with shared in-memory records, owner/lease/stale-response guards")
;
метод ПроверитьИгрокаРезерва(Команда: КомандаРезерва): ИграРезерва
    возврат Команда.Игра
;
метод ПродажиОткрытыРезерва(Игра: ИграРезерва): Булево
    возврат Игра.Статус == "running"
;
метод ЗавершеноРезерва(Команда: КомандаРезерва, Раунд: Число, Токен: Строка): Булево
    возврат Ложь
;
метод ЗаписатьКомандуРезерва(Игра: ИграРезерва, Заказ: ЗаказРезерва)
;
структура ИграРезерва
    пер СвободныйРынок: Булево = Истина
    пер Статус: Строка = "running"
    пер ТокенФазы: Строка = "phase"
    пер Фаза: Строка = "sales"
    пер ТекущийРаунд: Число = 1
;
структура КомандаРезерва
    пер Ид: Строка
    пер Игра: ИграРезерва
;
структура ЗаказРезерва
    пер Игра: ИграРезерва
    пер Раунд: Число = 1
    пер Участник: КомандаРезерва?
    пер Статус: Строка = "Свободен"
    пер НачалоПопытки: Момент?
    пер ВремяПолучения: Момент?
    пер ЧислоПопыток: Число = 0
    пер Попытка: Строка = ""
    пер Ошибка: Строка = ""
    пер ВнешнийДокумент: Строка = ""
    пер НомерERP: Строка = ""
;
'''


def reservation_source():
    source = (EXCHANGE / 'РынокЗаказов.xbsl').read_text()
    body = '\n'.join(method(source, name) for name in ('Резервировать', 'Подтвердить'))
    body = body.replace('метод Резервировать(', 'метод РезервироватьТеста(').replace('метод Подтвердить(', 'метод ПодтвердитьТеста(')
    body = body.replace('УчастникиИгры.Ссылка', 'КомандаРезерва').replace('ЗаказыРынка.Ссылка', 'ЗаказРезерва').replace('Игры.Ссылка', 'ИграРезерва')
    body = re.sub(r'^    исп (?:КонтекстДоступа|Транзакции).+\n', '', body, flags=re.M)
    body = body.replace('ДоступКабинета.ПроверитьИгрока(', 'ПроверитьИгрокаРезерва(').replace('ДоступКабинета.Завершено(', 'ЗавершеноРезерва(')
    body = body.replace('СсылкаИгры.ЗагрузитьОбъект(Истина)!', 'СсылкаИгры').replace('СсылкаЗаказа.ЗагрузитьОбъект(Истина)!', 'СсылкаЗаказа')
    body = body.replace('    Заказ.Записать()\n', '').replace('ПродажиОткрыты(', 'ПродажиОткрытыРезерва(').replace('ЗаписатьКомандуERP(', 'ЗаписатьКомандуРезерва(')
    for key, value in {'СостоянияИгры.Идет': 'running', 'ФазыИгры.Продажи': 'sales', 'ФазыИгры.СтартоваяПродажа': 'initial_sales'}.items():
        body = body.replace(key, '"' + value + '"')
    return body



GENERATION_DRIVER = r'''
метод ПроверитьПубликациюЗаказов()
    знч Игра = новый ИграГенерации()
    Игра.ТекущийРаунд = новый РаундГенерации(НомерРаунда = 1)
    Игра.СледующийЗаказ = Момент.Сейчас() - 120с
    для Код из ["CityRide", "SportDrive", "CargoMax", "TourPro"]
        Игра.ПараметрыРынка.Добавить(новый ПравилоГенерации(Продукт = новый ПродуктГенерации(Код = Код)))
    ;
    Игра.ДанныеГотовы = Ложь
    ОбновитьГенерацию(Игра)
    ТребоватьПроверку(Игра.Заказы.Пусто() и Игра.НомерПакетаЗаказов == 0, "Missing stock data published market orders")
    Игра.ДанныеГотовы = Истина
    Игра.МаксимумОднойКоманды = 2
    ОбновитьГенерацию(Игра)
    ТребоватьПроверку(Игра.Заказы.Пусто(), "Order cannot be fulfilled by any single team")
    Игра.МаксимумОднойКоманды = 10
    Игра.СледующийЗаказ = Момент.Сейчас() - 1с
    ОбновитьГенерацию(Игра)
    ТребоватьПроверку(Игра.Заказы.Размер() == 1, "Worker delay flooded market instead of publishing one small order")
    для Заказ из Игра.Заказы
        ТребоватьПроверку(Заказ.Количество == 3 и Заказ.Цена >= 35000 и Заказ.Цена <= 75000,
            "Published order ignored configured quantity/price bounds")
        ТребоватьПроверку(Заказ.ОснованиеЦены != "" и Заказ.ХешОснованияЦены == Хеш(Заказ.ОснованиеЦены), "Published price has no reproducible input hash")
        ТребоватьПроверку(Заказ.ОснованиеЦены.Длина() <= 1000, "Price basis exceeds metadata string limit")
    ;
    знч ДоПовтора = Игра.Заказы.Размер()
    ОбновитьГенерацию(Игра)
    ОбновитьГенерацию(Игра)
    ТребоватьПроверку(Игра.Заказы.Размер() == ДоПовтора, "Second cabinet/worker created duplicate due orders")
    пер Шаг = 0
    пока Шаг < 50
        Игра.СледующийЗаказ = Момент.Сейчас() - 1с
        ОбновитьГенерацию(Игра)
        Шаг = Шаг + 1
    ;
    знч ПослеЗаполнения = Игра.Заказы.Размер()
    для Правило из Игра.ПараметрыРынка
        ТребоватьПроверку(ПосчитатьЗаказыГенерации(Игра, Правило.Продукт!, Ложь).Количество <= 10,
            "Shared queue exceeds available stock")
    ;
    Игра.СледующийЗаказ = Момент.Сейчас() - 10с
    Игра.Статус = "paused"
    ОбновитьГенерацию(Игра)
    ТребоватьПроверку(Игра.Заказы.Размер() == ПослеЗаполнения, "Pause did not stop order generation")
    Игра.Статус = "running"
    Игра.Дедлайн = Момент.Сейчас() - 1с
    ОбновитьГенерацию(Игра)
    ТребоватьПроверку(Игра.Заказы.Размер() == ПослеЗаполнения, "Expired phase published new orders")
    Консоль.Записать("PASS: original generation method with in-memory persistence, shared queue caps, configured batches, repeated reads, pause/deadline guards")
;
метод ПредложениеГенерации(Игра: ИграГенерации): Соответствие<Строка, Объект?>?
    если не Игра.ДанныеГотовы
        возврат Неопределено
    ;
    возврат {"quantities": <Строка, Объект?>{"CityRide": 10, "SportDrive": 10, "CargoMax": 10, "TourPro": 10},
        "maximumQuantities": <Строка, Объект?>{"CityRide": Игра.МаксимумОднойКоманды, "SportDrive": Игра.МаксимумОднойКоманды,
            "CargoMax": Игра.МаксимумОднойКоманды, "TourPro": Игра.МаксимумОднойКоманды}, "sourcesHash": "stock-inputs"}
;
метод ПосчитатьЗаказыГенерации(Игра: ИграГенерации, Продукт: ПродуктГенерации, Получен: Булево): СуммаГенерации
    пер Количество = 0
    для Заказ из Игра.Заказы
        если Заказ.Продукт!.Код == Продукт.Код и Заказ.Получен == Получен
            Количество = Количество + Заказ.Количество
        ;
    ;
    возврат новый СуммаГенерации(Количество = Количество)
;
метод СохранитьЗаказГенерации(Игра: ИграГенерации, Заказ: ЗаказГенерации)
    Игра.Заказы.Добавить(Заказ)
;
структура ИграГенерации
    пер МаксимумОднойКоманды: Число = 10
    пер ДанныеГотовы: Булево = Истина
    пер СвободныйРынок: Булево = Истина
    пер Статус: Строка = "running"
    пер Фаза: Строка = "sales"
    пер СостояниеФазы: Строка = "open"
    пер Дедлайн: Момент?
    пер СледующийЗаказ: Момент?
    пер ТекущийРаунд: РаундГенерации?
    пер Seed: Число = 17
    пер НомерПакетаЗаказов: Число = 0
    пер МинКоличествоЗаказа: Число = 3
    пер МаксКоличествоЗаказа: Число = 3
    пер МинИнтервалЗаказов: Число = 20
    пер МаксИнтервалЗаказов: Число = 20
    пер ПараметрыРынка: Массив<ПравилоГенерации> = []
    пер ТрендыПоРаундам: Массив<ТрендГенерации> = []
    пер Заказы: Массив<ЗаказГенерации> = []
;
структура ПравилоГенерации
    пер Продукт: ПродуктГенерации?
    пер БазовыйСпрос: Число = 1000
    пер Тренд: Число = 1
    пер РеферентнаяЦена: Число = 52000
    пер МинимальнаяЦена: Число = 35000
    пер МаксимальнаяЦена: Число = 75000
    пер Эластичность: Число = 1
;
структура ПродуктГенерации
    пер Код: Строка
;
структура РаундГенерации
    пер НомерРаунда: Число
;
структура ТрендГенерации
    пер НомерРаунда: Число
    пер Продукт: ПродуктГенерации?
    пер Тренд: Число
;
структура СуммаГенерации
    пер Количество: Число
;
структура ЗаказГенерации
    пер Игра: ИграГенерации?
    пер Раунд: РаундГенерации?
    пер Продукт: ПродуктГенерации?
    пер НомерПакета: Число
    пер Наименование: Строка
    пер Количество: Число
    пер Цена: Число
    пер ОснованиеЦены: Строка
    пер ХешОснованияЦены: Строка
    пер ВремяПоявления: Момент?
    пер Статус: Строка
    пер Получен: Булево = Ложь
;
'''


def generation_source():
    source = (EXCHANGE / 'РынокЗаказов.xbsl').read_text()
    body = '\n'.join(method(source, name) for name in ('Обновить', 'ПродажиОткрыты'))
    body = body.replace('метод Обновить(', 'метод ОбновитьГенерацию(').replace('ПродажиОткрыты(', 'ПродажиГенерации(')
    body = body.replace('Игры.Ссылка', 'ИграГенерации').replace('Игры.Объект', 'ИграГенерации')
    body = re.sub(r'^    исп (?:КонтекстДоступа|Транзакции).+\n', '', body, flags=re.M)
    body = body.replace('Ссылка.ЗагрузитьОбъект(Истина)!', 'Ссылка').replace('Раунд.ЗагрузитьОбъект()!', 'Раунд').replace('Продукт.ЗагрузитьОбъект()!', 'Продукт')
    body = body.replace('ПредложениеВсехКоманд(', 'ПредложениеГенерации(')
    for received in (True, False):
        op = '!=' if received else '=='
        expression = r'Запрос\{ВЫБРАТЬ СУММА\(Количество\).*?И Участник ' + re.escape(op) + r' НЕОПРЕДЕЛЕНО\}\.Выполнить\(\)\.ПервыйИлиНеопределено\(\)!'
        body, count = re.subn(expression, f'ПосчитатьЗаказыГенерации(Игра, Продукт, {"Истина" if received else "Ложь"})', body, count=1, flags=re.S)
        assert count == 1, 'Generation query seam changed'
    body = body.replace('новый ЗаказыРынка.Объект()', 'новый ЗаказГенерации()')
    body = body.replace('Заказ.Записать()', 'СохранитьЗаказГенерации(Игра, Заказ)').replace('    Игра.Записать()\n', '    // In-memory game is already saved.\n')
    for key, value in {'СостоянияИгры.Идет': 'running', 'ФазыИгры.Продажи': 'sales', 'ФазыИгры.СтартоваяПродажа': 'initial_sales', 'СостоянияФазы.Открыта': 'open'}.items():
        body = body.replace(key, '"' + value + '"')
    return body



def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', type=Path, required=True)
    parser.add_argument('--java', default='java')
    args = parser.parse_args()
    ids = {name: uid('market-' + name) for name in ('first', 'recover', 'lost', 'conflict', 'deleted', 'stock', 'legacy')}
    driver = DRIVER
    for key, value in ids.items():
        driver = driver.replace('%' + key.upper() + '%', value)
    odata = (EXCHANGE / 'НастройкаБазOData.xbsl').read_text()
    summary = (EXCHANGE.parent / 'Управление/СводкиИгры.xbsl').read_text()
    market = (EXCHANGE / 'РынокЗаказов.xbsl').read_text()
    checks = (EXCHANGE.parent / 'Данные/ПроверкиДанных.xbsl').read_text()
    protocol = (EXCHANGE / 'ПротоколERP.xbsl').read_text().split('@ВПроекте\nметод УникальныеПоля', 1)[0]
    body = '\n'.join([protocol, (EXCHANGE / 'КонтрактERP.xbsl').read_text(),
                      method((EXCHANGE / 'ПрофильНастройкиOData.xbsl').read_text(), 'Получить').replace('метод Получить(', 'метод ПолучитьПрофиль('),
                      *(method(odata, name) for name in ('СоздатьЗаказПоСтрокамOData', 'ПроверитьСтрокиЗаказаERP',
                        'ТоварыЗаказаERP', 'Найти', 'Разрешить', 'Совпадает', 'ПровестиДокумент',
                        'ЗапросOData', 'ПрочитатьВсе', 'ОстатокПродукта', 'СоединитьСтроки', 'ПрочитатьОстаткиРынкаOData')),
                      method(summary, 'КомандаГотова'), method(summary, 'ПричинаОжидания'), lifecycle_source(),
                      *(method(market, name) for name in ('ДинамическаяЦена', 'РазмерНовогоЗаказа', 'ВыбратьМодельПакета', 'ПараметрыПакета')),
                      method(checks, 'СтабильныйПриоритет'), method(checks, 'ПроверитьПараметрыЗаказов'),
                      reservation_source(), generation_source(), MARKET_DRIVER, GENERATION_DRIVER, driver])
    body = re.sub(r'^импорт .+\n|^@[^\n]+\n', '', body, flags=re.M)
    body = body.replace('ПротоколERP.', '').replace('КонтрактERP.', '')
    body = body.replace('ПрофильНастройкиOData.Получить()', 'ПолучитьПрофиль()')
    body = body.replace('Транзакции.ЕстьАктивная()', 'Ложь').replace('НормализоватьАдрес(Адрес)', 'Адрес')
    body = body.replace('ПроверкиДанных.Требовать(', 'ТребоватьПроверку(').replace('СнимкиERP.Ссылка?', 'Строка?')
    body = body.replace('ПроверкиДанных.СтабильныйПриоритет', 'СтабильныйПриоритет').replace('ПроверкиДанных.ПроверитьПараметрыЗаказов', 'ПроверитьПараметрыЗаказов')
    body = body.replace('        Требовать(Значение >=', '        ТребоватьПроверку(Значение >=').replace('    Требовать(Макс', '    ТребоватьПроверку(Макс')
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
                if document['Комментарий'].endswith(ids['first']):
                    legacy = erp.add(entity, copy.deepcopy(document))
                    legacy.update(Комментарий='BICYCLE_PLAYER/' + ids['legacy'], Posted=True,
                                  ЦенаВключаетНДС=False, НалогообложениеНДС='ПродажаНеОблагаетсяНДС')
                    legacy['Товары'][0]['СуммаНДС'] = 0
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
    assert len(documents) == 6, 'Retry created a duplicate or stock failure created a document'
    for name in ('first', 'recover', 'lost'):
        docs = [d for d in documents if d['Комментарий'] == 'BICYCLE_PLAYER/' + ids[name]]
        assert len(docs) == 1 and docs[0]['Posted'], name
        assert sum(event[1:] == (docs[0]['Ref_Key'], 'Post') for event in erp.posts) == 1, name
        assert docs[0]['СуммаДокумента'] == 78000
        assert docs[0]['ЦенаВключаетНДС'] is True
        assert docs[0]['НалогообложениеНДС'] == 'ПродажаОблагаетсяНДС'
        assert docs[0]['Товары'][0]['СуммаНДС'] == 14065.57
        assert docs[0]['Товары'][0]['СуммаСНДС'] == 78000
    legacy = next(d for d in documents if d['Комментарий'].endswith(ids['legacy']))
    assert legacy['Posted'] and legacy['ЦенаВключаетНДС'] is False and legacy['Товары'][0]['СуммаНДС'] == 0
    assert not any(event[1] == legacy['Ref_Key'] for event in erp.posts), 'Legacy order was changed or posted again'
    assert not any(d.get('Posted') for d in documents if d['Комментарий'].endswith((ids['conflict'], ids['deleted'])))
    assert all(path.startswith(('Catalog_', 'Document_', 'AccumulationRegister_')) for _, path, _, _ in requests)
    print(f'PASS: {len(requests)} OData requests; 3 conducted orders, no retry duplicates or HTTP-extension calls')


if __name__ == '__main__':
    main()
