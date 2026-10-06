#!/usr/bin/env python3
"""Static consistency checks only; this is not an Element compiler.

Check declared handlers, module calls/imports, DTO/table constructor fields,
component/property bindings and basic delimiters. DB and UI APIs need native QA.
"""
from pathlib import Path
import re
import sys
import yaml

ROOT = Path(__file__).resolve().parents[1] / 'dimkashelk' / 'Игра'
ERRORS = []

def fail(path, message):
    ERRORS.append(f'{path.relative_to(ROOT)}: {message}')

def nodes(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from nodes(child)
    elif isinstance(value, list):
        for child in value:
            yield from nodes(child)

def erase_literals(source):
    return re.sub(r'"(?:[^"\\]|\\.)*"|//[^\n]*',
                  lambda m: ' ' * len(m[0]), source)

METADATA = {d['Имя']: (p, d) for p in ROOT.rglob('*.yaml')
            if 'ВидЭлемента' in (d := yaml.safe_load(p.read_text()))}
SOURCES = {p: p.read_text() for p in ROOT.rglob('*.xbsl')}
MODULES = {name: (p.with_suffix('.xbsl'), d)
           for name, (p, d) in METADATA.items() if d['ВидЭлемента'] == 'ОбщийМодуль'}
METHODS = {p: set(re.findall(r'^метод (\w+)\(', s, re.M)) for p, s in SOURCES.items()}

for path, source in SOURCES.items():
    code = erase_literals(source)
    if '&&' in code or '||' in code or re.search(r'(?<![\w)\]])!(?!=)', code):
        fail(path, 'Use XBSL logical operators: и, или, не')
    if path.name.endswith('.Объект.xbsl') and 'Запрос{' in code:
        fail(path, 'Move query literals from nested object modules to entity managers')
    if re.search(r'Запрос\{[^}]*%\w+\.', code, re.S):
        fail(path, 'Bind query property expressions to scalar local parameters')
    stack = []
    for character in code:
        if character in '([{':
            stack.append(character)
        elif character in ')]}':
            if not stack or stack.pop() != {')': '(', ']': '[', '}': '{'}[character]:
                fail(path, 'Unbalanced expression delimiters')
                break
    if stack:
        fail(path, 'Unclosed expression delimiters')
    blocks = []
    for number, line in enumerate(code.splitlines(), 1):
        text = line.strip()
        if re.match(r'^(метод|если|для|пока|попытка)\b', text):
            blocks.append(number)
        elif text == ';':
            if not blocks:
                fail(path, f'Unexpected block terminator at {number}')
            else:
                blocks.pop()
    if blocks:
        fail(path, f'Unclosed blocks: {blocks}')
    if re.search(r'\bTODO\b|\bFIXME\b', source):
        fail(path, 'Unimplemented placeholder')
    if '${' in source:
        fail(path, 'Unexpected interpolation syntax (use %{...})')
    methods = re.findall(r'^метод (\w+)\(', code, re.M)
    if len(methods) != len(set(methods)):
        fail(path, 'Duplicate method declaration')
    imports = set(re.findall(r'^импорт (\w+)', code, re.M))
    for module, method in re.findall(r'\b(\w+)\.(\w+)\(', code):
        if module not in MODULES:
            continue
        module_path, _ = MODULES[module]
        if method not in METHODS.get(module_path, set()):
            fail(path, f'Unknown module method: {module}.{method}')
        if module_path.parent != path.parent and module_path.parent.name not in imports:
            fail(path, f'Missing source import: {module_path.parent.name} ({module}.{method})')
    # Named DTO and table-row constructor fields are declared in YAML.
    for match in re.finditer(r'новый (\w+)(?:\.(\w+))?\(', code):
        name, child = match.groups()
        if name not in METADATA:
            continue
        _, descriptor = METADATA[name]
        if child:
            table = next((t for t in descriptor.get('ТабличныеЧасти', []) if t['Имя'] == child), None)
            if not table:
                continue
            fields = {f['Имя'] for f in table['Реквизиты']}
        elif descriptor['ВидЭлемента'] == 'Структура':
            fields = {f['Имя'] for f in descriptor['Поля']}
        else:
            continue
        depth, end = 1, match.end()
        while end < len(code) and depth:
            depth += (code[end] == '(') - (code[end] == ')')
            end += 1
        args = code[match.end():end - 1]
        depth = 0
        argstart = 0
        for index, character in enumerate(args + ','):
            depth += (character in '([{') - (character in ')]}')
            if character == ',' and depth == 0:
                arg = args[argstart:index].strip()
                argstart = index + 1
                field = re.match(r'(\w+)\s*=(?!=)', arg)
                if field and field[1] not in fields:
                    fail(path, f'Unknown constructor field: {name}.{child or ""}.{field[1]}')

for name, (path, descriptor) in METADATA.items():
    if descriptor['ВидЭлемента'] != 'КомпонентИнтерфейса':
        continue
    form_source = path.with_suffix('.xbsl')
    declared = METHODS.get(form_source, set())
    properties = {p['Имя'] for p in descriptor.get('Свойства', [])}
    components = {n['Имя'] for n in nodes(descriptor.get('Наследует', {})) if 'Имя' in n}
    for node in nodes(descriptor):
        for attribute in ('Обработчик', 'ПослеСоздания', 'ПриНажатии'):
            if attribute in node and node[attribute] not in declared:
                fail(path, f'Missing native handler: {node[attribute]}')
        if 'ТипФормы' in node and node['ТипФормы'] not in METADATA:
            fail(path, f'Unknown navigation form: {node["ТипФормы"]}')
        for value in node.values():
            if not isinstance(value, str):
                continue
            reference = re.match(r'^=Компоненты\.(\w+)', value)
            if reference and reference[1] not in components:
                fail(path, f'Unknown component: {reference[1]}')
            binding = re.fullmatch(r'=(\w+)', value)
            # These are native object/list form properties and commands.
            native = {'Объект', 'ЗаписатьИЗакрыть', 'Записать', 'Закрыть', 'Обновить'}
            if binding and binding[1] not in properties | native:
                fail(path, f'Unknown root binding: {binding[1]}')

if ERRORS:
    print('\n'.join(ERRORS), file=sys.stderr)
    sys.exit(1)
print(f'PASS: {len(SOURCES)} XBSL files; handlers, imports, constructors and bindings are consistent. Native compilation is not verified.')
