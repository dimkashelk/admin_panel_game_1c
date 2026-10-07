#!/usr/bin/env python3
"""Execute form refresh code with a fake clock/service; not native UI/DB QA."""
import ast
from decimal import Decimal
import re
from types import SimpleNamespace
import unittest
import yaml

import test_xbsl_logic as xbsl


class ServiceError(Exception):
    def __init__(self, description='Нет связи'):
        super().__init__(description)
        self.Описание = description


class Interpolations(ast.NodeTransformer):
    def visit_Constant(self, node):
        if not isinstance(node.value, str) or '%{' not in node.value:
            return node
        pieces = re.split(r'%\{([^{}]+)\}', node.value)
        return ast.JoinedStr([ast.Constant(value) if index % 2 == 0 else
            ast.FormattedValue(ast.parse(xbsl.expression(value), mode='eval').body, -1, None)
            for index, value in enumerate(pieces)])


def load_form(name, service):
    path = xbsl.ROOT / 'Управление' / f'{name}.yaml'
    descriptor = yaml.safe_load(path.read_text())
    original_expression = xbsl.expression

    def ui_expression(text):
        parts = re.split(r'("(?:[^"\\]|\\.)*")', text)
        for index in range(0, len(parts), 2):
            parts[index] = re.sub(r'\b(\d+)с\b', r'\1', parts[index])
            parts[index] = re.sub(r'&(\w+)', r'\1', parts[index])
            parts[index] = parts[index].replace(
                'Массив<ЭлементСпискаЗначений<УчастникиИгры.Ссылка?>>', 'XArray')
            parts[index] = parts[index].replace(
                'ЭлементСпискаЗначений<УчастникиИгры.Ссылка?>', 'ЭлементСпискаЗначений')
        return original_expression(''.join(parts))

    try:
        xbsl.expression = ui_expression
        tree = ast.parse(xbsl.translate(path.with_suffix('.xbsl')))
    finally:
        xbsl.expression = original_expression
    properties = {prop['Имя'] for prop in descriptor['Свойства']}
    for function in tree.body:
        if isinstance(function, ast.FunctionDef):
            names = sorted(properties - {arg.arg for arg in function.args.args})
            function.body.insert(0, ast.Global(names))
    tree = Interpolations().visit(tree)
    ast.fix_missing_locations(tree)
    clock = SimpleNamespace(now=Decimal(100))
    timers = []

    enum_labels = {}
    for enum_path in (xbsl.ROOT / 'Данные').glob('*.yaml'):
        enum = yaml.safe_load(enum_path.read_text())
        if enum.get('ВидЭлемента') == 'Перечисление':
            enum_labels.update({item['Имя']: item.get('Представление', item['Имя'])
                                for item in enum['Элементы']})

    def ui_call(receiver, method, *args):
        if method == 'Представление':
            return enum_labels[receiver]
        return receiver if method == 'ВСекундах' else xbsl.call(receiver, method, *args)

    env = {**vars(xbsl.SUMMARIES), 'call': ui_call,
        'XArray': xbsl.XArray, 'ЭлементСпискаЗначений': SimpleNamespace,
        'Момент': SimpleNamespace(Сейчас=lambda: clock.now),
        'Время': SimpleNamespace(Сейчас=lambda: clock.now),
        'УправлениеИгрой': service, 'СводкиИгры': xbsl.SUMMARIES,
        'ПроверкиДанных': SimpleNamespace(Требовать=lambda condition, message:
            None if condition else (_ for _ in ()).throw(ServiceError(message))),
        'Открыта': True,
        'ПодключитьОбработчикТаймера': lambda callback, interval: timers.append((callback, interval))}
    defaults = xbsl.make_dto(descriptor['Свойства'])()
    env.update(vars(defaults))
    exec(compile(tree, str(path.with_suffix('.xbsl')), 'exec'), env)
    return env, clock, timers


class RefreshTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.panel = xbsl.SUMMARIES.СостояниеПульта(Игра='game-1', Статус='Идет',
            Фаза='Производство', Токен='phase-1', Версия=Decimal(1),
            ТаймерВключен=True, СекундОсталось=Decimal(65),
            Участники=xbsl.XArray([xbsl.SUMMARIES.СтрокаГотовности(Команда='Альфа')]))
        self.callback = None
        self.error = None

        def fetch(game):
            self.calls.append(game)
            if self.callback:
                self.callback()
            if self.error:
                raise self.error
            return self.panel

        self.env, self.clock, self.timers = load_form('ПультВедущего', SimpleNamespace(ПолучитьПульт=fetch))
        self.env['ПослеСоздания']()
        self.env['Игра'] = 'game-1'

    def tick(self, seconds=0):
        self.clock.now += Decimal(seconds)
        self.env['АвтоматическоеОбновление']()

    def test_summary_uses_enum_presentations_instead_of_code_names(self):
        self.panel.Фаза = 'СтартоваяПродажа'
        self.tick()
        self.assertIn('Идёт · Стартовая продажа · раунд', self.env['Сводка'])
        self.assertNotIn('СтартоваяПродажа', self.env['Сводка'])

    def test_timer_registration_hidden_form_and_poll_frequency(self):
        self.assertEqual(len(self.timers), 1)
        self.assertEqual(self.timers[0][1], 1)
        self.env['Открыта'] = False
        self.tick(1)
        self.assertEqual(self.calls, [])
        self.env['Открыта'] = True
        self.tick()
        self.tick(1)
        self.assertEqual(self.calls, ['game-1'])
        self.assertIn('осталось 1:04', self.env['Сводка'])
        self.tick(4)
        self.assertEqual(self.calls, ['game-1', 'game-1'])

    def test_refresh_preserves_edits_and_action_message(self):
        self.tick()
        self.env['Предложения'] = ['edited offer']
        self.env['Сообщение'] = 'Тестовый снимок принят'
        self.panel.Участники[0].Готов = True
        self.tick(5)
        self.assertEqual(self.env['Предложения'], ['edited offer'])
        self.assertEqual(self.env['Сообщение'], 'Тестовый снимок принят')
        self.assertIn('готовы 1 из 1', self.env['Сводка'])

    def test_new_phase_discards_old_proposals_and_updates_command_token(self):
        self.tick()
        self.env['Предложения'] = ['old phase offer']
        self.panel.Токен = 'phase-2'
        self.panel.Версия = Decimal(2)
        self.tick(5)
        self.assertEqual(self.env['Предложения'], [])
        self.assertEqual((self.env['Токен'], self.env['Версия']), ('phase-2', 2))

    def test_connection_failure_clears_visible_state_and_retries(self):
        self.tick()
        self.env['Предложения'] = ['edited offer']
        self.env['Сообщение'] = 'Снимок принят'
        self.error = ServiceError()
        self.tick(5)
        self.assertIsNone(self.env['Состояние'])
        self.assertEqual(self.env['Участники'], [])
        self.assertEqual(self.env['ПредупреждениеВремени'], '')
        self.assertFalse(self.env['ОбновлениеВыполняется'])
        self.assertIn('Нет связи', self.env['СостояниеОбновления'])
        self.error = None
        self.tick(4)
        self.assertEqual(len(self.calls), 2)
        self.tick(1)
        self.assertEqual(len(self.calls), 3)
        self.assertIsNotNone(self.env['Состояние'])
        self.assertEqual(self.env['Предложения'], ['edited offer'])
        self.assertEqual(self.env['Сообщение'], 'Снимок принят')

    def test_response_for_previous_game_is_discarded(self):
        def switch():
            self.env['Игра'] = 'game-2'
            self.env['ИграПриИзменении'](None, None)
        self.callback = switch
        self.tick()
        self.assertIsNone(self.env['Состояние'])
        self.assertEqual(self.env['Токен'], '')
        self.assertFalse(self.env['ОбновлениеВыполняется'])
        self.callback = None
        self.panel.Игра = 'game-2'
        self.tick(1)
        self.assertEqual(self.env['Состояние'].Игра, 'game-2')

    def test_failure_for_previous_game_does_not_delay_new_game(self):
        self.callback = lambda: (self.env.update(Игра='game-2'), self.env['ИграПриИзменении'](None, None))
        self.error = ServiceError()
        self.tick()
        self.assertEqual(self.env['СостояниеОбновления'], '')
        self.assertIsNone(self.env['МоментСледующегоОбновления'])

    def test_countdown_warns_and_expires_between_server_polls(self):
        self.panel.СекундОсталось = Decimal(2)
        self.tick()
        self.assertIn('не более минуты', self.env['ПредупреждениеВремени'])
        self.tick(2)
        self.assertIn('Время фазы истекло', self.env['ПредупреждениеВремени'])
        self.assertIn('осталось 0:00', self.env['Сводка'])
        self.assertEqual(self.calls, ['game-1'])

    def test_paused_console_freezes_timer_and_hides_warning(self):
        self.panel.Статус = 'Пауза'
        self.panel.СекундОсталось = Decimal(20)
        self.tick()
        self.tick(3)
        self.assertIn('таймер на паузе: 0:20', self.env['Сводка'])
        self.assertEqual(self.env['ПредупреждениеВремени'], '')

    def test_manual_refresh_failure_clears_stale_countdown(self):
        self.tick()
        self.error = ServiceError()
        self.env['Обновить'](None)
        self.assertIsNone(self.env['Состояние'])
        self.assertEqual(self.env['Сообщение'], 'Нет связи')
        self.assertEqual(self.env['ПредупреждениеВремени'], '')


class DashboardRefreshTests(unittest.TestCase):
    def test_dashboard_recovers_from_failure_and_skips_closed_form(self):
        calls = []
        response = xbsl.SUMMARIES.СформироватьСводку(xbsl.XArray([
            xbsl.SUMMARIES.СостояниеПульта(
                Игра='game-1', Наименование='Игра 1', Статус='Идет', Фаза='Продажи',
                Участники=xbsl.XArray([xbsl.SUMMARIES.СтрокаГотовности(Команда='Альфа')]))]))
        service = SimpleNamespace(error=None)

        def fetch():
            calls.append(True)
            if service.error:
                raise service.error
            return response

        service.ПолучитьГлавнуюСтраницу = fetch
        env, _, timers = load_form('ГлавнаяСтраница', service)
        env['ПослеСоздания']()
        self.assertEqual(len(timers), 1)
        self.assertEqual(timers[0][1], 15)
        self.assertEqual(len(env['ТекущиеИгры']), 1)
        self.assertEqual(env['ПроблемныеКоманды'][0].Команда, 'Альфа')
        env['Открыта'] = False
        env['АвтоматическоеОбновление']()
        self.assertEqual(len(calls), 1)
        env['Открыта'] = True
        service.error = ServiceError()
        env['АвтоматическоеОбновление']()
        self.assertEqual(env['ТекущиеИгры'], [])
        self.assertEqual(env['ПроблемныеКоманды'], [])
        self.assertIn('Нет связи', env['СостояниеОбновления'])
        self.assertFalse(env['ОбновлениеВыполняется'])
        service.error = None
        env['АвтоматическоеОбновление']()
        self.assertEqual(len(env['ТекущиеИгры']), 1)
        self.assertIn('Обновлено', env['СостояниеОбновления'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
