#!/usr/bin/env python3
"""Execute wizard XBSL with a fake creation service; not native UI/DB QA."""
from types import SimpleNamespace
import unittest

from test_form_refresh import ServiceError, load_form
import test_xbsl_logic as xbsl


class NewGameTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.error = None

        def create(*args):
            self.calls.append(args)
            if self.error:
                raise self.error
            return 'new-game'

        self.env, _, _ = load_form('МастерНовойИгры', SimpleNamespace(СоздатьИгру=create))
        self.env['ПослеСоздания']()
        self.env['Сценарий'] = 'scenario'

    def teams(self, names):
        self.env['Команды'] = xbsl.XArray(
            self.env['ВводКоманды'](Наименование=name) for name in names)

    def create(self):
        self.env['Создать'](None)

    def test_default_two_teams(self):
        self.create()
        self.assertEqual(self.calls, [('Учебная игра', 'scenario', ['Альфа', 'Бета'], 42, True, True)])
        self.assertEqual(self.env['СозданнаяИгра'], 'new-game')
        self.assertIn('Команд: 2', self.env['Сообщение'])

    def test_can_choose_legacy_allocation_market(self):
        self.env['СвободныйРынок'] = False
        self.create()
        self.assertFalse(self.calls[0][5])

    def test_arbitrary_team_count_keeps_names_and_order(self):
        for count in (3, 4, 20):
            with self.subTest(count=count):
                names = [f'Команда {index}' for index in range(1, count + 1)]
                self.teams(names)
                self.create()
                self.assertEqual(self.calls[-1][2], names)
                self.assertIn(f'Команд: {count}', self.env['Сообщение'])

    def test_add_edit_and_remove_before_creation(self):
        rows = self.env['Команды']
        rows.append(self.env['ВводКоманды'](Наименование='Гамма'))
        rows[0].Наименование = 'Вектор'
        rows.remove(rows[1])
        self.create()
        self.assertEqual(self.calls[0][2], ['Вектор', 'Гамма'])

    def test_trims_names_without_losing_form_edits(self):
        self.teams(['  Вектор  ', ' Бета '])
        self.create()
        self.assertEqual(self.calls[0][2], ['Вектор', 'Бета'])
        self.assertEqual(self.env['Команды'][0].Наименование, '  Вектор  ')

    def test_rejects_missing_or_duplicate_names_before_server_call(self):
        for names, message in ((['Альфа', ''], 'название каждой'),
                               (['Альфа', '   '], 'название каждой'),
                               (['Альфа', 'Бета', 'Альфа'], 'уникальными'),
                               (['Альфа', ' Альфа '], 'уникальными')):
            with self.subTest(names=names):
                self.teams(names)
                self.create()
                self.assertEqual(self.calls, [])
                self.assertIsNone(self.env['СозданнаяИгра'])
                self.assertIn(message, self.env['Сообщение'])

    def test_rejects_fewer_than_two_teams(self):
        for names in ([], ['Альфа']):
            with self.subTest(names=names):
                self.teams(names)
                self.create()
                self.assertEqual(self.calls, [])
                self.assertIn('минимум две', self.env['Сообщение'])

    def test_missing_scenario_preserves_team_list(self):
        self.teams(['Альфа', 'Бета', 'Гамма'])
        self.env['Сценарий'] = None
        self.create()
        self.assertEqual(self.calls, [])
        self.assertEqual(len(self.env['Команды']), 3)
        self.assertEqual(self.env['Сообщение'], 'Выберите сценарий')

    def test_server_error_preserves_input_for_retry(self):
        self.teams(['Альфа', 'Бета', 'Гамма'])
        self.error = ServiceError('Создание не удалось')
        self.create()
        self.assertIsNone(self.env['СозданнаяИгра'])
        self.assertEqual(self.env['Сообщение'], 'Создание не удалось')
        self.assertEqual(len(self.env['Команды']), 3)
        self.error = None
        self.create()
        self.assertEqual(self.env['СозданнаяИгра'], 'new-game')
        self.assertEqual(self.calls[-1][2], ['Альфа', 'Бета', 'Гамма'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
