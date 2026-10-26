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

from setup_erp_odata import OData, ROOT, ZERO, SetupError, resolve, read_all

PRODUCTS = ('CityRide', 'SportDrive', 'CargoMax', 'TourPro')


def latest_price(record_sets, item_id, price_type, currency, at=None):
    candidates = [r for rs in record_sets for r in rs['RecordSet']
                  if r['Active'] and (at is None or r['Period'] <= at)
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
    needed = {'organization', 'finished_goods', 'purchases', 'workshop',
              'sales_price', 'cost_products', 'cost_components', 'rub', 'pcs'}
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
    price_type = records['sales_price']
    if price_type.get('DeletionMark') or price_type['Статус'] != 'Действует':
        raise SetupError('Вид продажной цены ERP не действует')
    if price_type['ВалютаЦены_Key'] != refs['rub']:
        raise SetupError('Продажный вид цены должен быть в рублях')
    mappings = resolve(profile['items'], refs)
    prices = read_all(client, 'InformationRegister_ЦеныНоменклатуры25')
    stock = read_all(client, "AccumulationRegister_ТоварыНаСкладах/Balance(Period=datetime'" + at + "')")
    commitments = read_all(client, "AccumulationRegister_ТоварыКОтгрузке/Balance(Period=datetime'" + at + "')")
    offers = []
    for code in PRODUCTS:
        item = records[code]
        if item['ЕдиницаИзмерения_Key'] != refs['pcs']:
            raise SetupError('Единица велосипедов отличается от шт.: ' + code)
        price, period = latest_price(prices, item['Ref_Key'], refs['sales_price'], refs['rub'])
        physical, shipping, available = available_stock(stock, item['Ref_Key'], refs['finished_goods'], commitments)
        offers.append(dict(productCode=code, refKey=item['Ref_Key'], description=item['Description'],
                           price=format(price, '.2f'), pricePeriod=period, physicalQuantity=str(physical),
                           shippingQuantity=str(shipping), availableQuantity=available, unitCode='pcs'))
    balances = []
    vat_rates = []
    for mapping in mappings:
        code, item_id = mapping['itemCode'], mapping['refKey']
        item = records[code]
        if item['ЕдиницаИзмерения_Key'] != refs['pcs']:
            raise SetupError('Единица ERP отличается от шт.: ' + code)
        cost_key = 'cost_products' if code in PRODUCTS else 'cost_components'
        cost, _ = latest_price(prices, item_id, refs[cost_key], refs['rub'], at)
        if code in PRODUCTS:
            rate = client.read('Catalog_СтавкиНДС', item['СтавкаНДС_Key'])
            if rate.get('DeletionMark'):
                raise SetupError('Ставка НДС помечена на удаление: ' + code)
            vat_rates.append(dict(productCode=code, vatRate=format(Decimal(str(rate['Ставка'])), '.2f')))
        for warehouse in (('finished_goods',) if code in PRODUCTS else ('purchases', 'workshop')):
            physical, shipping, available = available_stock(stock, item_id, refs[warehouse], commitments)
            if shipping:
                raise SetupError('Начальные остатки содержат резервы: ' + code)
            balances.append(dict(itemCode=code, warehouseId=warehouse,
                                 unitCode='set' if code.startswith(('POWER_', 'WHEELS_', 'EQUIP_')) else 'pcs',
                                 quantity=str(available), unitCost=format(cost, '.2f')))
    cash = Decimal(0)
    for register, catalog, dimension in [
            ('ДенежныеСредстваБезналичные', 'БанковскиеСчетаОрганизаций', 'БанковскийСчет_Key'),
            ('ДенежныеСредстваНаличные', 'Кассы', 'Касса_Key')]:
        for row in read_all(client, "AccumulationRegister_" + register + "/Balance(Period=datetime'" + at + "')"):
            if row['Организация_Key'] != refs['organization']:
                continue
            account = client.read('Catalog_' + catalog, row[dimension])
            if account.get('DeletionMark') or account['ВалютаДенежныхСредств_Key'] != refs['rub']:
                raise SetupError('Начальные деньги должны быть на активном рублёвом счёте/кассе')
            cash += Decimal(str(row['СуммаBalance']))
    if cash < 0:
        raise SetupError('Начальные деньги не могут быть отрицательными')
    prepare = dict(initialCash=format(cash, '.2f'), currency='RUB', balances=balances,
                   initialPrices=[dict(productCode=r['productCode'], price=r['price']) for r in offers], seed='fixed')
    return dict(database=client.url.removesuffix('odata/standard.odata/'), asOf=at,
                organizationName=organization_name, warehouseId=refs['finished_goods'],
                warehouseName=records['finished_goods']['Description'], priceTypeId=refs['sales_price'],
                priceTypeName=price_type['Description'],
                priceIncludesVAT=price_type['ЦенаВключаетНДС'], offers=offers, mappings={'items': mappings},
                prepare=prepare, vatRates=vat_rates)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', required=True)
    parser.add_argument('--as-of', required=True, help='Game time for balances and costs YYYY-MM-DDTHH:MM:SS; sale prices use the latest active record')
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
