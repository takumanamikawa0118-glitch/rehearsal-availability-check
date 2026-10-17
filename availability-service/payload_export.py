"""Adapter for parse_probe.ROWS -> per-facility API envelopes; no network calls.
Not connected to the running hourly workflow yet.
"""
import datetime

FACILITIES = {'hiranuma','tobe','karuizawa','katabira','kikuna'}
STATUSES = {'available_in_source','available_candidate','occupied','unavailable','unknown','occupied_or_unknown'}

def make_payload(facility, rows, success, checked_at):
    if facility not in FACILITIES:
        raise ValueError('Unknown facility')
    parsed = datetime.datetime.strptime(checked_at, '%Y-%m-%dT%H:%M:%SZ')
    if parsed.strftime('%Y-%m-%dT%H:%M:%SZ') != checked_at:
        raise ValueError('Invalid timestamp')
    cells = []
    if success:
        seen = set()
        for row in rows:
            if row['facility'] != facility:
                raise ValueError('Mixed facilities')
            cell = {key:row[key] for key in ['room','date','period','status']}
            date = datetime.date.fromisoformat(cell['date'])
            if date.isoformat() != cell['date'] or cell['period'] not in ['午前','午後','夜間'] or cell['status'] not in STATUSES:
                raise ValueError('Invalid cell')
            if not isinstance(cell['room'],str) or not cell['room'].strip() or len(cell['room']) > 80:
                raise ValueError('Invalid room')
            if facility == 'katabira' and cell['status'] == 'available_in_source':
                raise ValueError('Image candidate needs review')
            key = (cell['room'],cell['date'],cell['period'])
            if key in seen:
                raise ValueError('Duplicate cell')
            seen.add(key)
            cells.append(cell)
        if not cells or len(cells) > 1500:
            raise ValueError('Invalid cell count')
    return {'schema_version':1,'facility':facility,'checked_at':checked_at,
            'result':'ok' if success else 'error','cells':cells}

if __name__ == '__main__':
    import unittest
    class AdapterTests(unittest.TestCase):
        def test_private_source_fields_removed(self):
            row = dict(facility='kikuna',room='A室',date='2026-10-20',period='午前',
                       status='occupied',raw='must never be sent',reason='not part of contract',page=1)
            result=make_payload('kikuna',[row],True,'2026-10-17T00:00:00Z')
            self.assertEqual(set(result['cells'][0]),{'room','date','period','status'})
        def test_failure_discards_partial_rows(self):
            self.assertEqual(make_payload('kikuna',[{'partial':True}],False,'2026-10-17T00:00:00Z')['cells'],[])
        def test_empty_success_refused(self):
            with self.assertRaises(ValueError):make_payload('kikuna',[],True,'2026-10-17T00:00:00Z')
    unittest.main()
