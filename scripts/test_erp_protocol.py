#!/usr/bin/env python3
"""Compile and run original protocol/schema/queue XBSL in Element Script 10.0.

This exercises JSON, validation, canonical hashing and queue pagination. It does
not emulate HTTP authentication, Element transactions or ERP documents.
Set ELEMENT_SCRIPT_HOME and ELEMENT_SCRIPT_JAVA to an installed native runtime.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'dimkashelk/Игра/Обмен'


def literal(value):
    return json.dumps(value, ensure_ascii=False).replace('$', r'\$').replace('%', r'\%').replace(r'\n', r'\н').replace(r'\r', r'\в').replace(r'\t', r'\т')


def examples():
    data = json.loads((ROOT / 'contracts/erp-spec-tables.json').read_text())['examples']
    names = ['Context','CommandsResponse','SnapshotRequest','SnapshotRequest','ReadinessRequest','CommandReceiptRequest','CommandReceiptRequest','SalesEventRequest','SalesEventRequest','ExecutionEventRequest','Accepted','Error','PrepareGamePayload','PhasePayload','FreezePayload','PayRealizationPayload','FinalSnapshotPayload','PausePayload','ResumePayload']
    return data, [{'name':name,'schema':schema,'value':value,'valid':True} for (name,value),schema in zip(data.items(), names)]


class NativeProtocolTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get('ELEMENT_SCRIPT_HOME'), 'Native Element Script runtime not configured')
    def test_original_protocol(self):
        data,cases = examples()
        def bad(name, schema, value):
            cases.append({'name':name,'schema':schema,'value':value,'valid':False})
        context = data['Ответ на GET context']
        for field in context:
            value = copy.deepcopy(context); del value[field]
            bad('missing context '+field, 'Context', value)
        for field in ['gameId','roundNumber','settings']:
            value = copy.deepcopy(context); value[field] = None
            bad('null context '+field,'Context',value)
        for schema, values in [('UUID',['00000000-0000-0000-0000-000000000000','00000000-0000-4000-8000-0000000000AA']),('Amount',['1','01.00','-1.00','0.001','10000000000000.00']),('PositiveAmount',['0.00']),('Quantity',['1.0000001','-1']),('GameMonth',['2027-13']),('Hash',['a'*63]),('Code',['1item'])]:
            for value in values: bad(schema+' '+value,schema,value)
        for value in ['0.00','1.50','9999999999999.99']:
            cases.append({'name':'amount boundary','schema':'Amount','value':value,'valid':True})
        for name in ['Окончательный снимок предложения','Финальный снимок месяца']:
            value=copy.deepcopy(data[name]); del value['freezeCommandId'];bad('missing freeze '+name,'SnapshotRequest',value)
        value=copy.deepcopy(data['Финальный снимок месяца']); del value['financials'];bad('missing final finances','SnapshotRequest',value)
        value=copy.deepcopy(data['Готовность']);value['ready']=False;bad('false without reason','ReadinessRequest',value)
        value=copy.deepcopy(data['Подтверждение ошибки ERP']);del value['errorCode'];bad('failed without code','CommandReceiptRequest',value)
        value=copy.deepcopy(context);value['settings']['products'][0]=None;bad('null array element','Context',value)
        value=copy.deepcopy(context);value['unknown']=1;bad('closed properties','Context',value)
        value=copy.deepcopy(context);value['roundNumber']=1.5;bad('fractional round','Context',value)
        value=copy.deepcopy(context);value['serverTime']='2026-10-07T12:00:00';bad('timezone required','Context',value)
        value=copy.deepcopy(data['Состояние автоматической закупки']);del value['planId'];bad('purchase without plan','ExecutionEventRequest',value)
        command=copy.deepcopy(data['Ответ на GET commands']['commands'][0]);command['payload']={'requireClosedMonth':True,'sourceVersion':'x'};bad('payload kind mismatch','Command',command)
        protocol=(BASE/'ПротоколERP.xbsl').read_text()
        protocol=protocol[:protocol.index('@ВПроекте\nметод Ответ(')]+'\n'+protocol[protocol.index('@ВПроекте\nметод СтраницаОчереди('):]
        settings=(BASE/'НастройкиERP.xbsl').read_text()
        pure=[]
        for name in ['ПроверитьНастройки','Единица','ПроверитьБалансы','ПримерПрофиля']:
            method=settings[settings.index('метод '+name+'('):].split('\n@ВПроекте')[0]
            pure.append(method)
        source=(protocol+'\n'+'\n'.join(pure)+'\n'+(BASE/'КонтрактERP.xbsl').read_text()).replace('импорт Данные\n','').replace('@ВПроекте','@Глобально').replace('ПротоколERP.','').replace('КонтрактERP.','')
        source+='''
метод Скрипт(): Строка
    знч Профиль = Прочитать(ПримерПрофиля())
    знч Настройки = ОбъектJson(Профиль.Получить("settings"))
    ПроверитьНастройки(Настройки)
    знч Подготовка = ОбъектJson(Профиль.Получить("prepare"))
    ПроверитьСхему(Подготовка, Схема("PrepareGamePayload"))
    ПроверитьБалансы(МассивJson(Подготовка.Получить("balances")), Истина)
    Требовать(СтрокаJson(Подготовка, "settingsHash") == Хеш(КаноническийJson(Настройки)), "TEST_FAILURE", "Hash профиля отличается от Python SHA-256")
    знч Данные = Прочитать(CASES_LITERAL)
    пер Количество = 0
    для Элемент из МассивJson(Данные.Получить("cases"))
        знч Случай = ОбъектJson(Элемент)
        пер Принят = Истина
        попытка
            ПроверитьСхему(Случай.Получить("value"), Схема(СтрокаJson(Случай, "schema")))
        поймать Отказ: Ошибка
            если БулевоJson(Случай, "valid")
                выбросить новый ИсключениеНедопустимоеСостояние(СтрокаJson(Случай, "name") + ": " + Отказ.Описание + " " + Отказ.Поле)
            ;
            Принят = Ложь
            Требовать(Отказ.КодОтвета == 422, "TEST_FAILURE", "Неожиданный статус валидации")
        ;
        если Принят != БулевоJson(Случай, "valid")
            выбросить новый ИсключениеНедопустимоеСостояние("Неверный результат: " + СтрокаJson(Случай, "name"))
        ;
        Количество = Количество + 1
    ;
    знч Канон = КаноническийJson(Прочитать("{\\\"z\\\":1.0,\\\"a\\\":\\\"Русский/текст\\\",\\\"b\\\":[true,null]}"))
    Требовать(Канон == "{\\\"a\\\":\\\"Русский/текст\\\",\\\"b\\\":[true,null],\\\"z\\\":1}", "TEST_FAILURE", "Канонический JSON отличается: " + Канон)
    Требовать(Хеш("abc") == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad", "TEST_FAILURE", "Неверный SHA-256")
    Требовать(Деньги(123.1) == "123.10" и Деньги(0) == "0.00" и Деньги(1.005) == "1.01", "TEST_FAILURE", "Неверное денежное округление")
    Требовать(СуммаСНДС(3, 0.05, Истина, 10) == 0.17 и СуммаСНДС(3, 0.05, Ложь, 10) == 0.15, "TEST_FAILURE", "Неверное округление НДС")
    знч Конверты = МассивJson(Данные.Получить("queue"))
    знч Страница = СтраницаОчереди(Конверты, [1, 3], 1)
    Требовать(ЧислоJson(Страница, "acknowledgedThroughSequence") == 1, "TEST_FAILURE", "Курсор перескочил неподтверждённую команду")
    Требовать(БулевоJson(Страница, "hasMore") и МассивJson(Страница.Получить("commands")).Размер() == 1, "TEST_FAILURE", "Неверная пагинация")
    Требовать(ЧислоJson(ОбъектJson(МассивJson(Страница.Получить("commands"))[0]), "sequence") == 5, "TEST_FAILURE", "Последняя управляющая команда потеряна")
    знч Полная = СтраницаОчереди(Конверты, [1, 3], 100)
    Требовать(МассивJson(Полная.Получить("commands")).Размер() == 3 и не БулевоJson(Полная, "hasMore"), "TEST_FAILURE", "Команда потеряна")
    знч Завершена = СтраницаОчереди(Конверты, [1,2,3,4,5], 1)
    Требовать(ЧислоJson(Завершена, "acknowledgedThroughSequence") == 5 и МассивJson(Завершена.Получить("commands")).Пусто(), "TEST_FAILURE", "Терминальная очередь неверна")
    возврат "PASS:" + Количество.ВСтроку()
;
'''.replace('CASES_LITERAL', literal(json.dumps({'cases':cases,'queue':[{'commandId':str(n),'sequence':n,'kind':('pause_game' if n==4 else 'resume_game') if n>=4 else 'open_phase','payload':{'stateVersion':n}} for n in range(1,6)]},ensure_ascii=False)))
        home=Path(os.environ['ELEMENT_SCRIPT_HOME']);java=os.environ.get('ELEMENT_SCRIPT_JAVA','java')
        with tempfile.TemporaryDirectory(prefix='erp-protocol-') as temp:
            script=Path(temp)/'ERPProtocol.sbsl';script.write_text(source)
            args=[java,'--add-opens','java.base/java.lang=ALL-UNNAMED','--add-opens','java.base/java.nio=ALL-UNNAMED',f'-Dlogs.root={temp}/logs',f'-Dexecutor.location={home}',f'-Dlogback.configurationFile={home}/config/logback.xml','-cp',f'{home}/lib/*','com.e1c.g5rt.executor.boot.ExecutorBootstrap','-c','10.0',str(script)]
            result=subprocess.run(args,capture_output=True,text=True,timeout=60)
            self.assertEqual(result.returncode,0,result.stdout+'\n'+result.stderr)
            self.assertIn('PASS:'+str(len(cases)),result.stdout)
            print(f'Native Element Script 10.0: {len(cases)} schema cases, canonical JSON/SHA-256, money/VAT, profile/calendar and queue checks passed')

if __name__=='__main__':unittest.main()
