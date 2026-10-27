"""Run production XBSL order closure against an isolated local OData ERP.

No real ERP writes. The receipt/line property allowlist was read from the
explicitly authorized unposted ERP 1550925 diagnostic documents.
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

NAMES = ('ЗакрытьИОплатитьЗаказOData', 'ПрочитатьОтборЗакрытия', 'КлючТовараЗакрытия',
         'ДвиженияДокументаЗакрытия', 'ПроверитьПолнуюОтгрузкуЗаказа', 'ПоляОплатыЗаказа',
         'ДобавитьЧастьОплаты', 'ПроверитьПоступлениеЗаказа', 'ПроверитьДвиженияОплатыЗаказа',
         'ПроверитьУсловияЗакрытияЗаказа', 'ПроверитьОтсутствиеДругойОплаты', 'ПрочитатьСтатусыЗаказовOData', 'СостояниеЗаказаERP')
ZERO = '00000000-0000-0000-0000-000000000000'
ORDER = 'Document_ЗаказКлиента'
SALE = 'Document_РеализацияТоваровУслуг'
PAYMENT = 'Document_ПоступлениеБезналичныхДенежныхСредств'
CASH = 'AccumulationRegister_ДенежныеСредстваБезналичные'
STOCK = 'AccumulationRegister_ТоварыНаСкладах'

DRIVER = r'''
метод ТребоватьПроверку(Условие: Булево, Текст: Строка)
    если не Условие
        выбросить новый ИсключениеНедопустимоеСостояние(Текст)
    ;
;
метод Скрипт(Адрес: Строка, ТекстСлучаев: Строка): Число
    для Значение из МассивJson(Прочитать(ТекстСлучаев).Получить("cases"))
        знч Случай = ОбъектJson(Значение)
        знч Имя = СтрокаJson(Случай, "name")
        знч ОжидаемаяОшибка = СтрокаJson(Случай, "error")
        знч Соответствия = <Строка, Строка>{:}
        для Пара из ОбъектJson(Случай.Получить("mappings"))
            Соответствия.Вставить(Пара.Ключ, Пара.Значение как Строка)
        ;
        пер Код = ""
        попытка
            знч Результат = ЗакрытьИОплатитьЗаказOData(Адрес + Имя + "/", "test-user", "test-password",
                "Учебная организация", СтрокаJson(Случай, "id"), СтрокаJson(Случай, "order"),
                123000, "2026-10-15", МассивJson(Случай.Получить("lines")), Соответствия)
            ТребоватьПроверку(Результат.Получить("closed") == Истина и Результат.Получить("paymentConfirmed") == Истина,
                "Unconfirmed order/payment reported successful: " + Имя)
        поймать Сбой: Ошибка
            Код = Сбой.Код
        ;
        ТребоватьПроверку(Код == ОжидаемаяОшибка, "Expected " + ОжидаемаяОшибка + ", got " + Код + ": " + Имя)
        если Случай.Получить("retry") == Истина или ОжидаемаяОшибка == ""
            знч Повтор = ЗакрытьИОплатитьЗаказOData(Адрес + Имя + "/", "test-user", "test-password",
                "Учебная организация", СтрокаJson(Случай, "id"), СтрокаJson(Случай, "order"),
                123000, "2026-10-15", МассивJson(Случай.Получить("lines")), Соответствия)
            ТребоватьПроверку(Повтор.Получить("paymentRefKey") == Случай.Получить("id"), "Retry changed payment GUID")
        ;
        если ОжидаемаяОшибка == "" или Случай.Получить("retry") == Истина или Имя == "cash-not-reflected"
            знч Статусы = ПрочитатьСтатусыЗаказовOData(Адрес + Имя + "/", "test-user", "test-password", "Учебная организация",
                <Объект?>[{"orderId": Случай.Получить("id"), "refKey": Случай.Получить("order"),
                    "paymentRefKey": Случай.Получить("id"), "amount": 123000}])
            ТребоватьПроверку(ОбъектJson(Статусы[0]).Получить("paymentConfirmed") == (Имя != "cash-not-reflected"),
                "Payment polling did not verify bank register: " + Имя)
        ;
        Консоль.Записать("PASS: " + Имя)
    ;
    возврат 0
;
'''


def source():
    odata = (EXCHANGE / 'НастройкаБазOData.xbsl').read_text()
    protocol = (EXCHANGE / 'ПротоколERP.xbsl').read_text().split('@ВПроекте\nметод УникальныеПоля', 1)[0]
    names = (*NAMES, 'ПроверитьСтрокиЗаказаERP', 'Найти', 'Совпадает', 'СоединитьСтроки', 'ЗапросOData', 'ПровестиДокумент')
    body = '\n'.join([protocol, (EXCHANGE / 'КонтрактERP.xbsl').read_text(),
                      *(method(odata, name) for name in names), DRIVER])
    body = re.sub(r'^импорт .+\n|^@[^\n]+\n', '', body, flags=re.M)
    return (body.replace('ПротоколERP.', '').replace('КонтрактERP.', '')
            .replace('ПроверкиДанных.Требовать(', 'ТребоватьПроверку(')
            .replace('Транзакции.ЕстьАктивная()', 'Ложь').replace('НормализоватьАдрес(Адрес)', 'Адрес'))


def fixture(name):
    org, partner, counterparty, currency, warehouse, bank = [uid(x) for x in ('org', 'partner', 'counterparty', 'rub', 'warehouse', 'bank')]
    order_id = uid('closure-' + name)
    item_ids = [uid('closure-item-' + str(i)) for i in range(2)]
    lines = [dict(productCode='CityRide', quantity=2, unitPrice='39000.00'),
             dict(productCode='SportDrive', quantity=1, unitPrice='45000.00')]
    goods = [dict(LineNumber=str(i+1), Номенклатура_Key=item_ids[i], Характеристика_Key=ZERO,
                  Склад_Key=warehouse, Количество=row['quantity'], Цена=float(row['unitPrice']),
                  СуммаСНДС=row['quantity'] * float(row['unitPrice']), Отменено=False)
             for i, row in enumerate(lines)]
    order = dict(Ref_Key=order_id, Number='ORDER-1', Date='2026-10-15T12:00:00', Posted=True,
                 DeletionMark=False, DataVersion='version-1', Организация_Key=org, Партнер_Key=partner,
                 Контрагент_Key=counterparty, Валюта_Key=currency, Склад_Key=warehouse,
                 Комментарий='BICYCLE_PLAYER/' + order_id, Статус='КОтгрузке', Товары=goods, СуммаДокумента=123000)
    rows = {ORDER: [order], PAYMENT: [], SALE: [], STOCK: [], CASH: [],
            'Catalog_Организации': [dict(Ref_Key=org, Description='Учебная организация', DeletionMark=False)],
            'Catalog_Валюты': [dict(Ref_Key=currency, Description='руб.', DeletionMark=False)],
            'Catalog_БанковскиеСчетаОрганизаций': [dict(Ref_Key=bank, Owner_Key=org, ВалютаДенежныхСредств_Key=currency, DeletionMark=False)],
            'Catalog_СтатьиДвиженияДенежныхСредств': [dict(Ref_Key=uid('article'), PredefinedDataName='ПоступлениеОплатыОтКлиента', DeletionMark=False)],
            'Catalog_ОбъектыРасчетов': []}
    for i, good in enumerate(goods):
        sale_id = uid(name+'-shipment-'+str(i))
        row = {**good, 'ЗаказКлиента': order_id, 'ЗаказКлиента_Type': 'StandardODATA.' + ORDER}
        rows[SALE].append(dict(Ref_Key=sale_id, Number='SALE-'+str(i), Posted=True, DeletionMark=False,
                               Организация_Key=org, Склад_Key=warehouse, ЗаказКлиента='',
                               ЗаказКлиента_Type='StandardODATA.Undefined', Товары=[row]))
        rows[STOCK].append(dict(Recorder=sale_id, Recorder_Type='StandardODATA.'+SALE,
            RecordSet=[dict(Active=True, RecordType='Expense', Сторно=False, Номенклатура_Key=good['Номенклатура_Key'],
                            Характеристика_Key=ZERO, Склад_Key=warehouse, ВНаличии=good['Количество'])]))
        if name == 'invoice-settlements':
            rows['Catalog_ОбъектыРасчетов'].append(dict(Ref_Key=uid('settlement-'+sale_id), DeletionMark=False,
                Организация_Key=org, Партнер_Key=partner, Контрагент=counterparty,
                Контрагент_Type='StandardODATA.Catalog_Контрагенты', ВалютаВзаиморасчетов_Key=currency,
                Объект=sale_id, Объект_Type='StandardODATA.'+SALE))
    if name != 'invoice-settlements':
        rows['Catalog_ОбъектыРасчетов'].append(dict(Ref_Key=uid('settlement-'+order_id), DeletionMark=False,
            Организация_Key=org, Партнер_Key=partner, Контрагент=counterparty,
            Контрагент_Type='StandardODATA.Catalog_Контрагенты', ВалютаВзаиморасчетов_Key=currency,
            Объект=order_id, Объект_Type='StandardODATA.'+ORDER))
    error = ''
    if name == 'partial': rows[SALE][0]['Товары'][0]['Количество'] = 1; rows[STOCK][0]['RecordSet'][0]['ВНаличии'] = 1; error='ODATA_SHIPMENT'
    if name == 'no-warehouse-movement': rows[STOCK][0]['RecordSet'] = []; error='ODATA_SHIPMENT'
    if name == 'foreign-shipment': rows[SALE][0]['Товары'][0]['ЗаказКлиента'] = uid('other-order'); error='ODATA_SHIPMENT'
    if name == 'unposted-shipment': rows[SALE][0]['Posted'] = False; error='ODATA_SHIPMENT'
    if name == 'wrong-item': rows[SALE][0]['Товары'][0]['Номенклатура_Key'] = uid('other-item'); error='ODATA_SHIPMENT'
    if name == 'over-delivery': rows[SALE][0]['Товары'][0]['Количество'] = 3; rows[STOCK][0]['RecordSet'][0]['ВНаличии'] = 3; error='ODATA_SHIPMENT'
    if name == 'cancelled-line': order['Товары'][0]['Отменено'] = True; error='ODATA_SHIPMENT'
    if name == 'changed-price': order['Товары'][0]['Цена'] = 40000; error='ODATA_ORDER'
    if name == 'wrong-org': order['Организация_Key'] = uid('other-org'); error='ODATA_ORDER'
    if name == 'deleted-order': order['DeletionMark'] = True; error='ODATA_ORDER'
    if name == 'unposted-order': order['Posted'] = False; error='ODATA_ORDER'
    if name == 'manual-closed-unshipped': order['Статус'] = 'Закрыт'; rows[SALE] = []; error='ODATA_SHIPMENT'
    if name == 'missing-settlement': rows['Catalog_ОбъектыРасчетов'] = []; error='ODATA_SETTLEMENT'
    if name == 'wrong-settlement': rows['Catalog_ОбъектыРасчетов'][0]['Контрагент'] = uid('other-party'); error='ODATA_SETTLEMENT'
    if name == 'ambiguous-bank': rows['Catalog_БанковскиеСчетаОрганизаций'].append(copy.deepcopy(rows['Catalog_БанковскиеСчетаОрганизаций'][0])); error='ODATA_CONFLICT'
    if name == 'occupied-payment-guid': rows[PAYMENT] = [dict(Ref_Key=order_id, Комментарий='manual payment', DeletionMark=False)]; error='ODATA_CONFLICT'
    if name == 'manual-payment':
        rows[PAYMENT]=[dict(Ref_Key=uid('manual-payment'), Posted=True, DeletionMark=False,
            Организация_Key=org, Комментарий='Manual payment', ДокументОснование=order_id,
            ДокументОснование_Type='StandardODATA.'+ORDER, РасшифровкаПлатежа=[])]
        error='ODATA_PAYMENT'
    if name == 'version-conflict': error='ODATA_HTTP'
    if name in ['lost-create-response','lost-post-response']: error='ODATA_HTTP'
    if name == 'cash-not-reflected': error='ODATA_VERIFY'
    if name == 'lost-order-post-response': error='ODATA_HTTP'
    if name == 'foreign-currency': order['Валюта_Key']=uid('usd'); error='ODATA_ORDER'
    if name == 'paginated-shipments':
        rows[SALE] = [dict(Ref_Key=uid('irrelevant-sale-'+str(i)), Posted=True, DeletionMark=False,
                          Организация_Key=org, Склад_Key=warehouse, Товары=[]) for i in range(105)] + rows[SALE]
    return dict(name=name, id=order_id, order=order_id, mappings=dict(zip(['CityRide','SportDrive'],item_ids)),
                lines=lines, error=error, retry=name.startswith('lost-')), rows


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime',type=Path,required=True)
    args=parser.parse_args()
    names=['full-shipment','invoice-settlements','partial','no-warehouse-movement','foreign-shipment','unposted-shipment',
           'wrong-item','over-delivery','cancelled-line','changed-price','wrong-org','deleted-order','unposted-order',
           'manual-closed-unshipped','missing-settlement','wrong-settlement','ambiguous-bank','occupied-payment-guid',
           'version-conflict','lost-create-response','lost-post-response','lost-order-post-response',
           'cash-not-reflected','foreign-currency','paginated-shipments','manual-payment']
    cases=[]; states={}; calls=[]; failed=set()
    for name in names:
        case, rows=fixture(name); cases.append(case); states[name]=rows
    schema=json.loads((ROOT/'contracts/erp-order-closure-schema.json').read_text())['entities'][PAYMENT]
    class Handler(BaseHTTPRequestHandler):
        protocol_version='HTTP/1.1'
        def log_message(self,*args): pass
        def respond(self,value,status=200):
            data=json.dumps(value,ensure_ascii=False).encode()
            self.send_response(status); self.send_header('Content-Length',str(len(data)))
            self.send_header('Content-Type','application/json'); self.end_headers(); self.wfile.write(data)
        def do_GET(self): self.handle_request()
        def do_POST(self): self.handle_request()
        def do_PATCH(self): self.handle_request()
        def handle_request(self):
            parsed=urlsplit(self.path); parts=unquote(parsed.path).strip('/').split('/',1)
            name,path=parts; rows=states[name]; entity=path.split('(')[0]
            params={k:v[0] for k,v in parse_qs(parsed.query).items()}
            raw=self.rfile.read(int(self.headers.get('Content-Length',0)))
            body=json.loads(raw) if raw else None
            calls.append((name,self.command,path,copy.deepcopy(body)))
            assert self.headers.get('Authorization')=='Basic '+base64.b64encode(b'test-user:test-password').decode()
            if entity in [CASH,STOCK]:
                key=re.search(r"Recorder='([^']+)'",path)[1]
                record=next((r for r in rows[entity] if r['Recorder']==key),None)
                self.respond(record if record else {},200 if record else 404); return
            if "(guid'" in path:
                key=path.split("(guid'",1)[1].split("'",1)[0]
                doc=next((r for r in rows.get(entity,[]) if r['Ref_Key']==key),None)
                if not doc: self.respond({},404); return
                if self.command=='GET': self.respond(doc); return
                if self.command=='PATCH':
                    assert entity==ORDER and body=={'Статус':'Закрыт'}
                    assert self.headers.get('If-Match')=='version-1'
                    if name=='version-conflict': self.respond({},412); return
                    doc.update(body); doc['DataVersion']='version-2'; self.respond(doc); return
                assert path.endswith('/Post()') and body is None
                doc['Posted']=True
                if entity==ORDER and name=='lost-order-post-response' and name not in failed:
                    failed.add(name); self.respond({},503); return
                if entity==PAYMENT:
                    assert doc['ПроведеноБанком'] is True
                    cash=doc['СуммаДокумента'] if name!='cash-not-reflected' else 0
                    rows[CASH]=[dict(Recorder=key,Recorder_Type='StandardODATA.'+PAYMENT,
                        RecordSet=[dict(Active=True, RecordType='Receipt', Сторно=False,
                            Организация_Key=doc['Организация_Key'], БанковскийСчет_Key=doc['БанковскийСчет_Key'],
                            Сумма=cash, КЗачислению=0)])]
                    if name=='lost-post-response' and name not in failed:
                        failed.add(name); self.respond({},503); return
                self.respond({}); return
            if self.command=='POST':
                assert entity==PAYMENT
                assert set(body)<=set(schema['properties']), 'Receipt uses fields not confirmed in ERP'
                for row in body['РасшифровкаПлатежа']:
                    assert set(row)<=set(schema['tables']['РасшифровкаПлатежа'])
                assert 'Posted' not in body and body['ПроведеноБанком'] is True
                assert body['ДатаПроведенияБанком']=='2026-10-15T00:00:00'
                assert body['ДатаВходящегоДокумента']=='2026-10-15T00:00:00'
                assert not rows[PAYMENT], 'Duplicate payment created'
                rows[PAYMENT].append({**body,'Number':'PAY-1','Posted':False,'DeletionMark':False})
                if name=='lost-create-response' and name not in failed:
                    failed.add(name); self.respond({},503); return
                self.respond(rows[PAYMENT][-1],201); return
            found=copy.deepcopy(rows.get(entity,[])); condition=params.get('$filter','')
            marker=re.search(r"substringof\('([^']+)',Комментарий\)",condition)
            if marker: found=[r for r in found if marker[1] in r.get('Комментарий','')]
            for k,v in re.findall(r"(\w+) eq (true|false)",condition): found=[r for r in found if r.get(k) is (v=='true')]
            for k,v in re.findall(r"(\w+) eq (?:guid)?'([^']*)'",condition): found=[r for r in found if r.get(k)==v]
            start=int(params.get('$skip',0)); size=int(params.get('$top',100))
            self.respond({'value':found[start:start+size]})
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
    try:
        with tempfile.TemporaryDirectory(prefix='erp-closure-') as temp:
            folder=Path(temp); script=folder/'ЗакрытиеOData.sbsl'; script.write_text(source())
            command=['java','-Xmx256m','--add-opens','java.base/java.lang=ALL-UNNAMED','--add-opens','java.base/java.nio=ALL-UNNAMED',
                     '-Dfile.encoding=UTF-8',f'-Djava.io.tmpdir={folder}',f'-Dlogs.root={folder/"logs"}',
                     f'-Dexecutor.location={args.runtime}',f'-Dlogback.configurationFile={args.runtime/"config/logback.xml"}',
                     '-cp',str(args.runtime/'lib/*'),'com.e1c.g5rt.executor.boot.ExecutorBootstrap','-c','10.0',str(script),
                     f'http://127.0.0.1:{server.server_port}/',json.dumps({'cases':cases})]
            result=subprocess.run(command,cwd=folder,capture_output=True,text=True,timeout=120)
            print(result.stdout)
            if result.returncode: print(result.stderr); raise SystemExit(result.returncode)
    finally: server.shutdown(); server.server_close(); thread.join(timeout=2)
    for case in cases:
        writes=[c for c in calls if c[0]==case['name'] and c[1]!='GET']
        if case['error'] and case['name'] not in ['lost-create-response','lost-post-response','lost-order-post-response','cash-not-reflected','version-conflict']:
            assert not writes, ('Validation failed after ERP writes',case['name'])
        creates=[c for c in writes if c[1]=='POST' and c[2]==PAYMENT]
        posts=[c for c in writes if c[1]=='POST' and c[2].startswith(PAYMENT+'(')]
        assert len(creates)<=1 and len(posts)<=1, ('Duplicate cash effect',case['name'])
    print(f'PASS: {len(cases)} production closure cases; {len(calls)} local OData requests; no duplicate payments')

if __name__=='__main__': main()
