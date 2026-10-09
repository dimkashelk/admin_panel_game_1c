"""Execute original admin rules and form handlers with bounded service/DB fakes.

The query adapter returns fixture rows; it does not verify Element query syntax,
transactions, persisted records, authentication or RLS. Native compilation and
an integration pass are separate checks.
"""
import ast
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import re
from types import SimpleNamespace
import tempfile
import unittest

import test_xbsl_logic as xbsl
from test_form_refresh import load_form, Interpolations, ServiceError


def method(path, name):
    text = (xbsl.ROOT / path).read_text()
    return re.search(r'^метод ' + name + r'\([\s\S]*?^;\s*$', text, re.M).group(0)


def compile_methods(source, env):
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / 'admin.xbsl'
        path.write_text(source)
        tree = Interpolations().visit(ast.parse(xbsl.translate(path)))
    ast.fix_missing_locations(tree)
    namespace = {**vars(xbsl.CHECKS), **env}
    exec(compile(tree, '<original admin methods>', 'exec'), namespace)
    return namespace


RULES = compile_methods('\n'.join(method('Обмен/СверкаРезультатовERP.xbsl', name)
    for name in ('ПроверитьУсловияСверки', 'ПроверитьСвежесть')) + '\n' +
    method('Обмен/ДиагностикаОбменаERP.xbsl', 'СостояниеСвязи'), {'ПроверкиДанных': xbsl.CHECKS})


class ReconciliationRulesTests(unittest.TestCase):
    def test_only_invalid_real_results_with_current_version_and_reason(self):
        check = RULES['ПроверитьУсловияСверки']
        for status in ('Идет', 'Пауза', 'Завершена'):
            check(False, status, 7, 7, False, ' Исправлена реализация ')
        for args in [(True,'Завершена',7,7,False,'Причина'),
                     (False,'Черновик',7,7,False,'Причина'),
                     (False,'Подготовка',7,7,False,'Причина'),
                     (False,'Отменена',7,7,False,'Причина'),
                     (False,'Завершена',6,7,False,'Причина'),
                     (False,'Завершена',7,7,True,'Причина'),
                     (False,'Завершена',7,7,False,'   '),
                     (False,'Завершена',7,7,False,'x'*1001)]:
            with self.subTest(args=args), self.assertRaises(ValueError): check(*args)

    def test_snapshot_is_new_after_received_facts_and_not_from_future(self):
        check = RULES['ПроверитьСвежесть']
        now = datetime(2026,10,8,10,tzinfo=timezone.utc)
        old, fact = now-timedelta(hours=2), now-timedelta(hours=1)
        check(now, old, fact, now)
        check(now, old, None, now)
        for captured in [old-timedelta(seconds=1), old, fact, fact-timedelta(seconds=1), now+timedelta(seconds=1)]:
            with self.subTest(captured=captured), self.assertRaises(ValueError): check(captured, old, fact, now)

    def test_stale_connection_label_does_not_claim_transport_failure(self):
        check = RULES['СостояниеСвязи']
        self.assertEqual(check(False, 0), 'Сообщения ещё не получены')
        self.assertEqual(check(True, 120), 'Сообщение получено недавно')
        self.assertEqual(check(True, 121), 'Более 2 минут без сообщений')


class PublicationTests(unittest.TestCase):
    def publisher(self, missing_round=None, bad_round=None, issue='confirmation', version=7):
        game=SimpleNamespace(Ссылка='game',Статус='Завершена',ВерсияСостояния=version,
                             ЧислоРаундов=Decimal(3),РезультатыОпубликованы=False)
        writes=[]
        game.Записать=lambda: writes.append(game.ВерсияСостояния)
        rounds={Decimal(n):SimpleNamespace(Ссылка=f'round-{n}',ЗагрузитьОбъект=lambda:SimpleNamespace(Игра='game'),number=n) for n in range(4)}
        if missing_round is not None:del rounds[Decimal(missing_round)]
        visited=[]
        def query(text, local):
            if 'ИЗ РаундыИгры' in text:return SimpleNamespace(Ссылка=rounds[local['Номер']].Ссылка) if local['Номер'] in rounds else None
            if 'ИЗ УчастникиИгры' in text:return [SimpleNamespace(Ссылка='team-a'),SimpleNamespace(Ссылка='team-b')]
            number=int(local['Раунд'].split('-')[1]);team=local['ПараметрЗапроса5_0']
            visited.append((number,team))
            if number==bad_round and team=='team-b' and issue=='missing':return None
            return SimpleNamespace(ФинальныйСнимок=None if number==bad_round and team=='team-b' and issue=='snapshot' else 'snapshot',
                Долги=Decimal(1) if number==bad_round and team=='team-b' and issue=='debt' else Decimal(0),
                ЗакрытиеМесяцаПодтверждено=not(number==bad_round and team=='team-b' and issue=='confirmation'))
        src='\n'.join(method('Аналитика/РезультатыИгры.xbsl', name) for name in ['ПроверитьЗакрытиеРаунда','ПроверитьЗакрытиеИгры','Опубликовать'])
        # Query/resource adaptation only; business conditions execute unchanged.
        src=re.sub(r'^\s*исп .+$','',src,flags=re.M)
        src=src.replace('Игра.ЗагрузитьОбъект(Истина)!','ОбъектИгры').replace('Раунд.ЗагрузитьОбъект()!.Игра!','ИграРаунда(Раунд)')
        src=re.sub(r'Запрос\{([\s\S]*?)\}\.Выполнить\(\)(?:\.ПервыйИлиНеопределено\(\))?',lambda m:'ЗапросТест('+repr(m[1]).replace("'",'"')+', Локальные())',src)
        # Query text uses escaped newlines accepted as literal content by the harness.
        env=compile_methods(src,{'ОбъектИгры':game,'ИграРаунда':lambda _: 'game',
            'ЗапросТест':query,'Локальные':lambda:None,'ДоступИгры':SimpleNamespace(ПроверитьВедущего=lambda _:None),
            'ПроверкиДанных':xbsl.CHECKS})
        # Bind locals from the executing method for fixture query parameters.
        import inspect
        env['Локальные']=lambda: inspect.currentframe().f_back.f_locals
        return env,game,writes,visited

    def test_publish_requires_all_teams_in_every_round_including_round_zero(self):
        env,game,writes,visited=self.publisher()
        env['Опубликовать']('game',Decimal(7))
        self.assertTrue(game.РезультатыОпубликованы)
        self.assertEqual(writes,[Decimal(8)])
        self.assertEqual(visited,[(n,team) for n in range(4) for team in ['team-a','team-b']])

    def test_missing_round_or_invalid_intermediate_result_blocks_publication(self):
        for n in range(4):
            for issue in ['confirmation','missing','snapshot','debt']:
                with self.subTest(n=n,issue=issue):
                    env,game,writes,_=self.publisher(bad_round=n,issue=issue)
                    with self.assertRaises(ValueError):env['Опубликовать']('game',Decimal(7))
                    self.assertFalse(game.РезультатыОпубликованы);self.assertEqual(writes,[])
            env,game,writes,_=self.publisher(missing_round=n)
            with self.assertRaises(ValueError):env['Опубликовать']('game',Decimal(7))
            self.assertFalse(game.РезультатыОпубликованы);self.assertEqual(writes,[])

    def test_stale_version_does_not_publish_or_write(self):
        env,game,writes,_=self.publisher(version=8)
        with self.assertRaises(ValueError):env['Опубликовать']('game',Decimal(7))
        self.assertFalse(game.РезультатыОпубликованы);self.assertEqual(writes,[])


class AdminFormsTests(unittest.TestCase):
    def diagnostics(self, respond):
        env,clock,timers=load_form('ДиагностикаERP',SimpleNamespace())
        env['Игра']='game-1'
        env['ДиагностикаОбменаERP']=SimpleNamespace(Получить=lambda *args:respond(env,*args))
        return env,clock,timers

    def test_diagnostics_drops_old_game_or_filter_response(self):
        for change in [{'Игра':'game-2'},{'ТолькоНезавершенные':True}]:
            def response(env,*_):
                env.update(change)
                return SimpleNamespace(Подключения=['old'],Команды=['old'],Сводка='old')
            env,_,_=self.diagnostics(response)
            env['Прочитать']()
            self.assertEqual(env['Задания'],[]);self.assertEqual(env['Подключения'],[])
            self.assertFalse(env['ОбновлениеВыполняется'])

    def test_diagnostics_clears_private_data_on_access_or_transport_error(self):
        def response(env,*_):raise ServiceError('Нет доступа')
        env,_,_=self.diagnostics(response);env.update(Подключения=['old'],Задания=['old'],Сводка='old')
        env['Прочитать']()
        self.assertEqual(env['Задания'],[]);self.assertEqual(env['Подключения'],[])
        self.assertIn('Нет доступа',env['Сообщение'])
        self.assertFalse(env['ОбновлениеВыполняется'])

    def test_diagnostics_one_timer_and_no_requests_for_closed_form(self):
        calls=[]
        env,_,timers=self.diagnostics(lambda *args:calls.append(args))
        env['ПослеСоздания']();self.assertEqual(len(timers),1)
        env['Открыта']=False;timers[0][0]()
        self.assertEqual(calls,[])

    def test_result_details_from_old_game_are_discarded(self):
        env,_,_=load_form('РезультатыИгрыФорма',SimpleNamespace(),subsystem='Аналитика')
        env.update(Игра='game-1',Раунд='round-1')
        def response(*args):
            env['Игра']='game-2'
            return SimpleNamespace(Рейтинг=['old'],Модели=['old'],Остатки=['old'],История=['old'])
        env['РезультатыИгры']=SimpleNamespace(ПолучитьДетали=response)
        env['Обновить'](None)
        for name in ['Строки','Модели','Остатки','История']:self.assertEqual(env[name],[])
        self.assertFalse(env['ОбновлениеВыполняется'])

    def test_mapping_table_cannot_be_saved_for_another_team(self):
        env,_,_=load_form('НазначенияИгры',SimpleNamespace())
        writes=[];env['ОбменERP']=SimpleNamespace(СохранитьТаблицуСоответствий=lambda *args:writes.append(args))
        env.update(УчастникERP='team-2',УчастникТаблицы='team-1',ТаблицаСоответствий=['old'])
        env['СохранитьТаблицу'](None)
        self.assertEqual(writes,[])
        self.assertIn('Сначала загрузите',env['Сообщение'])

    def test_mapping_change_clears_old_table_and_json(self):
        env,_,_=load_form('НазначенияИгры',SimpleNamespace())
        env.update(УчастникТаблицы='team-1',ТаблицаСоответствий=['old'],СоответствияERP='old json')
        env['УчастникПриИзменении'](None,None)
        self.assertEqual(env['ТаблицаСоответствий'],[]);self.assertEqual(env['СоответствияERP'],'')
        self.assertIsNone(env['УчастникТаблицы'])

    def test_reconciliation_choices_only_include_invalid_periods_and_selected_teams(self):
        env,_,_=load_form('СверкаИтоговERP',SimpleNamespace())
        env['Периоды']=xbsl.XArray([
            SimpleNamespace(Раунд='round-1',Участник='team-a',Команда='Альфа',НомерРаунда=1,Подтвержден=False),
            SimpleNamespace(Раунд='round-1',Участник='team-b',Команда='Бета',НомерРаунда=1,Подтвержден=False),
            SimpleNamespace(Раунд='round-2',Участник='team-a',Команда='Альфа',НомерРаунда=2,Подтвержден=True),
            SimpleNamespace(Раунд='round-3',Участник='team-b',Команда='Бета',НомерРаунда=3,Подтвержден=False)])
        self.assertEqual([x.Значение for x in env['ПолучитьРаундыДляВыбора']()],['round-1','round-3'])
        self.assertEqual(env['ПолучитьКомандыДляВыбора'](),[])
        env['Раунд']='round-3'
        self.assertEqual([x.Значение for x in env['ПолучитьКомандыДляВыбора']()],['team-b'])

    def test_round_list_received_for_old_game_is_discarded(self):
        env,_,_=load_form('РезультатыИгрыФорма',SimpleNamespace(),subsystem='Аналитика')
        env.update(Игра='game-1',Раунд='old-round',Раунды=['old'])
        def response(*args):
            env['Игра']='game-2'
            return ['round-from-game-1']
        env['РезультатыИгры']=SimpleNamespace(ПолучитьРаундыВедущего=response)
        env['ИграПриИзменении'](None,None)
        self.assertEqual(env['Раунды'],[])
        self.assertIsNone(env['Раунд'])

if __name__=='__main__':unittest.main()
