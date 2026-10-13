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
        refs = PROFILE['defaults'].copy()
        for node in PROFILE['nodes']:
            if node['mode'] == 'existing':
                row = self.add(node['entity'], resolve(node['fields'], refs))
                if node['key'] == 'vat':
                    row['Ставка'] = 22
                refs[node['key']] = row['Ref_Key']
        self.organization = refs['organization']
        self.bank = refs['bank_account']

    def add(self, entity, fields):
        row = {**copy.deepcopy(fields), 'Ref_Key': str(uuid.uuid4()), 'DeletionMark': False}
        self.rows.setdefault(entity, []).append(row)
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
            self.assertTrue(all(r['Номенклатура_Key'] in valid_ids for r in spec['МатериалыИУслуги']))

    def test_retries_do_not_repeat_stock_or_money_postings(self):
        erp = ERP()
        erp.fail_after_post = 'Document_ВводОстатковДенежныхСредств'
        with self.assertRaises(SetupError):
            apply(erp, PROFILE)
        committed_posts = len(erp.posts)
        apply(erp, PROFILE)
        self.assertEqual(len(erp.posts), committed_posts)
        self.assertEqual(len(erp.rows['Document_ВводОстатковДенежныхСредств']), 1)

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
