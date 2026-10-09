#!/usr/bin/env python3
"""Read ERP sale prices and warehouse balances without installing an extension.

No writes. Credentials are read only from ERP_ODATA_USER/PASSWORD.
The setup profile supplies lookup names, never price or quantity values.
"""
import argparse
from datetime import datetime
from decimal import Decimal
import json
import os
from pathlib import Path

from setup_erp_odata import OData, ROOT, ZERO, SetupError, resolve

PRODUCTS = ('CityRide', 'SportDrive', 'CargoMax', 'TourPro')


def read_all(client, path, params=None):
    result = []
    for offset in range(0, 20000, 100):
        page = client.request(path, params={**(params or {}), '$top': 100, '$skip': offset})['value']
        result.extend(page)
        if len(page) < 100:
            return result
    raise SetupError('Слишком много строк; чтение не завершено: ' + path)


def latest_price(record_sets, item_id, price_type, currency, at):
    candidates = [r for rs in record_sets for r in rs['RecordSet']
                  if r['Active'] and r['Period'] <= at
                  and r['Номенклатура_Key'] == item_id and r['ВидЦены_Key'] == price_type
                  and r['ХарактеристикаЦО_Key'] == ZERO and r['УпаковкаЦО_Key'] == ZERO
                  and r['СерияЦО_Key'] == ZERO and r['МаркетинговоеМероприятие_Key'] == ZERO]
    if not candidates:
        raise SetupError('Нет действующей продажной цены: ' + item_id)
    period = max(r['Period'] for r in candidates)
    latest = [r for r in candidates if r['Period'] == period]
    if len({(str(r['Цена']), r['Валюта_Key'], r['Упаковка_Key']) for r in latest}) != 1:
        raise SetupError('Неоднозначная последняя цена: ' + item_id)
    row = latest[0]
    if row['Валюта_Key'] != currency or row['Упаковка_Key'] != ZERO:
        raise SetupError('Цена задана в другой валюте или упаковке: ' + item_id)
    price = Decimal(str(row['Цена']))
    if price <= 0:
        raise SetupError('Продажная цена должна быть положительной: ' + item_id)
    return price, period


def available_stock(rows, item_id, warehouse, commitments=()):
    matching = [r for r in rows if r['Номенклатура_Key'] == item_id
                and r['Склад_Key'] == warehouse and r['Характеристика_Key'] == ZERO
                and r['Назначение_Key'] == ZERO]
    physical = sum((Decimal(str(r['ВНаличииBalance'])) for r in matching), Decimal(0))
    shipping = sum((Decimal(str(r['КОтгрузкеBalance'])) for r in matching), Decimal(0))
    orders = sum((Decimal(str(r['КОтгрузкеBalance'])) + Decimal(str(r['ВРезервеBalance']))
                  for r in commitments if r['Номенклатура_Key'] == item_id and r['Склад_Key'] == warehouse
                  and r['Характеристика_Key'] == ZERO and r['Назначение_Key'] == ZERO), Decimal(0))
    # The warehouse and shipping registers describe overlapping stages of the
    # same shipments. Do not count the same commitment twice.
    shipping = max(shipping, orders)
    available = max(Decimal(0), physical - shipping)
    if available != available.to_integral_value():
        raise SetupError('Остаток велосипедов не целый: ' + item_id)
    return physical, shipping, int(available)


def snapshot(client, at, organization_name):
    datetime.strptime(at, '%Y-%m-%dT%H:%M:%S')
    profile = json.loads((ROOT / 'contracts/erp-odata-setup.json').read_text())
    refs = {**profile['defaults'], 'organizationName': organization_name}
    needed = {'organization', 'finished_goods', 'agreement_customer', 'customer',
              'counterparty_customer', 'rub', 'pcs', 'set', 'battery', 'power'}
    needed.update(i['refKey'][1:] for i in profile['items'])
    needed.update(i['characteristicId'][1:] for i in profile['items'] if i['characteristicId'])
    records = {}
    for node in profile['nodes']:
        if node['key'] not in needed:
            continue
        row = client.find(node['entity'], resolve(node['match'], refs))
        if not row or row.get('DeletionMark'):
            raise SetupError('Не найдена активная настройка ERP: ' + node['key'])
        refs[node['key']] = row['Ref_Key']
        records[node['key']] = row
    agreement = records['agreement_customer']
    for key, field in [('organization', 'Организация_Key'), ('customer', 'Партнер_Key'),
                       ('counterparty_customer', 'Контрагент_Key'), ('finished_goods', 'Склад_Key'),
                       ('rub', 'Валюта_Key')]:
        if agreement[field] != refs[key]:
            raise SetupError('Соглашение ERP не соответствует выбранной области: ' + field)
    if agreement['Статус'] != 'Действует':
        raise SetupError('Соглашение покупателя не действует')
    price_type = client.read('Catalog_ВидыЦен', agreement['ВидЦен_Key'])
    if price_type.get('DeletionMark') or price_type['Статус'] != 'Действует':
        raise SetupError('Вид продажной цены ERP не действует')
    if price_type['ЦенаВключаетНДС'] != agreement['ЦенаВключаетНДС']:
        raise SetupError('Настройки НДС вида цены и соглашения различаются')
    mappings = resolve(profile['items'], refs)
    prices = read_all(client, 'InformationRegister_ЦеныНоменклатуры25')
    stock = read_all(client, "AccumulationRegister_ТоварыНаСкладах/Balance(Period=datetime'" + at + "')")
    commitments = read_all(client, "AccumulationRegister_ТоварыКОтгрузке/Balance(Period=datetime'" + at + "')")
    offers = []
    for code in PRODUCTS:
        item = records[code]
        if item['ЕдиницаИзмерения_Key'] != refs['pcs']:
            raise SetupError('Единица велосипедов отличается от шт.: ' + code)
        price, period = latest_price(prices, item['Ref_Key'], agreement['ВидЦен_Key'], agreement['Валюта_Key'], at)
        physical, shipping, available = available_stock(stock, item['Ref_Key'], agreement['Склад_Key'], commitments)
        offers.append(dict(productCode=code, refKey=item['Ref_Key'], description=item['Description'],
                           price=format(price, '.2f'), pricePeriod=period, physicalQuantity=str(physical),
                           shippingQuantity=str(shipping), availableQuantity=available, unitCode='pcs'))
    return dict(database=client.url.removesuffix('odata/standard.odata/'), asOf=at,
                organizationName=organization_name, warehouseId=agreement['Склад_Key'],
                warehouseName=records['finished_goods']['Description'], priceTypeId=agreement['ВидЦен_Key'],
                priceTypeName=price_type['Description'], agreementId=agreement['Ref_Key'],
                customerId=agreement['Партнер_Key'], customerName=records['customer']['Description'],
                priceIncludesVAT=agreement['ЦенаВключаетНДС'], offers=offers, mappings={'items': mappings})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', required=True)
    parser.add_argument('--as-of', required=True, help='Game time YYYY-MM-DDTHH:MM:SS')
    parser.add_argument('--organization-name', required=True)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    try:
        client = OData(args.url, os.environ.get('ERP_ODATA_USER', ''), os.environ.get('ERP_ODATA_PASSWORD', ''))
        result = snapshot(client, args.as_of, args.organization_name)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
        print('Цены и остатки ERP сохранены:', args.output)
    except (SetupError, ValueError) as exc:
        parser.exit(1, str(exc) + '\n')


if __name__ == '__main__':
    main()
