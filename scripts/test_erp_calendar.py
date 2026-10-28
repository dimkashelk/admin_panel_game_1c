"""Run production calendar generation and validation in Element Script."""
import argparse
from pathlib import Path
import re
import subprocess
import tempfile
from test_erp_pull import EXCHANGE, method

DRIVER = r'''
метод Скрипт(): Число
    знч Настройки = ОбъектJson(Прочитать(ПолучитьПрофиль()).Получить("gameSettings"))
    для Старт из ["2026-10-28", "2027-01-31", "2028-01-31", "2026-12-31", "2026-10-15"]
        знч Календарь = СформироватьКалендарь(Старт, 4)
        Требовать(ОбъектJson(Календарь[0]).Получить("documentDate") == Старт, "TEST", "Start date changed")
        Настройки.Вставить("calendar", Календарь)
        ПроверитьНастройки(Настройки)
    ;
    знч Обычный = СформироватьКалендарь("2027-01-31", 2)
    Требовать(ОбъектJson(Обычный[1]).Получить("documentDate") == "2027-02-28", "TEST", "February overflow")
    Требовать(ОбъектJson(Обычный[2]).Получить("documentDate") == "2027-03-31", "TEST", "Clamping changed anchor day")
    знч Високосный = СформироватьКалендарь("2028-01-31", 1)
    Требовать(ОбъектJson(Високосный[1]).Получить("documentDate") == "2028-02-29", "TEST", "Leap February")
    Требовать(ДатаДокументовРаунда(2100, 2, 31) == "2100-02-28", "TEST", "Century leap rule")
    Требовать(ДатаДокументовРаунда(2000, 2, 31) == "2000-02-29", "TEST", "400 year leap rule")
    пер ОшибкаПоймана = Ложь
    знч ПлохойКалендарь = СформироватьКалендарь("2026-10-28", 4)
    знч ПлохойРаунд = ОбъектJson(ПлохойКалендарь[1])
    ПлохойРаунд.Вставить("documentDate", "2026-11-15")
    ПлохойКалендарь[1] = ПлохойРаунд
    Настройки.Вставить("calendar", ПлохойКалендарь)
    попытка
        ПроверитьНастройки(Настройки)
    поймать Сбой: Исключение
        ОшибкаПоймана = Истина
    ;
    Требовать(ОшибкаПоймана, "TEST", "Calendar accepted a changed anchor day")
    Консоль.Записать("PASS: production calendar and settings validation, current day, legacy 15th, year rollover, leap years, month end and invalid anchor")
    возврат 0
;
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', type=Path, required=True)
    args = parser.parse_args()
    protocol = (EXCHANGE / 'ПротоколERP.xbsl').read_text().split('@ВПроекте\nметод Ответ(', 1)[0]
    settings = (EXCHANGE / 'НастройкиERP.xbsl').read_text()
    schema = method((EXCHANGE / 'КонтрактERP.xbsl').read_text(), 'Схема')
    profile = method((EXCHANGE / 'ПрофильНастройкиOData.xbsl').read_text(), 'Получить').replace('метод Получить(', 'метод ПолучитьПрофиль(')
    body = '\n'.join([protocol, schema, profile, *(method(settings, n) for n in
        ('ДатаДокументовРаунда', 'СформироватьКалендарь', 'ПроверитьНастройки')), DRIVER])
    body = re.sub(r'^(?:импорт |@)[^\n]*\n', '', body, flags=re.M)
    body = body.replace('ПротоколERP.', '').replace('КонтрактERP.', '')
    with tempfile.TemporaryDirectory(prefix='erp-calendar-') as temp:
        folder = Path(temp)
        script = folder / 'Календарь.sbsl'
        script.write_text(body)
        command = ['java', '-Xmx256m', '--add-opens', 'java.base/java.lang=ALL-UNNAMED',
            '--add-opens', 'java.base/java.nio=ALL-UNNAMED', '-Dfile.encoding=UTF-8',
            f'-Djava.io.tmpdir={folder}', f'-Dlogs.root={folder / "logs"}',
            f'-Dexecutor.location={args.runtime}', f'-Dlogback.configurationFile={args.runtime / "config/logback.xml"}',
            '-cp', str(args.runtime / 'lib/*'), 'com.e1c.g5rt.executor.boot.ExecutorBootstrap',
            '-c', '10.0', str(script)]
        result = subprocess.run(command, cwd=folder, capture_output=True, text=True, timeout=60)
        print(result.stdout)
        if result.returncode:
            print(result.stderr)
            raise SystemExit(result.returncode)


if __name__ == '__main__':
    main()
