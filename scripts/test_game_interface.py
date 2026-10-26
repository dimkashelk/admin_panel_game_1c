"""Cross-file checks for the game entry points and error notification coverage."""
from pathlib import Path
import re
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[1] / 'dimkashelk/Игра'


def nodes(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from nodes(child)
    elif isinstance(value, list):
        for child in value:
            yield from nodes(child)


class GameInterfaceTests(unittest.TestCase):
    def test_single_game_entry(self):
        app = yaml.safe_load((ROOT / 'Управление/Приложение.yaml').read_text())
        routes = [n.get('ТипФормы') for n in nodes(app) if 'ТипФормы' in n]
        self.assertEqual(routes.count('ИгрыФормаСписка'), 1)
        self.assertNotIn('МастерНовойИгры', routes)
        self.assertNotIn('НастройкаИгры', routes)
        self.assertNotIn('НастройкаИнформационныхБаз', routes)
        self.assertFalse((ROOT / 'Управление/МастерНовойИгры.yaml').exists())

    def test_new_and_existing_game_share_card(self):
        form = yaml.safe_load((ROOT / 'Управление/НастройкаИгры.yaml').read_text())
        controls = {n['Имя']: n for n in nodes(form['Наследует']) if 'Имя' in n}
        self.assertIn('НовыеКоманды', controls)
        self.assertIn('Команды', controls)
        self.assertIn('ДонастраиватьБазыERP', controls)
        self.assertNotIn('СозданнаяИгра', controls)  # no second game selector inside a card
        self.assertEqual(form['Наследует']['ОсновнаяКоманда']['Обработчик'], 'СохранитьИгру')
        source = (ROOT / 'Управление/НастройкаИгры.xbsl').read_text()
        self.assertNotIn('новый НастройкаИгры()', source)
        self.assertIn('СозданнаяИгра = УправлениеИгрой.СоздатьИгру(', source)

    def test_all_handlers_and_component_references_resolve(self):
        for name in ('НастройкаИгры', 'ИгрыФормаСписка', 'ГлавнаяСтраница'):
            path = next(ROOT.rglob(name + '.yaml'))
            form = yaml.safe_load(path.read_text())
            source = path.with_suffix('.xbsl').read_text()
            controls = {n['Имя'] for n in nodes(form) if 'Имя' in n}
            for node in nodes(form):
                for key in ('Обработчик', 'ПриНажатииСтроки', 'ПриРедактированииСтроки', 'ПриИзменении'):
                    if key in node:
                        self.assertIn('метод ' + node[key] + '(', source)
                for value in node.values():
                    if isinstance(value, str):
                        for control in re.findall(r'Компоненты\.(\w+)', value):
                            self.assertIn(control, controls)

    def test_property_declarations_have_no_control_settings(self):
        form = yaml.safe_load((ROOT / 'Управление/НастройкаИгры.yaml').read_text())
        for prop in form.get('Свойства', []):
            self.assertNotIn('Доступность', prop, prop['Имя'])
            self.assertNotIn('Видимость', prop, prop['Имя'])

    def test_list_editor_subsystem_dependency_is_declared(self):
        subsystem = yaml.safe_load((ROOT / 'Данные/Подсистема.yaml').read_text())
        self.assertIn('Управление', subsystem.get('Использование', []))

    def test_client_catches_notify_or_propagate(self):
        count = 0
        for path in ROOT.rglob('*.yaml'):
            form = yaml.safe_load(path.read_text())
            source = path.with_suffix('.xbsl')
            if form.get('ВидЭлемента') != 'КомпонентИнтерфейса' or not source.exists():
                continue
            lines = source.read_text().splitlines()
            self.assertNotIn('ОкноОшибки', source.read_text(), source.name)
            for index, line in enumerate(lines):
                if 'поймать ' not in line:
                    continue
                indent = len(line) - len(line.lstrip())
                block = []
                for following in lines[index + 1:]:
                    if following.strip() and len(following) - len(following.lstrip()) <= indent:
                        break
                    block.append(following)
                text = '\n'.join(block)
                self.assertTrue(any(item in text for item in (
                    'Сообщить(', 'СообщитьОшибкуОбновления(', 'выбросить Ошибка')),
                    f'{source.name}:{index + 1}: swallowed error')
                count += 1
        self.assertGreater(count, 60)


if __name__ == '__main__':
    unittest.main()
