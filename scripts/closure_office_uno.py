"""Native document-model QA, run with the pinned LibreOffice bundled Python.

Only synthetic files under reports are accepted. A private pipe/profile belongs
to this process; no existing Office instance or user document is attached.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import time
import uuid

import uno

ROOT = Path(__file__).resolve().parents[1]


def properties(**values):
    result = []
    for name, value in values.items():
        item = uno.createUnoStruct('com.sun.star.beans.PropertyValue')
        item.Name, item.Value = name, value
        result.append(item)
    return tuple(result)


def main():
    parser = argparse.ArgumentParser()
    for name in ('office', 'fixture', 'docx', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    parser.add_argument('--chart', type=Path)
    args = parser.parse_args()
    sources = [args.fixture.resolve(strict=True), args.docx.resolve(strict=True)]
    if args.chart:
        sources.append(args.chart.resolve(strict=True))
    for source in sources:
        source.relative_to(ROOT / 'reports')
        if source.is_symlink():
            raise ValueError('Linked sources refused')
    out = args.output.resolve()
    out.relative_to(ROOT / 'reports')
    out.mkdir(parents=True, exist_ok=False)
    before = [hashlib.sha256(p.read_bytes()).hexdigest() for p in sources]
    pipe = 'riwaq_qa_' + uuid.uuid4().hex
    command = [str(args.office.resolve(strict=True)),
               '-env:UserInstallation=' + (out / 'profile').as_uri(),
               '--headless', '--nologo', '--norestore', '--nodefault',
               '--accept=pipe,name=' + pipe + ';urp;StarOffice.ComponentContext']
    process = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               creationflags=subprocess.CREATE_NO_WINDOW)
    report = {'engine': 'LibreOffice 26.8.3.1 UNO', 'command': command, 'cases': []}
    desktop = None
    opened = []
    try:
        local = uno.getComponentContext()
        resolver = local.ServiceManager.createInstanceWithContext(
            'com.sun.star.bridge.UnoUrlResolver', local)
        deadline = time.monotonic() + 25
        while True:
            try:
                context = resolver.resolve('uno:pipe,name=' + pipe + ';urp;StarOffice.ComponentContext')
                break
            except Exception:
                if process.poll() is not None or time.monotonic() >= deadline:
                    raise RuntimeError('Private native document engine did not start')
                time.sleep(.15)
        desktop = context.ServiceManager.createInstanceWithContext('com.sun.star.frame.Desktop', context)

        def load(path):
            document = desktop.loadComponentFromURL(path.as_uri(), '_blank', 0,
                properties(Hidden=True, MacroExecutionMode=0, UpdateDocMode=0))
            if document is None:
                raise RuntimeError('Native engine refused synthetic file')
            opened.append(document)
            return document

        book = load(sources[0])
        sheet = book.Sheets.getByIndex(0)
        book.enableAutomaticCalculation(True)
        for value, expected in ((10, 20), (15, 30)):
            sheet.getCellRangeByName('A1').Value = value
            book.calculateAll()
            answer = sheet.getCellRangeByName('B1')
            actual, formula = answer.Value, answer.Formula
            target = out / ('recalculated-' + str(value) + '.xlsx')
            book.storeAsURL(target.as_uri(), properties(FilterName='Calc Office Open XML', Overwrite=False))
            reopened = load(target)
            reread = reopened.Sheets.getByIndex(0)
            saved = reread.getCellRangeByName('B1').Value
            identifier = reread.getCellRangeByName('C1').String
            zero = reread.getCellRangeByName('D1').Value
            report['cases'].append({'id': 'same-workbook-recalculate-' + str(value),
                'input': value, 'expected': expected, 'actual': actual, 'saved_value': saved,
                'formula': formula, 'text_identifier': identifier, 'zero': zero,
                'artifact': str(target.relative_to(ROOT)),
                'passed': actual == expected and saved == expected and formula == '=A1*2'
                          and identifier == '001' and zero == 0})
            reopened.close(True)
            opened.remove(reopened)
        document = load(sources[1])
        tables = document.TextTables
        table = tables.getByIndex(0)
        names = table.getCellNames()
        chosen = next(name for name in names if table.getCellByName(name).String == '20')
        table.getCellByName(chosen).String = '25'
        target = out / 'editable-table-changed.docx'
        document.storeAsURL(target.as_uri(), properties(FilterName='Office Open XML Text', Overwrite=False))
        reopened = load(target)
        saved = reopened.TextTables.getByIndex(0).getCellByName(chosen).String
        report['cases'].append({'id': 'native-editable-arabic-table', 'cell': chosen,
            'before': '20', 'after': saved, 'native_table_count': reopened.TextTables.Count,
            'artifact': str(target.relative_to(ROOT)), 'passed': saved == '25'})
        if args.chart:
            chart_book = load(sources[2])
            charts = chart_book.Sheets.getByIndex(0).Charts
            embedded = charts.getByIndex(0).EmbeddedObject
            sequences=[]
            for coordinate in embedded.getFirstDiagram().getCoordinateSystems():
                for chart_type in coordinate.getChartTypes():
                    for series in chart_type.getDataSeries():
                        for item in series.getDataSequences():
                            values=item.getValues()
                            sequences.append({'role':values.Role,'range':values.getSourceRangeRepresentation(),
                                              'values':list(values.getData())})
            report['chart_inspection']={'count':charts.Count,'data_sequences':sequences,
                'cell_B2':chart_book.Sheets.getByIndex(0).getCellRangeByName('B2').Value}
            target=out/'chart-native.pdf'
            chart_book.storeToURL(target.as_uri(),properties(FilterName='calc_pdf_Export'))
    except Exception as exc:
        report['error'] = {'type': type(exc).__name__, 'detail': str(exc)[:1500]}
    finally:
        for document in reversed(opened):
            try:
                document.close(True)
            except Exception:
                pass
        if desktop is not None:
            try:
                desktop.terminate()
            except Exception:
                pass
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            # The outer QA runner enforces the deadline for this entire owned tree.
            process.kill()
            process.wait(timeout=5)
        report['source_unchanged'] = [hashlib.sha256(p.read_bytes()).hexdigest() for p in sources] == before
        report['passed'] = len(report['cases']) == 3 and all(c['passed'] for c in report['cases']) and report['source_unchanged'] and 'error' not in report
        (out / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'passed': report['passed'], 'cases': len(report['cases'])}))


if __name__ == '__main__':
    main()
