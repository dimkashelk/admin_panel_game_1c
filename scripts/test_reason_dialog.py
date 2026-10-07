"""Check modal confirmation boundaries with the existing local XBSL harness."""
from decimal import Decimal
from types import SimpleNamespace
import unittest

from test_form_refresh import load_form


class ReasonDialogTests(unittest.TestCase):
    def panel(self, result, during_dialog=None):
        calls = []
        service = SimpleNamespace(**{name: lambda *args, name=name: calls.append((name, args))
            for name in ('Пауза', 'Отменить', 'ИсключитьУчастника')})
        env, _, _ = load_form('ПультВедущего', service)
        env.update(Игра='game-1', Участник='team-1', Версия=Decimal(7))
        env['ПрочитатьСостояние'] = lambda: None
        dialogs = []

        def create_dialog():
            def open_dialog():
                if during_dialog:
                    during_dialog(env)
                return result
            dialog = SimpleNamespace(ОткрытьВМодальномОкне=open_dialog)
            dialogs.append(dialog)
            return dialog

        env['ВводПричины'] = create_dialog
        return env, calls, dialogs

    def test_cancel_does_not_call_any_transition(self):
        for action in ('Пауза', 'Отменить', 'ИсключитьУчастника'):
            with self.subTest(action=action):
                env, calls, dialogs = self.panel(None)
                env[action](None)
                self.assertEqual(calls, [])
                self.assertEqual(len(dialogs), 1)

    def test_confirm_passes_reason_and_original_version(self):
        for action in ('Пауза', 'Отменить', 'ИсключитьУчастника'):
            with self.subTest(action=action):
                env, calls, _ = self.panel('Причина', lambda state: state.update(Версия=Decimal(8)))
                env[action](None)
                expected = ('game-1', Decimal(7)) + (('team-1',) if action == 'ИсключитьУчастника' else ()) + ('Причина',)
                self.assertEqual(calls, [(action, expected)])

    def test_game_change_during_dialog_blocks_transition(self):
        for action in ('Пауза', 'Отменить', 'ИсключитьУчастника'):
            with self.subTest(action=action):
                env, calls, _ = self.panel('Причина', lambda state: state.update(Игра='game-2'))
                env[action](None)
                self.assertEqual(calls, [])
                self.assertIn('другая игра', env['Сообщение'])

    def test_exclusion_keeps_the_confirmed_target(self):
        env, calls, _ = self.panel('Причина', lambda state: state.update(Участник='team-2'))
        env['ИсключитьУчастника'](None)
        self.assertEqual(calls[0][1][2], 'team-1')

    def test_team_picker_contains_only_loaded_game_participants(self):
        env, _, _ = self.panel(None)
        env['Участники'] = [SimpleNamespace(Участник='team-1', Команда='Альфа'),
            SimpleNamespace(Участник='team-2', Команда='Бета')]
        choices = env['ПолучитьКомандыДляВыбора']()
        self.assertEqual([(item.Значение, item.Представление) for item in choices],
            [('team-1', 'Альфа'), ('team-2', 'Бета')])
        self.assertEqual(env['ПолучитьНазваниеКоманды']('team-2'), 'Бета')
        env['СброситьСостояние']()
        self.assertEqual(env['ПолучитьКомандыДляВыбора'](), [])

    def test_missing_selection_does_not_open_dialog(self):
        env, calls, dialogs = self.panel('Причина')
        env['Игра'] = None
        env['Пауза'](None)
        self.assertEqual(calls, [])
        self.assertEqual(dialogs, [])

    def test_blank_reason_keeps_modal_open_and_valid_reason_is_trimmed(self):
        env, _, _ = load_form('ВводПричины', SimpleNamespace())
        closed = []
        env['Закрыть'] = lambda *args: closed.append(args)
        env['Причина'] = '   '
        env['Подтвердить'](None)
        self.assertEqual(closed, [])
        self.assertIn('Укажите причину', env['Сообщение'])
        env['Причина'] = '  Перерыв  '
        env['Подтвердить'](None)
        self.assertEqual(closed, [('Перерыв',)])

    def test_cancel_modal_has_no_reason_result(self):
        env, _, _ = load_form('ВводПричины', SimpleNamespace())
        closed = []
        env['Закрыть'] = lambda *args: closed.append(args)
        env['Отмена'](None)
        self.assertEqual(closed, [()])


if __name__ == '__main__':
    unittest.main()
