#!/usr/bin/env python3
"""Generate the XBSL literal from the sole checked-in OData setup profile."""
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def source():
    profile=json.loads((ROOT/'contracts/erp-odata-setup.json').read_text())
    literal=json.dumps(profile,ensure_ascii=False,separators=(',',':')).replace('\\','\\\\').replace('"','\\"').replace('$','\\$').replace('%','\\%')
    return '@ВПроекте\nметод Получить(): Строка\n    возврат "'+literal+'"\n;\n'
if __name__=='__main__':
    (ROOT/'dimkashelk/Игра/Обмен/ПрофильНастройкиOData.xbsl').write_text(source())
