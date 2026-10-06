#!/usr/bin/env python3
"""Execute the pure XBSL modules in a deliberately limited local harness.

This translates only the language subset used by the pure market, ratings and finance validation modules.
It does NOT compile Element code, emulate its DB/UI/permissions, or verify its
numeric implementation. Unsupported syntax fails instead of being skipped.
"""
from __future__ import annotations
import ast
from decimal import Decimal, localcontext, ROUND_DOWN, ROUND_HALF_UP
from functools import cmp_to_key
from pathlib import Path
import random
import re
from types import SimpleNamespace
import unittest
import yaml

ROOT = Path(__file__).resolve().parents[1] / 'dimkashelk' / 'Игра'


class XArray(list):
    def __getitem__(self, key):
        return super().__getitem__(int(key) if isinstance(key, Decimal) else key)

    def __setitem__(self, key, value):
        return super().__setitem__(int(key) if isinstance(key, Decimal) else key, value)


def call(receiver, method, *args):
    if method == 'Округлить':
        precision = int(args[0]) if args else 0
        mode = args[1] if len(args) > 1 else ROUND_HALF_UP
        return Decimal(receiver).quantize(Decimal(1).scaleb(-precision), rounding=mode)
    if method == 'Сравнить':
        return Decimal((receiver > args[0]) - (receiver < args[0]))
    if method in ('Размер', 'Длина'):
        return Decimal(len(receiver))
    if method == 'Пусто':
        return not receiver
    if method == 'Содержит':
        return args[0] in receiver
    if method == 'Добавить':
        receiver.append(args[0])
        return None
    if method == 'Удалить':
        receiver.remove(args[0])
        return None
    if method == 'Сортировать':
        return XArray(sorted(receiver, key=cmp_to_key(args[0])))
    return getattr(receiver, method)(*args)


def ternary(text):
    """Convert a top-level ternary; the source subset has no nested ternaries."""
    depth = 0
    quoted = False
    question = None
    for index, char in enumerate(text):
        if char == '"' and (index == 0 or text[index - 1] != '\\'):
            quoted = not quoted
        if quoted:
            continue
        if char in '([':
            depth += 1
        if char in ')]':
            depth -= 1
        if depth == 0 and char == '?' and question is None:
            question = index
        elif depth == 0 and char == ':' and question is not None:
            return f'({text[question + 1:index]} if {text[:question]} else {text[index + 1:]})'
    return text


class Expressions(ast.NodeTransformer):
    def visit_Subscript(self, node):
        node = self.generic_visit(node)
        if isinstance(node.ctx, ast.Load):
            return ast.Call(ast.Name('index_value', ast.Load()), [node.value, node.slice], [])
        return node

    def visit_Constant(self, node):
        if type(node.value) in (int, float):
            return ast.Call(ast.Name('Decimal', ast.Load()), [ast.Constant(str(node.value))], [])
        return node

    def visit_List(self, node):
        node = self.generic_visit(node)
        return ast.Call(ast.Name('XArray', ast.Load()), [node], [])

    def visit_Call(self, node):
        node = self.generic_visit(node)
        if isinstance(node.func, ast.Attribute):
            return ast.Call(ast.Name('call', ast.Load()),
                            [node.func.value, ast.Constant(node.func.attr), *node.args], node.keywords)
        return node


def expression(text):
    text = text.strip()
    text = re.sub(r'<[^<>]+>\[\]', '[]', text)
    text = re.sub(r'\bновый\s+', '', text)
    text = re.sub(r'([\w\]\)])!(?!=)', r'\1', text)
    text = re.sub(r'!(?!=)', ' not ', text)
    for old, new in [('&&', ' and '), ('||', ' or '), ('Истина', 'True'), ('Ложь', 'False'), ('Неопределено', 'None')]:
        text = text.replace(old, new)
    text = re.sub(r'\bи\b', 'and', text)
    text = re.sub(r'\bили\b', 'or', text)
    text = re.sub(r'\bне\b', 'not', text)
    text = ternary(text)
    text = re.sub(r'\(([^()]+)\)\s*->\s*(.+)', r'(lambda \1: \2)', text)
    tree = ast.parse(text.strip(), mode='eval')
    tree = Expressions().visit(tree)
    ast.fix_missing_locations(tree)
    return ast.unparse(tree.body)


def logical_lines(source):
    pending = ''
    depth = 0
    indent = 0
    for line in source.splitlines():
        line = re.sub(r'//.*$', '', line)
        if not line.strip() or line.lstrip().startswith(('импорт ', '@')):
            continue
        if not pending:
            indent = len(line) - len(line.lstrip())
        pending += ' ' + line.strip()
        # Ignore quoted content when checking multiline parentheses.
        code = re.sub(r'"(?:[^"\\]|\\.)*"', '""', line)
        depth += code.count('(') + code.count('[') - code.count(')') - code.count(']')
        if depth == 0:
            yield indent, pending.strip()
            pending = ''
    if pending or depth:
        raise SyntaxError('Unbalanced source expression')


def translate(path):
    output = []
    for indent, line in logical_lines(path.read_text()):
        if line == ';':
            continue
        if line.startswith('метод '):
            match = re.fullmatch(r'метод (\w+)\((.*)\)(?:: .+)?', line)
            if not match:
                raise SyntaxError(line)
            name, params = match.groups()
            args = ', '.join(p.split(':')[0].strip() for p in params.split(',') if p.strip())
            statement = f'def {name}({args}):'
        elif line.startswith('иначе если '):
            statement = 'elif ' + expression(line[len('иначе если '):]) + ':'
        elif line == 'иначе':
            statement = 'else:'
        elif line.startswith('если '):
            statement = 'if ' + expression(line[5:]) + ':'
        elif line.startswith('пока '):
            statement = 'while ' + expression(line[5:]) + ':'
        elif line.startswith('для '):
            name, value = line[4:].split(' из ', 1)
            statement = f'for {name} in {expression(value)}:'
        elif line == 'возврат':
            statement = 'return'
        elif line.startswith('возврат '):
            statement = 'return ' + expression(line[8:])
        elif line.startswith('выбросить '):
            statement = 'raise ' + expression(line[10:])
        elif line == 'продолжить':
            statement = 'continue'
        elif line == 'прервать':
            statement = 'break'
        else:
            line = re.sub(r'^(знч|пер)\s+', '', line)
            match = re.match(r'^(\w+)(?:\s*:\s*[^=]+)?\s*=\s*(?!=)(.*)$', line)
            if match:
                statement = match[1] + ' = ' + expression(match[2])
            else:
                match = re.match(r'^([\w.!\[\]]+)\s*=\s*(?!=)(.*)$', line)
                if match:
                    statement = expression(match[1]) + ' = ' + expression(match[2])
                else:
                    statement = expression(line)
        output.append(' ' * indent + statement)
    return '\n'.join(output)


def make_dto(fields):
    def construct(**values):
        defaults = {}
        for item in fields:
            kind = item['Тип']
            defaults[item['Имя']] = (None if kind.endswith('?') else
                XArray() if kind.startswith('Массив<') else
                Decimal(0) if kind == 'Число' else False if kind == 'Булево' else '')
        defaults.update(values)
        return SimpleNamespace(**defaults)
    return construct


def load_module(relative, modules):
    env = {'Decimal': Decimal, 'XArray': XArray, 'call': call,
           'index_value': lambda value, index: value[int(index) if isinstance(index, Decimal) else index],
           'РежимОкругления': SimpleNamespace(Вниз=ROUND_DOWN, ПоловинаВверх=ROUND_HALF_UP),
           'Символы': SimpleNamespace(ПолучитьКод=lambda text: Decimal(ord(text))),
           'ИсключениеНедопустимыйАргумент': ValueError, **modules}
    for path in (ROOT / 'Данные').glob('*.yaml'):
        obj = yaml.safe_load(path.read_text())
        if obj.get('ВидЭлемента') == 'Структура':
            env[obj['Имя']] = make_dto(obj['Поля'])
        if obj.get('ВидЭлемента') == 'Перечисление':
            env[obj['Имя']] = SimpleNamespace(**{v['Имя']: v['Имя'] for v in obj['Элементы']})
    code = translate(ROOT / relative)
    exec(compile(code, str(ROOT / relative), 'exec'), env)
    return SimpleNamespace(**env)


CHECKS = load_module('Данные/ПроверкиДанных.xbsl', {})
MARKET = load_module('Рынок/РасчетРынка.xbsl', {'ПроверкиДанных': CHECKS})
RATINGS = load_module('Аналитика/Рейтинг.xbsl', {'ПроверкиДанных': CHECKS})
SELFTEST = load_module('Рынок/ПроверкиРынка.xbsl', {'ПроверкиДанных': CHECKS, 'РасчетРынка': MARKET})


def params(**changes):
    values = dict(КодПродукта='CityRide', БазовыйСпрос=Decimal(12), РеферентнаяЦена=Decimal(10),
        МинимальнаяЦена=Decimal(1), МаксимальнаяЦена=Decimal(100), Эластичность=Decimal(0),
        ЧувствительностьКЦене=Decimal(1), Тренд=Decimal(1))
    values.update(changes)
    return MARKET.ПараметрыПродукта(**values)


def offer(name, price, qty, active=True):
    return MARKET.ПредложениеРынка(Участник=name, Цена=Decimal(price), Доступно=Decimal(qty), Активен=active)


def calculate(offers, algorithm='ЦеновыеДоли', rules=None, seed=42, round_number=0):
    with localcontext() as context:
        context.prec = 38
        return MARKET.Рассчитать(rules or params(), XArray(offers), algorithm, Decimal(seed), Decimal(round_number))


class PureLogicTests(unittest.TestCase):
    def test_embedded_xbsl_selftest(self):
        self.assertIn('пройдены', SELFTEST.Выполнить())

    def test_water_filling_and_largest_remainders(self):
        result = calculate([offer('A', 10, 1), offer('B', 10, 10), offer('C', 10, 10)])
        self.assertEqual([r.Заказано for r in result.Заказы][0], 1)
        self.assertEqual(sorted(r.Заказано for r in result.Заказы[1:]), [5, 6])

    def test_equal_price_ranking_uses_stock(self):
        result = calculate([offer('A', 10, 1), offer('B', 10, 10), offer('C', 10, 10)], 'СначалаДешевые')
        self.assertEqual(sorted(r.Заказано for r in result.Заказы), [0, 6, 6])

    def test_price_algorithms_are_distinct(self):
        offers = [offer('A', 10, 100), offer('B', 20, 100)]
        self.assertEqual([r.Заказано for r in calculate(offers).Заказы], [8, 4])
        self.assertEqual([r.Заказано for r in calculate(offers, 'СначалаДешевые').Заказы], [12, 0])

    def test_zero_stock_and_inactive_do_not_move_demand(self):
        offers = [offer('A', 10, 30), offer('B', 1, 0), offer('C', 1, 100, False)]
        result = calculate(offers, rules=params(Эластичность=Decimal(1)))
        self.assertEqual(result.СредняяЦена, 10)
        self.assertEqual(result.Спрос, 12)
        self.assertEqual(len(result.Заказы), 1)

    def test_zero_trend_and_empty_market(self):
        self.assertEqual(calculate([offer('A', 10, 100)], rules=params(Тренд=Decimal(0))).Заказы[0].Заказано, 0)
        result = calculate([])
        self.assertEqual(result.НепокрытыйСпрос, 12)
        self.assertEqual(result.Заказы, [])

    def test_rejects_invalid_input(self):
        for offers in ([offer('A', 0, 10)], [offer('A', 10, -1)],
                       [offer('A', 10, '0.5')], [offer('A', 10, 1), offer('A', 10, 2)],
                       [offer('A', '10.001', 1)], [offer('A', 101, 1)]):
            with self.subTest(offers=offers), self.assertRaises(ValueError):
                calculate(offers)

    def test_randomized_conservation_and_permutation(self):
        rng = random.Random(42)
        for _ in range(300):
            offers = [offer(str(i), rng.randint(1, 100), rng.randint(0, 300)) for i in range(rng.randint(1, 10))]
            rules = params(БазовыйСпрос=Decimal(rng.randint(0, 1000)),
                           Эластичность=Decimal('1.25'), ЧувствительностьКЦене=Decimal('1.35'))
            for algorithm in ('ЦеновыеДоли', 'СначалаДешевые'):
                result = calculate(offers, algorithm, rules)
                ordered = [(r.Участник, r.Заказано) for r in result.Заказы]
                self.assertEqual(sum(r.Заказано for r in result.Заказы), min(result.Спрос, result.Доступность))
                self.assertTrue(all(0 <= r.Заказано <= r.Доступно and r.Заказано == int(r.Заказано) for r in result.Заказы))
                rng.shuffle(offers)
                repeated = calculate(offers, algorithm, rules)
                self.assertEqual(ordered, [(r.Участник, r.Заказано) for r in repeated.Заказы])

    def test_input_is_not_mutated(self):
        offers = [offer('A', 10, 50), offer('B', 20, 100)]
        before = [vars(o).copy() for o in offers]
        calculate(offers)
        self.assertEqual(before, [vars(o) for o in offers])

    def test_competition_ranking_and_incomplete_results(self):
        rows = XArray([RATINGS.СтрокаРейтинга(Команда=name, Деньги=Decimal(money), Подтвержден=confirmed)
                      for name, money, confirmed in [('C', -5, True), ('B', 10, True), ('A', 10, True), ('D', 100, False)]])
        result = RATINGS.РасставитьМеста(rows)
        self.assertEqual([(r.Команда, r.Место) for r in result], [('A', 1), ('B', 1), ('C', 3), ('D', 0)])
        self.assertTrue(all(r.Место == 0 for r in rows))
        self.assertIsNone(result[-1].Деньги)
        self.assertEqual(rows[-1].Деньги, 100)

    def test_confirmed_rating_requires_cash(self):
        with self.assertRaises(ValueError):
            RATINGS.РасставитьМеста(XArray([RATINGS.СтрокаРейтинга(Команда='A', Подтвержден=True)]))

    def test_partial_and_multiple_payments(self):
        fact = self.sale(payments=[self.payment('P1', 30), self.payment('P2', 20)])
        self.assertEqual(CHECKS.ПроверитьСостояниеПродажи(fact), 50)
        with self.assertRaises(ValueError):
            CHECKS.ПроверитьСостояниеПродажи(self.sale(qty=4, payments=fact.Платежи))
        with self.assertRaises(ValueError):
            CHECKS.ПроверитьСостояниеПродажи(self.sale(payments=[self.payment('P1', 20), self.payment('P1', 20)]))

    def test_payment_repetition_and_new_versions(self):
        previous = self.sale(payments=[self.payment('P1', 20)])
        self.assertTrue(CHECKS.ПроверитьПовторПродажи(self.sale(payments=[self.payment('P1', 20)]), previous))
        self.assertFalse(CHECKS.ПроверитьПовторПродажи(self.sale(version=2,
            payments=[self.payment('P1', 20), self.payment('P2', 30)]), previous))
        for current in [self.sale(version=0), self.sale(qty=4, payments=previous.Платежи),
                        self.sale(payments=[self.payment('P1', 21)]),
                        self.sale(version=2, payments=[]),
                        self.sale(payments=[self.payment('P1', 20, version=2)]),
                        self.sale(payments=[self.payment('P1', 20), self.payment('P2', 30)]),
                        self.sale(payments=[self.payment('P1', 20, moment='changed')])]:
            with self.subTest(current=current), self.assertRaises(ValueError):
                CHECKS.ПроверитьПовторПродажи(current, previous)

    def test_payment_correction_requires_new_version(self):
        previous = self.sale(version=2, payments=[self.payment('P1', 20, version=2)])
        self.assertFalse(CHECKS.ПроверитьПовторПродажи(self.sale(version=3,
            payments=[self.payment('P1', 25, version=3)]), previous))
        with self.assertRaises(ValueError):
            CHECKS.ПроверитьПовторПродажи(self.sale(version=3,
                payments=[self.payment('P1', 25, version=1)]), previous)

    @staticmethod
    def payment(key, amount, version=1, moment='2026-10-06T12:00:00Z'):
        return CHECKS.ФактПлатежа(ИдПлатежа=key, Сумма=Decimal(amount), Версия=Decimal(version), Момент=moment)

    @staticmethod
    def sale(qty=5, version=1, payments=()):
        return CHECKS.ФактПродажи(ИдРеализации='invoice-1', Отгружено=Decimal(qty), Цена=Decimal(10),
            Версия=Decimal(version), Платежи=XArray(payments))


if __name__ == '__main__':
    unittest.main(verbosity=2)
