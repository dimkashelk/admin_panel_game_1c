"""Exercise actual setup handlers with bounded services; no live users/ERP writes."""
from types import SimpleNamespace as NS
import unittest
import test_xbsl_logic as xbsl
from test_form_refresh import load_form, ServiceError


def response(**values):
    return NS(Получить=values.__getitem__)


class GameSetupTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.admin = True
        self.env, _, _ = load_form('МастерНовойИгры', NS())
        self.env['ПослеСоздания']()
        self.env['УправлениеИгрой'] = NS(
            ПолучитьНастройки=lambda game: response(name=game, scenario='scenario', seed=42,
                demo=False, market=True, version=3, draft=True, status='Черновик', admin=self.admin),
            СохранитьНастройки=lambda *args: self.calls.append(('game', args)),
            СохранитьКоманду=lambda *args: self.calls.append(('team', args)) or 'team-new')
        self.env['ДоступКабинета'] = NS(
            КомандыИгры=lambda game: xbsl.XArray([NS(Участник='team-a',Представление='Альфа'), NS(Участник='team-b',Представление='Бета')]),
            ПользователиКоманды=lambda team: 'players-'+team,
            НазначитьВИгре=lambda *args: self.calls.append(('player', args)),
            СоздатьПользователяКоманды=lambda *args: self.calls.append(('new-player', args)) or 'player-ref')
        self.env['НастройкаБазOData'] = NS(
            ПодключениеКоманды=lambda game,team: response(connection='base-'+team,
                address='https://erp.example/'+team+'/', login='odata-'+team,
                organization=team+' ООО', hasPassword=True, active=True),
            СохранитьПодключениеКоманды=lambda *args: self.calls.append(('base', args)))
        self.env['СозданнаяИгра']='game-a'
        self.env['ОбновитьНастройки'](None)

    def test_existing_game_auto_loads_first_team_users_and_base(self):
        self.assertTrue(self.env['ПравилаСвернуты'])
        self.assertEqual(self.env['ЗагруженнаяИгра'],'game-a')
        self.assertEqual(self.env['УчастникERP'],'team-a')
        self.assertEqual(self.env['ЗагруженнаяКоманда'],'team-a')
        self.assertEqual(self.env['СписокПользователей'],'players-team-a')
        self.assertEqual(self.env['ПодключениеERP'],'base-team-a')
        self.assertEqual(self.env['ПарольКоманды'],'')

    def test_reload_keeps_selected_team(self):
        self.env['УчастникERP']='team-b';self.env['КомандаИзменена'](None,None)
        self.env['ОбновитьНастройки'](None)
        self.assertEqual(self.env['УчастникERP'],'team-b')

    def test_switch_team_clears_passwords_and_player_inputs(self):
        self.env.update(ПарольКоманды='secret',ПарольИгрока='temporary',ВыданныйПароль='issued',Пользователь='previous-player',ЛогинИгрока='old-login')
        self.env['УчастникERP']='team-b';self.env['КомандаИзменена'](None,None)
        for name in ('ПарольКоманды','ПарольИгрока','ВыданныйПароль','ЛогинИгрока'):
            self.assertEqual(self.env[name],'')
        self.assertIsNone(self.env['Пользователь'])
        self.assertEqual(self.env['СписокПользователей'],'players-team-b')

    def test_switch_game_discards_previous_team_data(self):
        self.env['СозданнаяИгра']='game-b';self.env['ИграИзменена'](None,None)
        self.assertEqual(self.env['ЗагруженнаяИгра'],'game-b')
        self.assertEqual(self.env['Название'],'game-b')

    def test_loading_error_hides_previous_team_and_base(self):
        self.env['НастройкаБазOData'].ПодключениеКоманды=lambda *args: (_ for _ in ()).throw(ServiceError('Нет доступа'))
        self.env['УчастникERP']='team-b';self.env['КомандаИзменена'](None,None)
        self.assertIsNone(self.env['ЗагруженнаяКоманда'])
        self.assertIsNone(self.env['ПодключениеERP'])
        self.assertEqual(self.env['АдресКоманды'],'')

    def test_late_team_response_cannot_populate_another_team(self):
        def read(*args):
            self.env['УчастникERP']='team-b'
            return response()
        self.env['НастройкаБазOData'].ПодключениеКоманды=read
        self.env['УчастникERP']='team-a';self.env['КомандаИзменена'](None,None)
        self.assertIsNone(self.env['ЗагруженнаяКоманда'])
        self.assertEqual(self.env['АдресКоманды'],'')

    def test_late_game_response_cannot_populate_another_game(self):
        def read(*args):
            self.env['СозданнаяИгра']='game-b'
            return response()
        self.env['УправлениеИгрой'].ПолучитьНастройки=read
        self.env['ОбновитьНастройки'](None)
        self.assertIsNone(self.env['ЗагруженнаяИгра'])
        self.assertIsNone(self.env['ЗагруженнаяКоманда'])

    def test_save_settings_passes_loaded_version(self):
        self.env['Название']='Новое название';self.env['СохранитьИгру'](None)
        self.assertEqual(self.calls,[('game',('game-a',3,'Новое название','scenario',42,False,True))])

    def test_add_and_rename_team_use_current_game_version(self):
        self.env['НоваяКоманда']='Гамма';self.env['ДобавитьКоманду'](None)
        self.assertEqual(self.calls[0],('team',('game-a',3,None,'Гамма')))
        self.env['НазваниеКоманды']='Вектор';self.env['ПереименоватьКоманду'](None)
        self.assertEqual(self.calls[1],('team',('game-a',3,'team-a','Вектор')))

    def test_player_assignment_does_not_require_other_form(self):
        self.env['Пользователь']='player';self.env['ДобавитьИгрока'](None);self.env['ОтключитьИгрока'](None)
        self.assertEqual(self.calls,[('player',('game-a','team-a','player',True)),('player',('game-a','team-a','player',False))])

    def test_create_player_generates_password_and_assigns_selected_team(self):
        self.env['Ууид']=lambda:NS(ВСтроку=lambda:'generated-uuid')
        self.env['ЛогинИгрока']='team_3';self.env['СоздатьИгрока'](None)
        self.assertEqual(self.calls,[('new-player',('team-a','team_3','G!generated-uuid'))])
        self.assertEqual(self.env['ВыданныйПароль'],'G!generated-uuid')
        self.assertEqual(self.env['ПарольИгрока'],'')
        self.assertFalse(self.env['Занято'])

    def test_creation_failure_clears_temporary_and_issued_password(self):
        self.env['ДоступКабинета'].СоздатьПользователяКоманды=lambda *args: (_ for _ in ()).throw(ServiceError('Логин занят'))
        self.env.update(ЛогинИгрока='taken',ПарольИгрока='temporary',ВыданныйПароль='old')
        self.env['СоздатьИгрока'](None)
        self.assertEqual(self.env['ПарольИгрока'],'');self.assertEqual(self.env['ВыданныйПароль'],'')
        self.assertEqual(self.env['Сообщение'],'Логин занят')

    def test_save_base_and_failure_clear_password(self):
        self.env['ПарольКоманды']='new-password';self.env['СохранитьБазу'](None)
        self.assertEqual(self.calls,[('base',('game-a','team-a','https://erp.example/team-a/','team-a ООО','odata-team-a','new-password',True))])
        self.env['НастройкаБазOData'].СохранитьПодключениеКоманды=lambda *args: (_ for _ in ()).throw(ServiceError('Ошибка сохранения'))
        self.env['ПарольКоманды']='failed-password';self.env['СохранитьБазу'](None)
        self.assertEqual(self.env['ПарольКоманды'],'')

    def test_mismatched_loaded_context_prevents_mutations(self):
        for field,value in [('СозданнаяИгра','game-b'),('УчастникERP','team-b')]:
            with self.subTest(field=field):
                previous=self.env[field];self.env[field]=value
                self.env.update(Пользователь='player',ПарольКоманды='secret',ЛогинИгрока='new-player',ПарольИгрока='temporary')
                self.env['ДобавитьИгрока'](None);self.env['СохранитьБазу'](None);self.env['СоздатьИгрока'](None)
                self.assertEqual(self.calls,[])
                self.assertEqual(self.env['ПарольКоманды'],'');self.assertEqual(self.env['ПарольИгрока'],'')
                self.env[field]=previous

    def test_new_game_resets_selected_game_and_secrets(self):
        self.env.update(ПарольКоманды='secret',ВыданныйПароль='issued');self.env['НоваяИгра'](None)
        self.assertIsNone(self.env['СозданнаяИгра']);self.assertIsNone(self.env['УчастникERP'])
        self.assertEqual(self.env['ПарольКоманды'],'');self.assertEqual(self.env['ВыданныйПароль'],'')
        self.assertEqual(len(self.env['Команды']),2)
        self.assertFalse(self.env['ПравилаСвернуты'])

    def test_non_admin_leader_can_load_base_without_reading_private_user_list(self):
        self.admin=False
        self.env['ДоступКабинета'].ПользователиКоманды=lambda *args: (_ for _ in ()).throw(ServiceError('Только администратор'))
        self.env['ОбновитьНастройки'](None)
        self.assertEqual(self.env['ЗагруженнаяКоманда'],'team-a')
        self.assertFalse(self.env['Администратор'])

    def test_preparation_popup_receives_selected_base_and_team(self):
        window=NS();window.ОткрытьВМодальномОкне=lambda:self.calls.append(('popup',window))
        self.env['НастройкаИнформационныхБаз']=lambda:window
        self.env['ПодготовкаБазы'](None)
        self.assertEqual(window.Подключение,'base-team-a');self.assertEqual(window.КомандаERP,'team-a')
        self.assertEqual(window.ОрганизацияИмя,'team-a ООО');self.assertEqual(window.Логин,'odata-team-a')
        self.assertFalse(hasattr(window,'Пароль'))

    def test_busy_state_prevents_duplicate_mutations(self):
        self.env['Занято']=True
        for action in ['СохранитьИгру','ДобавитьКоманду','ДобавитьИгрока','СоздатьИгрока','СохранитьБазу']:
            self.env[action](None)
        self.assertEqual(self.calls,[])

if __name__=='__main__':unittest.main()
