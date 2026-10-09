"""Exercise recovery and ERP side effects with a stateful OData transport.
Actual ERP integration is recorded separately in docs/odata-setup.md.
"""
import copy
import json
from pathlib import Path
import unittest
import urllib.parse
import uuid
import setup_erp_odata as s
from generate_odata_setup_profile import source


class ERP(s.OData):
    def __init__(self):
        self.records={};self.writes=[];self.next_id=None;self.fail_after_post=False;self.fail_after_conduct=False;self.bad_schema=None;self.stock=0
    def request(self,path,method='GET',body=None,params=None):
        if method=='GET' and params:
            if self.bad_schema==path:raise s.SetupError('Поле не опубликовано')
            return {'value':[]}
        if method=='POST' and path.endswith('/Post'):
            key=path.split("guid'")[1].split("'")[0];entity=path.split('(')[0]
            record=self.records[entity,key];record['Posted']=True
            self.stock+=record['Quantity'];self.writes.append((method,path))
            if self.fail_after_conduct:self.fail_after_conduct=False;raise s.SetupError('Ответ потерян после проведения')
            return {}
        if method=='POST':
            key=self.next_id or body['Ref_Key'];self.next_id=None
            record={'Posted':False,**copy.deepcopy(body),'Ref_Key':key};self.records[path,key]=record
            self.writes.append((method,path))
            if self.fail_after_post:self.fail_after_post=False;raise s.SetupError('Ответ потерян после записи')
            return copy.deepcopy(record)
        entity=path.split('(')[0];key=path.split("guid'")[1].split("'")[0]
        if method=='PATCH':
            self.records[entity,key].update(copy.deepcopy(body));self.writes.append((method,path));return {}
        return copy.deepcopy(self.records[entity,key])
    def find(self,entity,match):
        found=[v for (e,k),v in self.records.items() if e==entity and s.equivalent(v,match)]
        if len(found)>1:raise s.SetupError('Неоднозначное соответствие')
        return copy.deepcopy(found[0]) if found else None


def node(key,fields,mode='ensure',entity='Catalog_Test',match=None,**kwargs):
    return dict(key=key,id=str(uuid.uuid4()),entity=entity,fields=fields,mode=mode,match=match or {'Description':fields.get('Description','')},**kwargs)


def profile(*nodes):return {'profileId':'test','nodes':list(nodes),'items':[{'refKey':'@'+nodes[0]['key']}],'defaults':{}}


class SetupTests(unittest.TestCase):
    def test_dry_run_has_no_writes_and_uses_server_id(self):
        c=ERP();n=node('item',{'Description':'Bike'});key=str(uuid.uuid4());c.records[n['entity'],key]={'Ref_Key':key,**n['fields']}
        refs,_=s.plan(c,profile(n));self.assertEqual(refs['item'],key);self.assertEqual(c.writes,[])
    def test_actual_server_id_is_used_in_dependants(self):
        c=ERP();c.next_id=str(uuid.uuid4());expected=c.next_id
        d=profile(node('department',{'Description':'Workshop'}),node('warehouse',{'Description':'Stock','Owner_Key':'@department'}))
        result=s.apply(c,d);warehouse=next(v for v in c.records.values() if v['Description']=='Stock')
        self.assertEqual(warehouse['Owner_Key'],expected);self.assertEqual(result['items'][0]['refKey'],expected)
    def test_schema_and_conflicts_fail_before_first_write(self):
        for kind in ['schema','conflict','duplicate','deleted']:
            c=ERP();a=node('a',{'Description':'New'});b=node('b',{'Description':'Existing','Value':2},entity='Catalog_Other')
            if kind=='schema':c.bad_schema=b['entity']
            else:
                c.records[b['entity'],'1']={'Ref_Key':'1',**b['fields'],'Value':3 if kind=='conflict' else 2,'DeletionMark':kind=='deleted'}
                if kind=='duplicate':c.records[b['entity'],'2']={**c.records[b['entity'],'1'],'Ref_Key':'2'}
            with self.subTest(kind=kind),self.assertRaises(s.SetupError):s.apply(c,profile(a,b))
            self.assertEqual(c.writes,[])
    def test_lost_post_response_resumes_without_duplicate(self):
        c=ERP();c.fail_after_post=True;d=profile(node('bike',{'Description':'Bike'}))
        with self.assertRaises(s.SetupError):s.apply(c,d)
        result=s.apply(c,d);self.assertEqual(len(c.records),1);self.assertEqual(len(c.writes),1);self.assertEqual(result['entries'][0]['action'],'reuse')
    def test_unowned_finish_blocks_before_first_write(self):
        c=ERP();base=node('base',{'Description':'Spec'});end=node('end',{'Lines':[1]},'finish',target='base')
        c.records[base['entity'],'x']={'Ref_Key':'x',**base['fields'],'Описание':'Other setup','Lines':[]}
        with self.assertRaises(s.SetupError):s.apply(c,profile(node('new',{'Description':'New'}),base,end))
        self.assertEqual(c.writes,[])
    def test_conducted_stock_is_not_added_again_after_lost_response(self):
        c=ERP();c.fail_after_conduct=True
        n=node('stock',{'Комментарий':'Owned','Quantity':15},'document','Document_Stock',{'Комментарий':'Owned'});d=profile(n)
        with self.assertRaises(s.SetupError):s.apply(c,d)
        s.apply(c,d);s.apply(c,d);self.assertEqual(c.stock,15);self.assertEqual(len(c.writes),2)
        n['fields']['Quantity']=16
        with self.assertRaises(s.SetupError):s.apply(c,d)
        self.assertEqual(c.stock,15);self.assertEqual(len(c.writes),2)
    def test_bad_date_blocks_reads_and_writes(self):
        c=ERP();d=profile(node('item',{'Description':'Bike'}));d['defaults']={'setupDate':'2027-02-30T00:00:00'}
        with self.assertRaises(s.SetupError):s.apply(c,d)
        self.assertEqual(c.writes,[])
    def test_http_query_uses_percent20_and_does_not_follow_auth_redirect(self):
        class Capture:
            def open(self,req,**kwargs):self.url=req.full_url;raise TimeoutError()
        c=s.OData('https://example.com/base/ru_RU/','user','secret');c.opener=Capture()
        with self.assertRaises(s.SetupError):c.find('Catalog_Test',{'Description':"B O'K"})
        self.assertNotIn('+',c.opener.url);self.assertIn('%20',c.opener.url)
        query=urllib.parse.parse_qs(urllib.parse.urlsplit(c.opener.url).query)
        self.assertEqual(query['$filter'],["Description eq 'B O''K'"])
        with self.assertRaises(s.SetupError):s.NoRedirect().redirect_request(None,None,302,'',{ },'https://other.com')
    def test_profile_generated_source_and_game_requirements(self):
        p=json.loads((s.ROOT/'contracts/erp-odata-setup.json').read_text());self.assertEqual(len(p['items']),15)
        self.assertEqual((s.ROOT/'dimkashelk/Игра/Обмен/ПрофильНастройкиOData.xbsl').read_text(),source())
        refs={**p['defaults']};pairs=[]
        for n in p['nodes']:
            s.resolve(n['fields'],refs);s.resolve(n['match'],refs);refs[n['key']]=n['id']
        for i in s.resolve(p['items'],refs):pairs.append((i['refKey'],i['characteristicId']))
        self.assertEqual(len(set(pairs)),15)
        for model in ('CityRide','SportDrive','CargoMax','TourPro'):
            f=next(n['fields'] for n in p['nodes'] if n['key']=='finish_'+model)
            self.assertEqual(len(f['МатериалыИУслуги']),5);self.assertEqual(f['Статус'],'Действует')
        stock=next(n['fields'] for n in p['nodes'] if n['key']=='initial_stock')
        self.assertEqual([r['Количество'] for r in stock['Товары']],[15,8,4,6])
        self.assertEqual(sum(r['Сумма'] for r in stock['Товары']),1301800)

if __name__=='__main__':unittest.main()
