"""Run original XBSL posting control flow against a stateful transport.

The transport type is substituted for КлиентHttp; the posting method body is
unchanged. Real HTTP verification is recorded in docs/market-erp.md.
"""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from test_erp_protocol import BASE


class NativePostingTests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get('ELEMENT_SCRIPT_HOME'), 'Native Element Script runtime not configured')
    def test_posting_recovery_and_erp_refusal(self):
        protocol = (BASE / 'ПротоколERP.xbsl').read_text().split('@ВПроекте\nметод Ответ(')[0]
        module = (BASE / 'НастройкаБазOData.xbsl').read_text()
        method = module[module.index('метод ПровестиДокумент('):].split('\n@')[0]
        method = method.replace('Соединение: КлиентHttp', 'Соединение: Соответствие<Строка, Объект?>')
        source = (protocol + '\n' + (BASE / 'КонтрактERP.xbsl').read_text() + '\n' + method)
        source = source.replace('импорт Данные\n', '').replace('@ВПроекте', '@Глобально').replace('ПротоколERP.', '').replace('КонтрактERP.', '')
        source += '''
метод ЗапросOData(Состояние: Соответствие<Строка, Объект?>, Метод: Строка, Путь: Строка,
    Параметры: Соответствие<Строка, Объект?> = {:}, Тело: Объект? = Неопределено): Соответствие<Строка, Объект?>
    если Метод == "POST"
        Требовать(Путь == "Document_Test(guid'key')/Post()" и Тело == Неопределено,
            "TEST", "Неверный action или непустое тело")
        Состояние.Вставить("posts", ЧислоJson(Состояние, "posts") + 1)
        если Состояние.ПолучитьИлиНеопределено("refused") != Истина
            Состояние.Вставить("Posted", Истина)
        ;
        если Состояние.ПолучитьИлиНеопределено("lost") == Истина
            выбросить новый Ошибка("Ответ потерян после проведения", 502, "ODATA_HTTP")
        ;
    иначе
        Требовать(Метод == "GET" и Путь == "Document_Test(guid'key')", "TEST", "Неожиданный запрос")
    ;
    возврат Состояние
;

метод Скрипт(): Строка
    знч Черновик = <Строка, Объект?>{"Posted": Ложь, "DeletionMark": Ложь, "posts": 0,
        "Ref_Key": "key", "Number": "1", "Date": "2027-01-15T12:00:00"}
    знч Путь = "Document_Test(guid'key')"
    знч Результат = ПровестиДокумент(Черновик, Путь)
    Требовать(Результат.Получить("posted") == Истина и Результат.Получить("alreadyPosted") == Ложь,
        "TEST", "Черновик не проведён")
    Требовать(ПровестиДокумент(Черновик, Путь).Получить("alreadyPosted") == Истина
        и ЧислоJson(Черновик, "posts") == 1, "TEST", "Повторно проведён уже проведённый документ")
    Черновик.Вставить("DeletionMark", Истина)
    пер Отказ = Ложь
    попытка
        ПровестиДокумент(Черновик, Путь)
    поймать ОшибкаERP: Ошибка
        Отказ = Истина
    ;
    Требовать(Отказ и ЧислоJson(Черновик, "posts") == 1, "TEST", "Удаляемый документ проведён")
    Черновик.Вставить("DeletionMark", Ложь)
    Черновик.Вставить("Posted", Ложь)
    Черновик.Вставить("refused", Истина)
    Отказ = Ложь
    попытка
        ПровестиДокумент(Черновик, Путь)
    поймать ОшибкаERP: Ошибка
        Отказ = Истина
    ;
    Требовать(Отказ и Черновик.Получить("Posted") == Ложь, "TEST", "Нет проверки Posted после отказа ERP")
    Черновик.Вставить("refused", Ложь)
    Черновик.Вставить("lost", Истина)
    Отказ = Ложь
    попытка
        ПровестиДокумент(Черновик, Путь)
    поймать ОшибкаERP: Ошибка
        Отказ = Истина
    ;
    Требовать(Отказ и Черновик.Получить("Posted") == Истина, "TEST", "Ответ не потерян после записи")
    знч ЧислоВызовов = ЧислоJson(Черновик, "posts")
    Требовать(ПровестиДокумент(Черновик, Путь).Получить("alreadyPosted") == Истина
        и ЧислоJson(Черновик, "posts") == ЧислоВызовов, "TEST", "Повтор после потери ответа создаёт движения снова")
    возврат "PASS: posting, deletion, refusal and lost response recovery"
;
'''
        home = Path(os.environ['ELEMENT_SCRIPT_HOME'])
        with tempfile.TemporaryDirectory(prefix='odata-posting-native-') as temp:
            script = Path(temp) / 'Post.sbsl'
            script.write_text(source)
            args = [os.environ.get('ELEMENT_SCRIPT_JAVA', 'java'), '--add-opens', 'java.base/java.lang=ALL-UNNAMED',
                    '--add-opens', 'java.base/java.nio=ALL-UNNAMED', f'-Dlogs.root={temp}/logs',
                    f'-Dexecutor.location={home}', f'-Dlogback.configurationFile={home}/config/logback.xml',
                    '-cp', f'{home}/lib/*', 'com.e1c.g5rt.executor.boot.ExecutorBootstrap', '-c', '10.0', str(script)]
            result = subprocess.run(args, capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('PASS: posting, deletion, refusal and lost response recovery', result.stdout)


if __name__ == '__main__':
    unittest.main()
