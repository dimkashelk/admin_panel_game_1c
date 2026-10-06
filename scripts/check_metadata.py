#!/usr/bin/env python3
"""Check links and invariants across Element metadata without changing inputs.

This complements xbsl-validate; it does not compile XBSL or test the runtime.
"""

import argparse
from collections import Counter
from pathlib import Path
import sys
import uuid
import re

import yaml


class UniqueKeyLoader(yaml.SafeLoader):
    pass


def unique_mapping(loader, node, deep=False):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in result:
            raise ValueError(f"Duplicate YAML key: {key}")
        result[key] = loader.construct_object(value_node, deep=deep)
    return result


UniqueKeyLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, unique_mapping
)

PRIMITIVES = {"Строка", "Число", "Булево", "Дата", "ДатаВремя", "Момент", "Ууид"}
SYSTEM_REFERENCES = {"Пользователи.Ссылка", "ДвоичныйОбъект.Ссылка"}
COLLECTIONS = (
    "Реквизиты", "Измерения", "Ресурсы", "ТабличныеЧасти", "Элементы",
    "ПространстваБлокировок", "Индексы",
)


def mappings(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from mappings(child)
    elif isinstance(value, list):
        for child in value:
            yield from mappings(child)


def check(root):
    errors = []
    files = {}
    objects = {}
    subsystems = {}
    ids = {}

    def fail(path, message):
        errors.append(f"{path.relative_to(root)}: {message}")

    for path in sorted(root.rglob("*.yaml")):
        try:
            value = yaml.load(path.read_text(encoding="utf-8"), Loader=UniqueKeyLoader)
            if not isinstance(value, dict):
                raise ValueError("Expected YAML mapping")
        except (ValueError, yaml.YAMLError) as error:
            fail(path, str(error))
            continue
        files[path] = value
        if path.name == "Подсистема.yaml":
            subsystems[path.parent.name] = (path, value)
        if "ВидЭлемента" in value:
            name = value.get("Имя")
            if name in objects:
                fail(path, f"Duplicate object name: {name}")
            objects[name] = (path, value)
            if path.stem != name:
                fail(path, f"Filename differs from object name: {name}")
        for node in mappings(value):
            if "Ид" in node:
                identifier = node["Ид"]
                try:
                    parsed = uuid.UUID(str(identifier))
                    if path.name != "Проект.yaml" and parsed.version != 4:
                        fail(path, f"New metadata requires UUID v4: {identifier}")
                except (ValueError, AttributeError):
                    fail(path, f"Invalid UUID: {identifier}")
                if identifier in ids:
                    fail(path, f"UUID reused from {ids[identifier]}: {identifier}")
                ids[identifier] = path.relative_to(root)
            for section in COLLECTIONS:
                members = node.get(section, [])
                # UI fragments may contain references to commands rather than mappings.
                if not isinstance(members, list):
                    continue
                names = [member.get("Имя") for member in members if isinstance(member, dict)]
                for name, count in Counter(n for n in names if n is not None).items():
                    if count > 1:
                        fail(path, f"Duplicate name in {section}: {name}")

    project = files.get(root / "Проект.yaml")
    if not project:
        errors.append("Проект.yaml: Missing project descriptor")
    elif str(project.get("РежимСовместимости")) != "10.0":
        fail(root / "Проект.yaml", "Expected compatibility mode 10.0")

    graph = {name: value.get("Использование", []) for name, (_, value) in subsystems.items()}
    for name, uses in graph.items():
        for used in uses:
            if used not in graph:
                fail(subsystems[name][0], f"Unknown subsystem: {used}")
    visited, active = set(), set()

    def visit(name):
        if name in active:
            fail(subsystems[name][0], "Cycle in subsystem dependencies")
            return
        if name in visited:
            return
        active.add(name)
        for used in graph.get(name, []):
            if used in graph:
                visit(used)
        active.remove(name)
        visited.add(name)

    for name in graph:
        visit(name)

    for _, (path, obj) in objects.items():
        kind = obj["ВидЭлемента"]
        owner = path.parent.name
        if owner not in subsystems:
            fail(path, f"Object outside a subsystem: {owner}")
        if kind == "КомпонентИнтерфейса":
            # UI type grammar is owned by xbsl-form-add, not the metadata type checker.
            continue
        if kind in ("Справочник", "Документ"):
            system_names = {"Наименование", "Код"} if kind == "Справочник" else {"Дата", "Номер"}
            attributes = obj.get("Реквизиты", [])
            for attribute in attributes:
                if attribute["Имя"] in system_names:
                    if "Ид" in attribute:
                        fail(path, f"System field has UUID: {attribute['Имя']}")
                elif "Ид" not in attribute:
                    fail(path, f"Attribute has no UUID: {attribute['Имя']}")
            if kind == "Документ":
                names = {attribute["Имя"] for attribute in attributes}
                if not {"Дата", "Номер"} <= names:
                    fail(path, "Document requires Дата and Номер")
            for index in obj.get("Индексы", []) + obj.get("ПространстваБлокировок", []):
                declared = {attribute["Имя"] for attribute in attributes}
                if not set(index["Поля"]) <= declared:
                    fail(path, f"Unknown field in index/lock: {index['Имя']}")
        if kind == "Перечисление":
            if sum(item.get("ПоУмолчанию") == "Истина" for item in obj["Элементы"]) > 1:
                fail(path, "Enumeration has multiple defaults")
        if kind == "РегистрСведений":
            for dimension in obj.get("Измерения", []):
                if ".Ссылка" in dimension.get("Тип", "") and dimension.get("Ведущее") != "Ложь":
                    fail(path, f"Historical dimension permits cascade deletion: {dimension['Имя']}")
        for node in mappings(obj):
            if "Тип" not in node:
                continue
            type_name = node["Тип"]
            if not isinstance(type_name, str):
                fail(path, "Type must be a string")
                continue
            base = type_name.removesuffix("?")
            # DTO fields may contain typed arrays and other documented collections.
            match = re.fullmatch(r"(?:Массив|ЧитаемыйМассив|ЧитаемаяКоллекция)<(.+)>", base)
            if match:
                base = match[1].removesuffix("?")
            if base == "Строка" and node.get("МаксимальнаяДлина", 0) > 1000:
                fail(path, f"String maximum exceeds platform limit 1000: {node.get('Имя')}")
            if base in PRIMITIVES or base in SYSTEM_REFERENCES:
                continue
            is_reference = base.endswith(".Ссылка")
            target_name = base.removesuffix(".Ссылка") if is_reference else base
            if target_name not in objects:
                fail(path, f"Unknown type: {type_name}")
                continue
            target_path, target = objects[target_name]
            if is_reference and target["ВидЭлемента"] not in {"Справочник", "Документ"}:
                fail(path, f"Reference to an object without a link type: {type_name}")
            if not is_reference:
                if target["ВидЭлемента"] not in {"Перечисление", "Структура"}:
                    fail(path, f"Expected enumeration or structure type: {type_name}")
                elif target["ВидЭлемента"] == "Перечисление":
                    default = node.get("ЗначениеПоУмолчанию")
                    # YAML stores the member name; XBSL uses Type.Member syntax.
                    values = {element["Имя"] for element in target["Элементы"]}
                    if kind != "Структура" and not type_name.endswith("?") and default not in values:
                        fail(path, f"Enumeration default missing or invalid: {node.get('Имя')}")
                    if default is not None and default not in values:
                        fail(path, f"Unknown enumeration value: {default}")
            other_owner = target_path.parent.name
            if other_owner != owner:
                if target.get("ОбластьВидимости") != "ВПроекте":
                    fail(path, f"Referenced type is not visible: {target_name}")
                if other_owner not in obj.get("Импорт", []):
                    fail(path, f"Missing Импорт: {other_owner}")
                if other_owner not in graph.get(owner, []):
                    fail(path, f"Missing subsystem Использование: {other_owner}")

        if kind == "ОбщийМодуль" and not path.with_suffix(".xbsl").is_file():
            fail(path, "Common module is missing its XBSL companion")
        if kind in {"Справочник", "Документ"}:
            for view in ("Объект", "Список"):
                form = obj.get("Интерфейс", {}).get(view, {}).get("Форма")
                if form and (form not in objects or objects[form][1]["ВидЭлемента"] != "КомпонентИнтерфейса"):
                    fail(path, f"Unknown interface form: {form}")

    # A game's frozen settings must retain the scenario's structure, with separate IDs.
    if "Игры" in objects and "СценарииИгры" in objects:
        def structure(obj):
            return {table["Имя"]: [(field["Имя"], field["Тип"]) for field in table["Реквизиты"]]
                    for table in obj.get("ТабличныеЧасти", [])}
        if structure(objects["Игры"][1]) != structure(objects["СценарииИгры"][1]):
            fail(objects["Игры"][0], "Frozen settings differ from scenario tables")

    counts = Counter(obj["ВидЭлемента"] for _, obj in objects.values())
    return errors, {"files": len(files), "subsystems": len(subsystems),
                    "objects": dict(counts), "unique_ids": len(ids)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", nargs="?", type=Path,
                        default=Path(__file__).resolve().parents[1] / "dimkashelk" / "Игра")
    root = parser.parse_args().root.resolve()
    if not root.is_dir():
        parser.error(f"Project directory does not exist: {root}")
    errors, summary = check(root)
    for error in errors:
        print(error, file=sys.stderr)
    if errors:
        print(f"Failed: {len(errors)} metadata errors", file=sys.stderr)
        return 1
    print(f"OK: {summary['files']} YAML files, {summary['subsystems']} subsystems, "
          f"{sum(summary['objects'].values())} objects, {summary['unique_ids']} unique IDs")
    for kind, count in sorted(summary["objects"].items()):
        print(f"  {kind}: {count}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
