"""Native conditional formatting against the previous successful directory update."""
MARKER = 'lorcana-directory-changes-v1'


def change_requests(sheet, before, names, height):
    """names are the ordered category/total names starting at row 5.

    Save previous values, not formulas: TCG counts recalculate in the same batch.
    Matching by name prevents insertion/reordering from highlighting unrelated rows.
    E:H are reserved, hidden snapshot columns; never overwrite unknown content.
    """
    from sheets_sync import cell
    sid = sheet['properties']['sheetId']
    owned = len(before) > 3 and len(before[3]) > 4 and before[3][4] == MARKER
    if not owned and any(any(v not in ('', None) for v in r[4:8]) for r in before):
        raise RuntimeError('Directory E:H contain user data; move them before enabling change highlights')
    previous = {}
    for r in before[4:]:
        if len(r) > 1 and r[1] in names:
            if r[1] in previous:
                raise RuntimeError('Duplicate directory category: ' + str(r[1]))
            previous[r[1]] = (r + [''] * 4)[:4]
    data = [[MARKER, '上次系列代码', '上次备注', '本次行状态']]
    for name in names:
        old = previous.get(name)
        data.append([old[2], old[0], old[3], '已有'] if old else ['', '', '', '新增'])
    data.extend([[''] * 4 for _ in range(max(0, height - 3 - len(data)))])
    requests = []
    columns = sheet['properties'].get('gridProperties', {}).get('columnCount', 26)
    if columns < 8:
        requests.append({'appendDimension': {'sheetId': sid, 'dimension': 'COLUMNS', 'length': 8-columns}})
    # Delete only our rules, in reverse order; retain all user-defined rules.
    for i, rule in reversed(list(enumerate(sheet.get('conditionalFormats', [])))):
        values = rule.get('booleanRule', {}).get('condition', {}).get('values', [])
        if any(MARKER in v.get('userEnteredValue', '') for v in values):
            requests.append({'deleteConditionalFormatRule': {'sheetId': sid, 'index': i}})
    requests.append({'updateCells': {'start': {'sheetId': sid, 'rowIndex': 3, 'columnIndex': 4},
        'rows': [{'values': [cell(v) for v in row]} for row in data], 'fields': 'userEnteredValue'}})
    requests.append({'updateDimensionProperties': {'range': {'sheetId': sid, 'dimension': 'COLUMNS', 'startIndex': 4, 'endIndex': 8},
        'properties': {'hiddenByUser': True}, 'fields': 'hiddenByUser'}})
    conditions = [('$H5="新增"', {'red': .85, 'green': .94, 'blue': .85}),
                  ('AND($H5="已有",OR($A5<>$F5,$C5<>$E5,$D5<>$G5))', {'red': 1, 'green': .94, 'blue': .70})]
    for expression, color in conditions:
        requests.append({'addConditionalFormatRule': {'index': 0, 'rule': {
            'ranges': [{'sheetId': sid, 'startRowIndex': 4, 'endRowIndex': 4+len(names), 'startColumnIndex': 0, 'endColumnIndex': 4}],
            'booleanRule': {'condition': {'type': 'CUSTOM_FORMULA', 'values': [{'userEnteredValue': f'=AND({expression},N("{MARKER}")=0)'}]},
                            'format': {'backgroundColor': color}}}}})
    return requests
