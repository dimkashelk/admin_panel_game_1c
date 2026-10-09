#!/usr/bin/env python3
"""Repeatable OData 3 setup. Credentials come from ERP_ODATA_USER/PASSWORD.
The checked-in profile contains no database-specific GUIDs. Default is dry-run.
"""
import argparse
import base64
import copy
from datetime import datetime
import json
import os
from pathlib import Path
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
ZERO = '00000000-0000-0000-0000-000000000000'

class SetupError(Exception):
    pass

def normalize_url(url):
    p = urllib.parse.urlsplit(url.strip())
    if p.scheme != 'https' or not p.hostname or p.username or p.password or p.query or p.fragment:
        raise SetupError('Требуется HTTPS-адрес публикации без логина, параметров и фрагмента')
    path = p.path.rstrip('/')
    if path.endswith('/ru_RU'):
        path = path[:-6]
    if not path.endswith('/odata/standard.odata'):
        path += '/odata/standard.odata'
    return urllib.parse.urlunsplit((p.scheme, p.netloc, path + '/', '', ''))

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise SetupError('OData перенаправляет запрос: проверьте адрес публикации')

class OData:
    def __init__(self, url, user, password):
        self.url = normalize_url(url)
        if not user or not password:
            raise SetupError('Укажите ERP_ODATA_USER и ERP_ODATA_PASSWORD')
        self.auth = 'Basic ' + base64.b64encode((user + ':' + password).encode()).decode()
        self.opener = urllib.request.build_opener(NoRedirect())
    def request(self, path, method='GET', body=None, params=None):
        url = self.url + urllib.parse.quote(path, safe="()',$=")
        query = {'$format': 'json', **(params or {})}
        url += '?' + urllib.parse.urlencode(query, quote_via=urllib.parse.quote)
        req = urllib.request.Request(url, method=method,
            headers={'Authorization': self.auth, 'Accept': 'application/json', 'Content-Type': 'application/json'},
            data=None if body is None else json.dumps(body, ensure_ascii=False).encode())
        try:
            with self.opener.open(req, timeout=45) as res:
                data = res.read()
                return json.loads(data) if data else {}
        except urllib.error.HTTPError as exc:
            # Never echo a request body or authentication headers.
            try:
                error = json.loads(exc.read())['odata.error']['message']['value']
            except (ValueError, KeyError, TypeError):
                error = 'Не удалось прочитать ответ OData'
            raise SetupError(f'{method} {path}: HTTP {exc.code}: {error}') from None
        except (urllib.error.URLError, TimeoutError):
            raise SetupError(f'{method} {path}: ошибка соединения; повторите проверку') from None
    def find(self, entity, match):
        clauses = []
        for k,v in match.items():
            if k=='Комментарий':
                clauses.append("substringof('"+str(v).replace("'","''")+"',Комментарий) eq true")
                continue
            literal = "guid'"+v+"'" if k.endswith('_Key') or k in ('Ref_Key','Owner') else "'"+str(v).replace("'","''")+"'"
            clauses.append(("cast(Owner,'Catalog_Номенклатура')" if k=='Owner' else k)+' eq '+literal)
        rows = self.request(entity, params={'$filter': ' and '.join(clauses), '$top': 2, '$orderby': 'Ref_Key'}).get('value', [])
        if len(rows) > 1:
            raise SetupError(f'{entity}: неоднозначное соответствие {match.get("Description", "")}')
        if rows and 'Комментарий' in match and rows[0].get('Комментарий')!=match['Комментарий']:
            raise SetupError(f'{entity}: конфликт маркера документа')
        return rows[0] if rows else None
    def read(self, entity, key):
        return self.request(entity + "(guid'"+key+"')")


def resolve(value, refs):
    if isinstance(value,str) and value.startswith('@'):
        if value[1:] not in refs: raise SetupError('Неизвестная зависимость '+value)
        return refs[value[1:]]
    if isinstance(value,list): return [resolve(x,refs) for x in value]
    if isinstance(value,dict): return {k:resolve(v,refs) for k,v in value.items()}
    return value


def equivalent(actual, expected):
    if isinstance(expected,dict):
        return isinstance(actual,dict) and all(k in actual and equivalent(actual[k],v) for k,v in expected.items())
    if isinstance(expected,list):
        return isinstance(actual,list) and len(actual)==len(expected) and all(equivalent(a,b) for a,b in zip(actual,expected))
    if isinstance(expected,(int,float)) and not isinstance(expected,bool):
        return str(actual)==str(expected) or actual==expected
    return actual==expected


def plan(client, profile, options=None):
    refs, report, schema = {**profile.get('defaults',{}), **(options or {})}, [], {}
    if 'setupDate' in refs:
        try: datetime.strptime(refs['setupDate'],'%Y-%m-%dT%H:%M:%S')
        except ValueError:raise SetupError('Дата настройки: ожидается YYYY-MM-DDTHH:MM:SS') from None
    found={}
    for n in profile['nodes']:
        fields = resolve(n['fields'],refs)
        # $select is validated by the server even when the entity set is empty.
        selection = ','.join(fields)
        if (n['entity'],selection) not in schema:
            client.request(n['entity'],params={'$top':1,'$select':selection})
            schema[n['entity'],selection]=True
        if n['mode']=='finish':
            refs[n['key']]=refs[n['target']]
            old=found.get(n['target'])
            if old and not equivalent(old,fields) and old.get(n.get('ownershipField','Описание'))!='BICYCLE_SIMPLE/1.0 OData setup':
                raise SetupError(n['key']+': чужую запись нельзя автоматически менять')
            report.append(dict(key=n['key'], action='finish', entity=n['entity'],refKey=refs[n['key']],fields=fields))
            continue
        match=resolve(n['match'],refs)
        old=client.find(n['entity'],match)
        found[n['key']]=old
        refs[n['key']]=old['Ref_Key'] if old else n['id']
        if old and old.get('DeletionMark'):raise SetupError(n['key']+': запись помечена на удаление')
        if not old and n['mode']=='existing':raise SetupError(n['key']+': обязательный классификатор не найден')
        # Draft and active specs are both valid at the skeleton step; the finish step verifies the complete BOM.
        expected=copy.deepcopy(fields)
        if n['key'].startswith('spec_'):expected.pop('Статус',None)
        draft_document = n['mode']=='document' and old and not old.get('Posted')
        if old and not draft_document and not equivalent(old,expected):raise SetupError(n['key']+': существующая запись отличается от профиля; исправьте конфликт')
        report.append(dict(key=n['key'],action='reuse' if old else 'create',entity=n['entity'],refKey=refs[n['key']],fields=fields))
    return refs,report


def apply(client,profile,options=None):
    refs,entries=plan(client,profile,options) # all publication/schema/conflict checks before the first write
    completed=[]
    for n,e in zip(profile['nodes'],entries):
        try:
            fields=resolve(n['fields'],refs)
            if n['mode']=='finish':
                e['refKey']=refs[n['target']]
                actual=client.read(e['entity'],e['refKey'])
                if not equivalent(actual,fields):
                    if actual.get(n.get('ownershipField','Описание'))!='BICYCLE_SIMPLE/1.0 OData setup':
                        raise SetupError('чужую запись нельзя автоматически менять')
                    client.request(e['entity']+"(guid'"+e['refKey']+"')",'PATCH',fields)
                    if not equivalent(client.read(e['entity'],e['refKey']),fields):raise SetupError('спецификация не прошла сверку')
            else:
                old=client.find(e['entity'],resolve(n['match'],refs))
                if not old:
                    old=client.request(e['entity'],'POST',{'Ref_Key':n['id'],**fields})
                e['refKey']=old['Ref_Key']
                if n['mode']=='document':
                    path=e['entity']+"(guid'"+e['refKey']+"')"
                    actual=client.read(e['entity'],e['refKey'])
                    if not actual.get('Posted'):
                        if not equivalent(actual,fields):client.request(path,'PATCH',fields)
                        if not equivalent(client.read(e['entity'],e['refKey']),fields):raise SetupError('документ не прошёл сверку до проведения')
                        client.request(path+'/Post','POST')
                    if not client.read(e['entity'],e['refKey']).get('Posted'):raise SetupError('документ не проведён')
                expected=copy.deepcopy(fields)
                if n['key'].startswith('spec_'):expected.pop('Статус',None)
                if not equivalent(client.read(e['entity'],e['refKey']),expected):
                    raise SetupError('записанные поля отличаются от задания')
            refs[n['key']]=e['refKey']
            completed.append({k:v for k,v in e.items() if k!='fields'})
        except SetupError as exc:
            raise SetupError(f'Остановлено на {e["key"]}; завершено {len(completed)} шагов. {exc}. Повтор продолжит проверку сохранённых записей.') from None
    return dict(profile=profile['profileId'],entries=completed,items=resolve(profile['items'],refs))


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--url',required=True);p.add_argument('--apply',action='store_true')
    p.add_argument('--output',type=Path,required=True)
    p.add_argument('--organization-name');p.add_argument('--setup-date')
    args=p.parse_args()
    try:
        client=OData(args.url,os.environ.get('ERP_ODATA_USER',''),os.environ.get('ERP_ODATA_PASSWORD',''))
        profile=json.loads((ROOT/'contracts/erp-odata-setup.json').read_text())
        options={k:v for k,v in [('organizationName',args.organization_name),('setupDate',args.setup_date)] if v is not None}
        if args.apply:result=apply(client,profile,options)
        else:
            refs,entries=plan(client,profile,options)
            result=dict(profile=profile['profileId'],dryRun=True,entries=entries,items=resolve(profile['items'],refs))
        args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
        print('Сохранён отчёт:',args.output, 'Шагов:',len(result['entries']))
    except SetupError as exc:
        p.exit(1,str(exc)+'\n')
if __name__=='__main__': main()
