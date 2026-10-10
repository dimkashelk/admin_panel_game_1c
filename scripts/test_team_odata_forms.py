"""Run actual UI handlers with fake services; no ERP writes or native DB/RLS claims."""
from pathlib import Path
from types import SimpleNamespace
import unittest
import yaml
from test_form_refresh import load_form, ServiceError

ROOT = Path(__file__).resolve().parents[1] / 'dimkashelk' / 'Игра'


class TeamODataFormsTests(unittest.TestCase):
    def leader(self):
        env, _, _ = load_form('НазначенияИгры', SimpleNamespace())
        calls = []
        response = {'address': 'https://erp.example/team-a/', 'login': 'odata-a',
                    'organization': 'Альфа ООО', 'hasPassword': True, 'active': True}
        def read(*args):
            calls.append(('read', args))
            return SimpleNamespace(Получить=response.__getitem__)
        def save(*args):
            calls.append(('save', args))
        env.update(Игра='game-a', УчастникERP='team-a',
            НастройкаБазOData=SimpleNamespace(ПодключениеКоманды=read, СохранитьПодключениеКоманды=save))
        return env, calls

    def test_load_shows_password_presence_without_revealing_value(self):
        env, calls = self.leader()
        env['ЗагрузитьПодключение'](None)
        self.assertEqual(calls, [('read', ('game-a', 'team-a'))])
        self.assertEqual(env['АдресКоманды'], 'https://erp.example/team-a/')
        self.assertEqual(env['ЛогинКоманды'], 'odata-a')
        self.assertEqual(env['ПарольКоманды'], '')
        self.assertEqual(env['УчастникПодключения'], 'team-a')
        self.assertIn('Пароль сохранён', env['СостояниеПодключения'])

    def test_save_sends_current_team_settings_and_clears_password(self):
        env, calls = self.leader(); env['ЗагрузитьПодключение'](None)
        env['ПарольКоманды'] = 'test-password'
        env['СохранитьПодключение'](None)
        self.assertEqual(calls[1], ('save', ('game-a', 'team-a', 'https://erp.example/team-a/',
            'Альфа ООО', 'odata-a', 'test-password', True)))
        self.assertEqual(env['ПарольКоманды'], '')

    def test_save_failure_also_clears_password(self):
        env, _ = self.leader(); env['ЗагрузитьПодключение'](None)
        def fail(*args): raise ServiceError('Нет доступа ведущего')
        env['НастройкаБазOData'].СохранитьПодключениеКоманды = fail
        env['ПарольКоманды'] = 'test-password'; env['СохранитьПодключение'](None)
        self.assertEqual(env['ПарольКоманды'], '')
        self.assertEqual(env['Сообщение'], 'Нет доступа ведущего')

    def test_game_and_team_changes_clear_all_connection_state(self):
        for handler in ['ИграИзменена', 'УчастникПриИзменении']:
            with self.subTest(handler=handler):
                env, _ = self.leader(); env['ЗагрузитьПодключение'](None)
                env['ПарольКоманды'] = 'test-password'; env[handler](None, None)
                self.assertIsNone(env['УчастникПодключения'])
                for field in ['АдресКоманды', 'ЛогинКоманды', 'ПарольКоманды', 'ОрганизацияКоманды']:
                    self.assertEqual(env[field], '')

    def test_cannot_save_previous_team_settings_into_new_team(self):
        env, calls = self.leader(); env['ЗагрузитьПодключение'](None)
        env['УчастникERP'] = 'team-b'; env['ПарольКоманды'] = 'test-password'
        env['СохранитьПодключение'](None)
        self.assertEqual(len(calls), 1)
        self.assertEqual(env['ПарольКоманды'], '')
        self.assertIn('загрузите подключение', env['Сообщение'])

    def test_late_response_for_previous_team_is_discarded(self):
        env, _ = self.leader()
        def read(*args):
            env['УчастникERP'] = 'team-b'; env['УчастникПриИзменении'](None, None)
            return SimpleNamespace(Получить=lambda key: 'old-team-data')
        env['НастройкаБазOData'].ПодключениеКоманды = read
        env['ЗагрузитьПодключение'](None)
        self.assertIsNone(env['УчастникПодключения'])
        self.assertEqual(env['АдресКоманды'], '')

    def test_player_button_uses_only_team_order_and_phase(self):
        env, _, _ = load_form('КабинетИгрока', SimpleNamespace())
        calls = []
        env.update(Участник='team-a', Токен='phase-a',
            ВыбранныйЗаказ=SimpleNamespace(Заказ='order-a', МожноПолучить=True),
            КабинетыИгроков=SimpleNamespace(ПолучитьЗаказ=lambda *args:
                (calls.append(args) or 'Заказ проведён в ERP')),
            Прочитать=lambda: None)
        env['ПолучитьЗаказ'](None)
        self.assertEqual(calls, [('team-a', 'order-a', 'phase-a')])
        self.assertEqual(env['Сообщение'], 'Заказ проведён в ERP')
        self.assertFalse(env['Занято'])
        descriptor = yaml.safe_load((ROOT / 'Управление/КабинетИгрока.yaml').read_text())
        names = {item['Имя'] for item in descriptor['Свойства']}
        self.assertFalse({'Логин', 'Пароль'} & names)


if __name__ == '__main__':
    unittest.main()
