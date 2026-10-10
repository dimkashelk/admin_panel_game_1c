"""Execute actual form handlers with fake services; native DB/RLS is checked in cloud."""
from types import SimpleNamespace
import unittest
from test_form_refresh import load_form, ServiceError
import test_xbsl_logic as xbsl

class PlayerAccessFormsTests(unittest.TestCase):
    def assignments(self):
        calls=[]
        env,_,_=load_form('НазначенияИгры',SimpleNamespace())
        env['ДоступКабинета']=SimpleNamespace(НазначитьВИгре=lambda *args:calls.append(('player',args)),
            КомандыИгры=lambda game:xbsl.XArray([SimpleNamespace(Участник='team-'+game,Представление='Альфа')]))
        env['ДоступИгры']=SimpleNamespace(НазначитьПользователя=lambda *args:calls.append(('leader',args)))
        env['ПрикладныеРоли']=SimpleNamespace(Игрок='Игрок',Ведущий='Ведущий')
        env['ПослеСоздания']()
        env.update(Игра='game-a',Пользователь='team_1',УчастникERP='team-a')
        return env,calls

    def test_player_role_is_default_and_uses_game_and_team(self):
        env,calls=self.assignments()
        env['Сохранить'](None)
        self.assertEqual(calls,[('player',('game-a','team-a','team_1',True))])

    def test_player_cannot_be_assigned_without_team(self):
        env,calls=self.assignments();env['УчастникERP']=None
        env['Сохранить'](None)
        self.assertEqual(calls,[])
        self.assertIn('команду игры',env['Сообщение'])

    def test_game_change_clears_team_and_erp_mapping(self):
        env,_=self.assignments();env.update(УчастникТаблицы='team-a',ТаблицаСоответствий=[1],СоответствияERP='private')
        env['ИграИзменена'](None,None)
        self.assertIsNone(env['УчастникERP']);self.assertIsNone(env['УчастникТаблицы'])
        self.assertEqual(env['ТаблицаСоответствий'],[]);self.assertEqual(env['СоответствияERP'],'')

    def test_team_choices_use_selected_game(self):
        env,_=self.assignments()
        self.assertEqual(env['ВыборКомандИгры']()[0].Значение,'team-game-a')
        env['Игра']=None;self.assertEqual(env['ВыборКомандИгры'](),[])

    def test_leader_assignment_remains_explicit(self):
        env,calls=self.assignments();env['Роль']='Ведущий'
        env['Сохранить'](None)
        self.assertEqual(calls,[('leader',('game-a','team_1','Ведущий',True))])

    def team_form(self, fail=False, new=False):
        env,_,_=load_form('УчастникиИгрыФормаОбъекта',SimpleNamespace(),subsystem='Данные')
        calls=[]
        def create(*args):
            calls.append(args)
            if fail:raise ServiceError('Пользователь уже существует')
            return 'player-ref'
        env['Объект']=SimpleNamespace(ЭтоНовый=lambda:new,Ссылка='team-a')
        env['ДоступКабинета']=SimpleNamespace(СоздатьПользователяКоманды=create,ПользователиКоманды=lambda team:'team_2 · Игрок')
        env.update(ЛогинИгрока='team_2',ПарольИгрока='temporary-password')
        return env,calls

    def test_user_creation_assigns_to_saved_team_and_clears_password(self):
        env,calls=self.team_form();env['СоздатьПользователя'](None)
        self.assertEqual(calls,[('team-a','team_2','temporary-password')])
        self.assertEqual(env['ПользовательКоманды'],'player-ref');self.assertEqual(env['ПарольИгрока'],'')
        self.assertIn('Игрок',env['СписокПользователей'])

    def test_empty_password_is_generated_for_new_user(self):
        env,calls=self.team_form();env['ПарольИгрока']=''
        env['Ууид']=lambda:SimpleNamespace(ВСтроку=lambda:'random-generated-uuid')
        env['СоздатьПользователя'](None)
        self.assertEqual(calls,[('team-a','team_2','G!random-generated-uuid')])
        self.assertEqual(env['ПарольИгрока'],'')
        self.assertEqual(env['ВыданныйПароль'],'G!random-generated-uuid')

    def test_password_cleared_after_creation_error(self):
        env,calls=self.team_form(fail=True);env['СоздатьПользователя'](None)
        self.assertEqual(env['ПарольИгрока'],'');self.assertIn('уже существует',env['СообщениеПользователей'])

    def test_new_team_must_be_saved_before_user_creation(self):
        env,calls=self.team_form(new=True);env['СоздатьПользователя'](None)
        self.assertEqual(calls,[]);self.assertIn('сохраните',env['СообщениеПользователей'])

    def test_player_goes_to_cabinet_and_cannot_open_admin_link(self):
        env,_,_=load_form('Приложение',SimpleNamespace())
        opened=[]
        env['ДоступКабинета']=SimpleNamespace(ДоступноУправление=lambda:False,ДоступныРезультаты=lambda:False)
        env['КабинетИгрока']=SimpleNamespace(Открыть=lambda:opened.append('cabinet'))
        env['ПослеСоздания']()
        event=SimpleNamespace(СтандартнаяОбработка=True)
        env['ПриОткрытииПоСсылке'](event)
        self.assertFalse(env['УправлениеДоступно']);self.assertFalse(event.СтандартнаяОбработка)
        self.assertEqual(opened,['cabinet','cabinet'])

if __name__=='__main__':unittest.main()
