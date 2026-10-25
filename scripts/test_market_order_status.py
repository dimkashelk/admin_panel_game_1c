"""Native XBSL order status polling and state transition fixtures for the market suite."""
from test_erp_pull import EXCHANGE, method, uid


def add_status_fixtures(erp):
    """Only the local HTTP double receives these test documents."""
    for index in range(25):
        order_id = uid('status-order-' + str(index))
        erp.add('Document_ЗаказКлиента', dict(
            Ref_Key=uid('status-doc-' + str(index)), Number=f'STATUS-{index}', Date='2026-10-15T00:00:00',
            Статус='Закрыт' if index % 2 == 0 else 'КОтгрузке', Posted=True,
            Комментарий='BICYCLE_PLAYER/' + order_id, Организация_Key=erp.organization, StatusTest=True))
        # ERP.add deliberately assigns a different GUID; the fixtures use explicit known keys.
        erp.rows['Document_ЗаказКлиента'][-1]['Ref_Key'] = uid('status-doc-' + str(index))


def status_source():
    odata = (EXCHANGE / 'НастройкаБазOData.xbsl').read_text()
    market = (EXCHANGE / 'РынокЗаказов.xbsl').read_text()
    body = '\n'.join(method(odata, name) for name in ('ПрочитатьСтатусыЗаказовOData', 'СостояниеЗаказаERP'))
    body += '\n' + method(market, 'ПрименитьСтатусERP').replace('ЗаказыРынка.Объект', 'ЗаказРезерва')
    tests = ['метод ПроверитьСтатусыЗаказов(АдресТеста: Строка)', '    знч Заказы = <Объект?>[]']
    for index in range(25):
        tests += [f'    Заказы.Добавить(<Строка, Объект?>{{"orderId": "{uid("status-order-" + str(index))}", "refKey": "{uid("status-doc-" + str(index))}"}})']
    tests += [
        '    знч Состояния = ПрочитатьСтатусыЗаказовOData(АдресТеста, "test-user", "test-password", "ДвижжОК ООО", Заказы)',
        '    ТребоватьПроверку(Состояния.Размер() == 25, "Status batch dropped orders after first page")',
        '    пер Индекс = 0',
        '    для Значение из Состояния',
        '        знч Состояние = ОбъектJson(Значение)',
        '        ТребоватьПроверку(Состояние.Получить("closed") == (Индекс % 2 == 0) и Состояние.Получить("error") == "", "Status read differs from ERP")',
        '        Индекс = Индекс + 1', '    ;',
        '    знч Заказ = новый ЗаказРезерва()',
        '    знч Начало = Момент.Сейчас()',
        '    ПрименитьСтатусERP(Заказ, ОбъектJson(Состояния[0]), Начало)',
        '    ТребоватьПроверку(Заказ.ЗакрытERP и Заказ.СтатусERP == "Закрыт" и Заказ.НомерERP == "STATUS-0" и Заказ.ВремяПроверкиERP == Начало, "Closed ERP order did not reach Element")',
        '    ПрименитьСтатусERP(Заказ, <Строка, Объект?>{"error": "HTTP failure"}, Начало + 1с)',
        '    ТребоватьПроверку(Заказ.ЗакрытERP и Заказ.ВремяПроверкиERP == Начало и Заказ.ОшибкаПроверкиERP == "HTTP failure", "Read failure erased confirmed state")',
        '    ПрименитьСтатусERP(Заказ, ОбъектJson(Состояния[1]), Начало + 2с)',
        '    ТребоватьПроверку(не Заказ.ЗакрытERP и Заказ.ОшибкаПроверкиERP == "" и Заказ.ВремяПроверкиERP == Начало + 2с, "Reopened ERP order remained in closed portfolio")',
        '    ТребоватьПроверку(не Заказ.ЗачтенЗакрытым, "Reopened order remained credited before deadline")',
        '    ПрименитьСтатусERP(Заказ, ОбъектJson(Состояния[0]), Начало + 3с)',
        '    ТребоватьПроверку(Заказ.ЗачтенЗакрытым, "On-time closed order not credited")',
        '    Заказ.ВремяФиксацииИтога = Начало + 4с',
        '    ПрименитьСтатусERP(Заказ, ОбъектJson(Состояния[1]), Начало + 5с)',
        '    ТребоватьПроверку(не Заказ.ЗакрытERP и Заказ.ЗачтенЗакрытым, "Late reopening changed fixed result")',
        '    знч Поздний = новый ЗаказРезерва(ВремяФиксацииИтога = Начало + 4с)',
        '    ПрименитьСтатусERP(Поздний, ОбъектJson(Состояния[0]), Начало + 5с)',
        '    ТребоватьПроверку(Поздний.ЗакрытERP и не Поздний.ЗачтенЗакрытым, "Late closure counted in final portfolio")',
        '    ПрименитьСтатусERP(Поздний, ОбъектJson(Состояния[0]), Начало + 6с)',
        '    ТребоватьПроверку(не Поздний.ЗачтенЗакрытым, "Repeated late polling changed final portfolio")',
        '    знч ЗапросЗаказа = <Строка, Объект?>{"orderId": "local", "refKey": "erp"}',
        '    знч Документ = <Строка, Объект?>{"Организация_Key": "org", "Комментарий": "BICYCLE_PLAYER/local", "Статус": "Закрыт", "Posted": Истина, "Number": "1", "Date": "2026-10-15T00:00:00"}',
        '    ТребоватьПроверку(СостояниеЗаказаERP(ЗапросЗаказа, Документ, "org").Получить("closed") == Истина, "Posted closed order rejected")',
        '    Документ.Вставить("Posted", Ложь)',
        '    ТребоватьПроверку(СостояниеЗаказаERP(ЗапросЗаказа, Документ, "org").Получить("closed") == Ложь, "Unposted order counted as closed")',
        '    Документ.Вставить("Posted", Истина)',
        '    Документ.Вставить("DeletionMark", Истина)',
        '    ТребоватьПроверку(СостояниеЗаказаERP(ЗапросЗаказа, Документ, "org").Получить("closed") == Ложь, "Deleted order counted as closed")',
        '    Документ.Вставить("DeletionMark", Ложь)',
        '    ТребоватьПроверку(СостояниеЗаказаERP(ЗапросЗаказа, Документ, "another-org").Получить("error") != "", "Foreign organization supplied closure")',
        '    Документ.Вставить("Комментарий", "BICYCLE_PLAYER/other-order")',
        '    ТребоватьПроверку(СостояниеЗаказаERP(ЗапросЗаказа, Документ, "org").Получить("error") != "", "Foreign order marker supplied closure")',
        '    ТребоватьПроверку(СостояниеЗаказаERP(ЗапросЗаказа, Неопределено, "org").Получить("error") != "", "Missing ERP document silently accepted")',
        '    Консоль.Записать("PASS: native OData status polling in batches, closed/reopened orders, preserved last confirmation on failure, deleted/unposted/missing/foreign document guards")', ';']
    return body + '\n' + '\n'.join(tests)
