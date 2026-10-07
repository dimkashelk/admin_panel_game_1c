#!/usr/bin/env python3
"""Generate the public 1.0 contract from the normative ERP specification tables.

The extracted tables are checked in, so generation does not require the Word file.
Use --spec PATH to re-extract tables and complete protocol examples from the source.
"""
import argparse, json, re, zipfile
from pathlib import Path
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
TABLES = ROOT / 'contracts/erp-spec-tables.json'
PRIMITIVES = {
 'UUID': {'type':'string','format':'uuid','pattern':r'^(?!00000000-0000-0000-0000-000000000000$)[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'},
 'Amount': {'type':'string','pattern':r'^(0|[1-9][0-9]{0,12})\.[0-9]{2}$'},
 'PositiveAmount': {'type':'string','pattern':r'^(?!0\.00$)(0|[1-9][0-9]{0,12})\.[0-9]{2}$'},
 'SignedAmount': {'type':'string','pattern':r'^(?!-0\.00$)-?(0|[1-9][0-9]{0,12})\.[0-9]{2}$'},
 'Quantity': {'type':'string','pattern':r'^(0|[1-9][0-9]{0,12})(\.[0-9]{1,6})?$'},
 'PositiveQuantity': {'type':'string','pattern':r'^(?!0(?:\.0+)?$)(0|[1-9][0-9]{0,12})(\.[0-9]{1,6})?$'},
 'Code': {'type':'string','pattern':r'^[A-Za-z][A-Za-z0-9_]{0,63}$'},
 'GameMonth': {'type':'string','pattern':r'^[0-9]{4}-(0[1-9]|1[0-2])$'},
 'Hash': {'type':'string','pattern':r'^[0-9a-f]{64}$'},
}
CAPS = ['consistentSnapshot','phaseGuard','idempotentCommands','salesFacts','finalFinancials','automaticExecution','executionReports']
PAYLOADS = dict(zip(['prepare_game','open_phase','freeze_phase','create_customer_order','pay_realization','request_final_snapshot','pause_game','resume_game'], ['PrepareGamePayload','PhasePayload','FreezePayload','CreateOrderPayload','PayRealizationPayload','FinalSnapshotPayload','PausePayload','ResumePayload']))
ROUTES = [('context','get','Context'), ('commands','get','CommandsResponse'), ('snapshots','post','SnapshotRequest'), ('readiness','post','ReadinessRequest'), ('commands/{commandId}/receipt','post','CommandReceiptRequest'), ('sales-events','post','SalesEventRequest'), ('execution-events','post','ExecutionEventRequest')]

def extract(path):
 ns={'w':'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
 body=ET.fromstring(zipfile.ZipFile(path).read('word/document.xml')).find('w:body',ns)
 heading=''; tables={}; examples={}; in_examples=False
 for element in body:
  if element.tag.endswith('}p'):
   text=''.join(t.text or '' for t in element.findall('.//w:t',ns))
   if text.startswith('В 12 '):in_examples=True
   if text.startswith('В 13 '):in_examples=False
   if in_examples and text.startswith('{'): examples[heading]=json.loads(text)
   if text:heading=text
  elif element.tag.endswith('}tbl'):
   rows=[[''.join(t.text or '' for t in c.findall('.//w:t',ns)) for c in row.findall('w:tc',ns)] for row in element.findall('w:tr',ns)]
   if rows[0][0]=='Поле':tables[heading.split()[0]]=rows[1:]
 return {'source':'ТЗ_Расширение_ERP.docx, приложение В','schemas':tables,'examples':examples}

def ref(name): return {'$ref':'#/components/schemas/'+name}
def field_schema(kind, description):
 nullable=' или null' in kind; kind=kind.replace(' или null','')
 if kind.endswith('[]'):
  element=kind[:-2]
  item=field_schema(element, description) if element in ('string','integer') else ref(element)
  schema={'type':'array','items':item}
  limits=re.search(r'(\d+)–(\d+) элементов',description)
  if limits:schema.update(minItems=int(limits[1]),maxItems=int(limits[2]))
  if 'Без повторов' in description:schema['uniqueItems']=True
  if 'GUID' in description and element=='string':schema['items']=ref('UUID')
  if 'Элементы:' in description:schema['items']={'type':'string','enum':CAPS}
 elif kind in PRIMITIVES: schema=ref(kind)
 elif kind=='object':schema={}
 elif kind=='integer':
  limits=re.search(r'От (\d+) до (\d+)',description)
  schema={'type':'integer','minimum':int(limits[1]) if limits else 0,'maximum':int(limits[2]) if limits else 2147483647}
 elif kind in ('string','boolean'):
  schema={'type':kind}
  if 'Формат uuid' in description:schema=ref('UUID')
  elif 'Формат date-time' in description:schema['format']='date-time'
  elif 'Формат date' in description:schema['format']='date'
  pattern=re.search(r'Шаблон: (.+)$',description)
  if pattern:schema['pattern']=pattern[1]
  values=re.search(r'Значения: ([A-Za-z0-9_, ]+)\.',description)
  if values:schema['enum']=values[1].split(', ')
  const=re.search(r'Только (1\.0|RUB|true)\.',description)
  if const:schema['const']=True if const[1]=='true' else const[1]
  length=re.search(r'Длина (\d+)–(\d+) символов',description)
  if length:schema.update(minLength=int(length[1]),maxLength=int(length[2]))
 else:schema=ref(kind)
 if nullable:schema={'anyOf':[schema,{'type':'null'}]}
 schema['description']=description
 return schema

def conditional(schema, field, value, required):
 schema.setdefault('allOf',[]).append({'if':{'properties':{field:{'const':value}},'required':[field]},'then':{'required':required}})

def build(data):
 schemas={k:dict(v) for k,v in PRIMITIVES.items()}
 for name,rows in data['schemas'].items():
  schemas[name]={'type':'object','additionalProperties':False,'properties':{f:field_schema(t,d) for f,t,_,d in rows},'required':[f for f,_,r,_ in rows if r=='Да']}
 schemas['PhasePayload']['properties']['previousPhaseToken']['anyOf']=[ref('UUID'),{'type':'null'}]
 # Conditional structural requirements are also normative; business checks run server-side.
 conditional(schemas['SnapshotRequest'],'kind','frozen',['freezeCommandId'])
 conditional(schemas['SnapshotRequest'],'kind','final',['freezeCommandId','financials'])
 conditional(schemas['ReadinessRequest'],'ready',False,['reason'])
 for name in ['CommandReceiptRequest','ExecutionEventRequest','ExecutionStep']:
  conditional(schemas[name],'status','failed',['errorCode','errorMessage'])
 for kind in ['purchasing','production']:
  conditional(schemas['ExecutionEventRequest'],'executionKind',kind,['planId','planVersion'])
 conditional(schemas['ExecutionEventRequest'],'executionKind','sales',['erpOrderId'])
 schemas['Command']['allOf']=[{'if':{'properties':{'kind':{'const':kind}},'required':['kind']},'then':{'properties':{'payload':ref(payload)}}} for kind,payload in PAYLOADS.items()]
 for field in ('allowSupplierDebt','allowCustomerDebt'):schemas['GameSettings']['properties'][field]['const']=False
 paths={}
 for tail,method,schema in ROUTES:
  parameters=[{'name':'bindingId','in':'path','required':True,'schema':ref('UUID')}, {'name':'X-Game-Contract-Version','in':'header','required':True,'schema':{'type':'string','const':'1.0'}}]
  if '{commandId}' in tail:parameters.append({'name':'commandId','in':'path','required':True,'schema':ref('UUID')})
  if method=='post':parameters.append({'name':'Idempotency-Key','in':'header','required':True,'schema':ref('UUID')})
  if tail=='commands':parameters.extend([{'name':'gameId','in':'query','required':True,'schema':ref('UUID')},{'name':'afterSequence','in':'query','schema':{'type':'integer','minimum':0,'maximum':2147483647,'default':0}},{'name':'limit','in':'query','schema':{'type':'integer','minimum':1,'maximum':100,'default':20}}])
  responses={'200':{'description':'Успешное чтение или сохранение. Повтор POST возвращает прежний resourceId и replayed=true.','headers':{'X-Game-Contract-Version':{'schema':{'type':'string','const':'1.0'}}},'content':{'application/json':{'schema':ref('Accepted' if method=='post' else schema)}}}}
  for code in [400,401,403,404,405,409,413,422,429,500,503]:responses[str(code)]={'description':'Ошибка протокола; code определяет причину.','content':{'application/json':{'schema':ref('Error')}}}
  responses['405']['headers']={'Allow':{'schema':{'type':'string'}}};responses['429']['headers']={'Retry-After':{'schema':{'type':'integer','minimum':1}}}
  operation={'operationId':('get' if method=='get' else 'submit')+schema,'parameters':parameters,'responses':responses}
  if method=='post':operation['requestBody']={'required':True,'description':'UTF-8 JSON, максимум 1 МиБ, без сжатия. Ключ совпадает с messageId. Неизменные байты при повторе.','content':{'application/json':{'schema':ref(schema)}}}
  paths['/bindings/{bindingId}/'+tail]={method:operation}
 return {'openapi':'3.1.0','info':{'title':'ERP Game API','version':'1.0','description':'Нормативный контракт из приложения В ТЗ расширения ERP. Очередь инициируется ERP. Старый 0.1.0-design несовместим.'},'servers':[{'url':'/api/game/v1'}],'security':[{'serviceBearer':[]}],'paths':paths,'components':{'securitySchemes':{'serviceBearer':{'type':'http','scheme':'bearer'}},'schemas':schemas}}

def generate_xbsl(schemas):
 # JSON field names and schema identifiers are the explicit external-name exception.
 lines=['// Generated from ERP specification tables; regenerate with scripts/generate_game_contract.py.',
        '// External English identifiers are fixed by contract 1.0.',
        '@ВПроекте', 'метод Схема(Имя: Строка): Соответствие<Строка, Объект?>']
 for name, schema in schemas.items():
  text=json.dumps(schema,ensure_ascii=False,separators=(',',':'))
  # XBSL string escaping. Backslash sequences must reach the JSON reader literally.
  literal=json.dumps(text,ensure_ascii=False).replace("$", r"\$").replace("%", r"\%")
  lines.extend([f'    если Имя == "{name}"', f'        возврат ПротоколERP.Прочитать({literal})', '    ;'])
 lines.extend(['    выбросить новый ИсключениеНедопустимыйАргумент("Неизвестная схема ERP")',';',''])
 (ROOT/'dimkashelk/Игра/Обмен/КонтрактERP.xbsl').write_text('\n'.join(lines))

def main():
 parser=argparse.ArgumentParser();parser.add_argument('--spec',type=Path);args=parser.parse_args()
 if args.spec:TABLES.write_text(json.dumps(extract(args.spec),ensure_ascii=False,indent=2)+'\n')
 contract=build(json.loads(TABLES.read_text()))
 (ROOT/'contracts/game-api.openapi.json').write_text(json.dumps(contract,ensure_ascii=False,indent=2)+'\n')
 generate_xbsl(contract['components']['schemas'])
 print(f"Generated {len(contract['paths'])} routes, {len(contract['components']['schemas'])} schemas (1.0)")
if __name__=='__main__':main()
