"""Contract tests for independent ERP IDs, retries and complete initial data."""
import copy
import json
from pathlib import Path
import unittest
import uuid

from setup_erp_odata import SetupError, ZERO, apply, plan, resolve
from read_erp_market import snapshot

PROFILE = json.loads((Path(__file__).resolve().parents[1] / 'contracts/erp-odata-setup.json').read_text())


class ERP:
    """A server that assigns different GUIDs from the proposed POST GUIDs."""
    url = 'https://example.invalid/odata/standard.odata/'

    def __init__(self):
        self.rows = {}
        self.posts = []
        self.fail_after_post = None
        self.foreign_cash = 0
        self.foreign_debt = 0
        self.hide_debt = False
        refs = PROFILE['defaults'].copy()
        for node in PROFILE['nodes']:
            if node['mode'] == 'existing':
                row = self.add(node['entity'], resolve(node['fields'], refs))
                if node['key'] == 'vat':
                    row['Ставка'] = 22
                if node['key'] == 'bank_account':
                    row['Банк_Key'] = str(uuid.uuid4())
                refs[node['key']] = row['Ref_Key']
        self.organization = refs['organization']
        self.bank = refs['bank_account']

    def add(self, entity, fields):
        row = {**copy.deepcopy(fields), 'Ref_Key': str(uuid.uuid4()), 'DeletionMark': False}
        if entity.startswith('Document_'):
            row.setdefault('Posted', False)
            row.setdefault('Number', 'TEST-' + row['Ref_Key'][:8])
        self.rows.setdefault(entity, []).append(row)
        if entity == 'Document_ПервичныйДокумент':
            obj = self.add('Catalog_ОбъектыРасчетов', {'Организация_Key': fields['Организация_Key']})
            row['ОбъектРасчетов_Key'] = obj['Ref_Key']
        return row

    def find(self, entity, match):
        rows = [r for r in self.rows.get(entity, []) if all(r.get(k) == v for k, v in match.items())]
        if len(rows) > 1:
            raise SetupError('Неоднозначное соответствие')
        return copy.deepcopy(rows[0]) if rows else None

    def read(self, entity, key):
        return copy.deepcopy(next(r for r in self.rows[entity] if r['Ref_Key'] == key))

    def request(self, path, method='GET', body=None, params=None):
        entity = path.split('(')[0].split('/')[0]
        if '/Balance(' in path:
            if entity == 'AccumulationRegister_РасчетыСПоставщиками':
                values = []
                if not self.hide_debt:
                    for doc in self.rows.get('Document_ВводОстатков', []):
                        if doc.get('Posted'):
                            for r in doc['РасчетыСПартнерами']:
                                source = self.read('Document_ПервичныйДокумент', r['ОбъектРасчетов'])
                                values.append(dict(ОбъектРасчетов_Key=source['ОбъектРасчетов_Key'],
                                                   Валюта_Key=doc['Валюта_Key'], СуммаBalance=-r['Сумма']))
                if self.foreign_debt:
                    obj = self.rows['Catalog_ОбъектыРасчетов'][0]
                    values.append(dict(ОбъектРасчетов_Key=obj['Ref_Key'], СуммаBalance=-self.foreign_debt))
                return {'value': values}
            if entity == 'AccumulationRegister_ТоварыНаСкладах':
                values = []
                for doc in self.rows.get('Document_ВводОстатковТоваров', []):
                    if doc.get('Posted'):
                        values.extend(dict(Номенклатура_Key=r['Номенклатура_Key'], Склад_Key=doc['Склад_Key'],
                                           Характеристика_Key=ZERO, Назначение_Key=ZERO,
                                           ВНаличииBalance=r['Количество'], КОтгрузкеBalance=0) for r in doc['Товары'])
                return {'value': values}
            if entity == 'AccumulationRegister_ДенежныеСредстваБезналичные':
                amount = self.foreign_cash + sum(r['Сумма'] for d in self.rows.get('Document_ВводОстатковДенежныхСредств', [])
                                                if d.get('Posted') for r in d['БанковскиеСчета'])
                return {'value': [dict(Организация_Key=self.organization, БанковскийСчет_Key=self.bank,
                                       СуммаBalance=amount)] if amount else []}
            return {'value': []}
        if entity == 'InformationRegister_ЦеныНоменклатуры25':
            return {'value': [dict(RecordSet=[dict(**r, Active=True, Period=d['Date'],
                                                  УпаковкаЦО_Key=ZERO, СерияЦО_Key=ZERO,
                                                  МаркетинговоеМероприятие_Key=ZERO, Упаковка_Key=ZERO)
                                            for r in d['Товары2_5']])
                              for d in self.rows.get('Document_УстановкаЦенНоменклатуры', []) if d.get('Posted')]}
        if "(guid'" in path:
            key = path.split("(guid'", 1)[1].split("'", 1)[0]
            row = next(r for r in self.rows[entity] if r['Ref_Key'] == key)
            if path.endswith('/Post()'):
                row['Posted'] = True
                self.posts.append((entity, key, 'Post'))
                if self.fail_after_post == entity:
                    self.fail_after_post = None
                    raise SetupError('Ответ потерян после проведения')
                return {}
            if method == 'PATCH':
                row.update(copy.deepcopy(body))
                return {}
            return copy.deepcopy(row)
        if method == 'POST':
            fields = {k: v for k, v in body.items() if k != 'Ref_Key'}
            row = self.add(entity, fields)
            self.posts.append((entity, row['Ref_Key'], 'Create'))
            return copy.deepcopy(row)
        # The protocol requests $select during preflight, then pages for snapshots.
        rows = self.rows.get(entity, [])
        params = params or {}
        start, limit = params.get('$skip', 0), params.get('$top', len(rows))
        return {'value': copy.deepcopy(rows[start:start + limit])}


class SetupTests(unittest.TestCase):
    def test_document_customer_and_typical_sales_agreement(self):
        erp = ERP()
        apply(erp, PROFILE)
        customer = next(r for r in erp.rows['Catalog_Контрагенты'] if r['Description'] == 'Рынок')
        self.assertEqual((customer['ИНН'], customer['КПП']), ('7452567895', '715258615'))
        self.assertEqual(customer['КонтактнаяИнформация'][0]['НомерТелефона'], '89053456585')
        account = next(r for r in erp.rows['Catalog_БанковскиеСчетаКонтрагентов'] if r['Owner'] == customer['Ref_Key'])
        self.assertEqual(account['НомерСчета'], '53687456987412552365')
        agreement = erp.rows['Catalog_СоглашенияСКлиентами'][0]
        self.assertTrue(agreement['Типовое'])
        self.assertNotIn('Партнер_Key', agreement)

    def test_existing_item_keeps_arbitrary_article_and_full_name_when_filled(self):
        erp = ERP()
        item = erp.add('Catalog_Номенклатура', {'Description': 'Городской велосипед',
            'Артикул': 'CITY-001', 'НаименованиеПолное': 'Велосипед городской учебный'})
        apply(erp, PROFILE)
        actual = erp.read('Catalog_Номенклатура', item['Ref_Key'])
        self.assertEqual(actual['Артикул'], 'CITY-001')
        self.assertEqual(actual['НаименованиеПолное'], 'Велосипед городской учебный')
        self.assertNotEqual(actual['ВидНоменклатуры_Key'], ZERO)
        apply(erp, PROFILE)

    def test_required_item_conflict_reports_actual_expected_before_writes(self):
        erp = ERP()
        erp.add('Catalog_Номенклатура', {'Description': 'Городской велосипед', 'ТипНоменклатуры': 'Услуга'})
        with self.assertRaisesRegex(SetupError, 'ТипНоменклатуры: в ERP "Услуга"; ожидается "Товар"'):
            apply(erp, PROFILE)
        self.assertEqual(erp.posts, [])

    def test_dry_run_never_writes_or_reuses_proposed_ids_across_bases(self):
        first, second = ERP(), ERP()
        one, _ = plan(first, PROFILE)
        two, _ = plan(second, PROFILE)
        self.assertEqual(first.posts, [])
        self.assertEqual(second.posts, [])
        self.assertNotEqual(one['CityRide'], two['CityRide'])

    def test_actual_guids_flow_into_bom_documents_and_all_fifteen_mappings(self):
        erp = ERP()
        result = apply(erp, PROFILE)
        self.assertEqual(len(result['items']), 15)
        for mapping in result['items']:
            item = erp.read('Catalog_Номенклатура', mapping['refKey'])
            self.assertEqual(item['Артикул'], 'GAME-' + mapping['itemCode'])
            self.assertEqual(mapping['characteristicId'], '')
        valid_ids = {r['Ref_Key'] for r in erp.rows['Catalog_Номенклатура']}
        for doc in erp.rows['Document_ВводОстатковТоваров']:
            self.assertTrue(doc['Posted'])
            self.assertTrue(all(r['Номенклатура_Key'] in valid_ids for r in doc['Товары']))
        for spec in erp.rows['Catalog_РесурсныеСпецификации']:
            if not spec.get('IsFolder'):
                self.assertTrue(all(r['Номенклатура_Key'] in valid_ids for r in spec['МатериалыИУслуги']))

    def test_retries_do_not_repeat_stock_or_money_postings(self):
        erp = ERP()
        erp.fail_after_post = 'Document_ВводОстатковДенежныхСредств'
        with self.assertRaises(SetupError):
            apply(erp, PROFILE)
        committed_posts = [r for r in erp.posts if r[0] == 'Document_ВводОстатковДенежныхСредств']
        apply(erp, PROFILE)
        self.assertEqual([r for r in erp.posts if r[0] == 'Document_ВводОстатковДенежныхСредств'], committed_posts)
        all_posts = len(erp.posts)
        apply(erp, PROFILE)
        self.assertEqual(len(erp.posts), all_posts)
        self.assertEqual(len(erp.rows['Document_ВводОстатковДенежныхСредств']), 1)

    def test_supplier_debt_is_verified_and_lost_post_response_is_safe(self):
        erp = ERP()
        erp.fail_after_post = 'Document_ВводОстатков'
        with self.assertRaises(SetupError):
            apply(erp, PROFILE)
        count = len(erp.posts)
        result = apply(erp, PROFILE)
        self.assertEqual(len(erp.posts), count)
        self.assertEqual(result['verification']['supplierDebt'], '2801800.00')
        self.assertEqual(len(erp.rows['Document_ВводОстатков']), 1)

    def test_missing_debt_movements_cannot_report_success(self):
        erp = ERP()
        erp.hide_debt = True
        with self.assertRaisesRegex(SetupError, 'долг поставщику не прошёл сверку'):
            apply(erp, PROFILE)

    def test_foreign_settlements_block_before_first_write(self):
        erp = ERP()
        erp.add('Catalog_ОбъектыРасчетов', {'Организация_Key': erp.organization})
        erp.foreign_debt = 1
        with self.assertRaisesRegex(SetupError, 'уже есть взаиморасчёты'):
            apply(erp, PROFILE)
        self.assertEqual(erp.posts, [])

    def test_bank_and_phone_references_belong_to_target_erp(self):
        erp = ERP()
        apply(erp, PROFILE)
        account = erp.rows['Catalog_БанковскиеСчетаКонтрагентов'][0]
        self.assertEqual(account['Банк_Key'], erp.rows['Catalog_БанковскиеСчетаОрганизаций'][0]['Банк_Key'])
        supplier = next(r for r in erp.rows['Catalog_Контрагенты'] if r['Description'] == 'Единый поставщик')
        self.assertEqual(supplier['ИНН'], '7078125417')
        self.assertEqual(supplier['КонтактнаяИнформация'][0]['Вид_Key'], erp.rows['Catalog_ВидыКонтактнойИнформации'][0]['Ref_Key'])

    def test_missing_bank_blocks_before_any_write(self):
        erp = ERP()
        erp.rows['Catalog_БанковскиеСчетаОрганизаций'][0]['Банк_Key'] = ZERO
        with self.assertRaisesRegex(SetupError, 'Банк_Key'):
            apply(erp, PROFILE)
        self.assertEqual(erp.posts, [])

    def test_specifications_start_on_january_first_of_selected_year(self):
        erp = ERP()
        apply(erp, PROFILE, {'setupDate': '2028-05-15T00:00:00'})
        for spec in erp.rows['Catalog_РесурсныеСпецификации']:
            if not spec.get('IsFolder'):
                self.assertEqual(spec['НачалоДействия'], '2028-01-01T00:00:00')

    def test_changed_posted_opening_debt_is_a_conflict(self):
        erp = ERP()
        apply(erp, PROFILE)
        erp.rows['Document_ВводОстатков'][0]['РасчетыСПартнерами'][0]['Сумма'] = 1
        count = len(erp.posts)
        with self.assertRaisesRegex(SetupError, 'существующая запись отличается'):
            apply(erp, PROFILE)
        self.assertEqual(len(erp.posts), count)

    def test_new_bases_have_distinct_real_ids(self):
        first = apply(ERP(), PROFILE)['items']
        second = apply(ERP(), PROFILE)['items']
        self.assertTrue({r['refKey'] for r in first}.isdisjoint(r['refKey'] for r in second))

    def test_full_snapshot_matches_shared_profile_without_initial_writes(self):
        erp = ERP()
        result = apply(erp, PROFILE)
        post_count = len(erp.posts)
        data = snapshot(erp, '2026-10-15T23:59:59', 'ДвижжОК ООО')
        self.assertEqual(len(erp.posts), post_count)
        self.assertEqual(data['mappings']['items'], result['items'])
        self.assertEqual(data['prepare']['initialCash'], '1000000.00')
        self.assertEqual({r['productCode']: r['price'] for r in data['offers']},
                         {'CityRide': '39000.00', 'SportDrive': '45000.00', 'CargoMax': '56000.00', 'TourPro': '55000.00'})
        self.assertEqual({r['productCode']: r['availableQuantity'] for r in data['offers']},
                         {'CityRide': 15, 'SportDrive': 8, 'CargoMax': 4, 'TourPro': 6})
        by_key = lambda rows: {(r['itemCode'], r['warehouseId']): r for r in rows}
        self.assertEqual(by_key(data['prepare']['balances']), by_key(PROFILE['initial']['balances']))

    def test_foreign_initial_cash_blocks_before_any_write(self):
        erp = ERP()
        erp.foreign_cash = 1000000
        with self.assertRaisesRegex(SetupError, 'уже есть деньги'):
            apply(erp, PROFILE)
        self.assertEqual(erp.posts, [])

    def test_ambiguous_item_names_never_pick_an_arbitrary_guid(self):
        erp = ERP()
        for _ in range(2):
            erp.add('Catalog_Номенклатура', {'Description': 'Городской велосипед'})
        with self.assertRaisesRegex(SetupError, 'Неоднозначное'):
            plan(erp, PROFILE)
        self.assertEqual(erp.posts, [])


if __name__ == '__main__':
    unittest.main()
