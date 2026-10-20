"""Offline regression tests. No website access or production writes."""
import hashlib
import socket
import ssl
import unittest
import urllib.error
from unittest.mock import patch
import check_five as c

class SourceTests(unittest.TestCase):
    def setUp(self):
        c.ATTEMPTS.clear()

    def test_three_images_and_reordered_months(self):
        payloads = {f'https://katabira.hodogaya-kumin.com/dataimge/{m}.jpg': str(m).encode() for m in [12,10,11]}
        html = ''.join(f'<img src="/dataimge/{m}.jpg" alt="{m}月予約状況">' for m in [12,10,11]).encode('cp932')
        registry = [{'sha256': hashlib.sha256(str(m).encode()).hexdigest(), 'year':2026, 'month':m} for m in [10,11,12]]
        def fetch(url,*args):
            b = html if url == c.URLS['katabira'] else payloads[url]
            return b, hashlib.sha256(b).hexdigest()
        with patch.object(c,'BASE',{'katabira_images':registry,'samples':[]}), patch.object(c,'fetch',fetch), patch.object(c.parser,'parse_image') as parse:
            c.check_katabira()
            self.assertEqual([x.kwargs['month'] for x in parse.call_args_list],[12,10,11])
            self.assertTrue(all(x.kwargs['year']==2026 for x in parse.call_args_list))

    def test_unreviewed_image_refused(self):
        html='<img src="/dataimge/12.jpg" alt="12月予約状況">'.encode('cp932')
        with patch.object(c,'BASE',{'katabira_images':[],'samples':[]}), patch.object(c,'fetch',side_effect=[(html,''),(b'new','newhash')]), patch.object(c.parser,'parse_image') as parse:
            with self.assertRaisesRegex(ValueError,'画像が更新'):c.check_katabira()
            parse.assert_not_called()

    def test_month_label_mismatch_refused(self):
        html='<img src="/dataimge/12.jpg" alt="11月予約状況">'.encode('cp932')
        with patch.object(c,'BASE',{'katabira_images':[{'sha256':'known','year':2026,'month':12}],'samples':[]}), patch.object(c,'fetch',side_effect=[(html,''),(b'image','known')]):
            with self.assertRaises(ValueError):c.check_katabira()

    def test_kikuna_timeout_recovers_on_third_attempt(self):
        timeout=urllib.error.URLError(socket.timeout('timed out'))
        with patch.object(c,'fetch_once',side_effect=[timeout,timeout,(b'ok','hash')]),patch.object(c.time,'sleep') as sleep:
            self.assertEqual(c.fetch(c.URLS['kikuna'],'kikuna.html',c.URLS['kikuna']),(b'ok','hash'))
            self.assertEqual([x.args[0] for x in sleep.call_args_list],[10,20])
            self.assertEqual([x['attempt'] for x in c.ATTEMPTS],[1,2,3])
            self.assertEqual(c.ATTEMPTS[0]['kind'],'timeout')
            self.assertEqual(c.ATTEMPTS[0]['timeout_seconds'],40)

    def test_retries_are_bounded(self):
        with patch.object(c,'fetch_once',side_effect=urllib.error.URLError(socket.timeout())),patch.object(c.time,'sleep'):
            with self.assertRaises(urllib.error.URLError):c.fetch(c.URLS['kikuna'],'kikuna.html',c.URLS['kikuna'])
            self.assertEqual(len(c.ATTEMPTS),3)

    def test_denial_rate_limit_and_tls_never_retried(self):
        for exc in [urllib.error.HTTPError(c.URLS['kikuna'],code,'denied',{},None) for code in [403,429]]+[urllib.error.URLError(ssl.SSLError('certificate'))]:
            with self.subTest(error=type(exc).__name__),patch.object(c,'fetch_once',side_effect=exc) as fetch,patch.object(c.time,'sleep') as sleep:
                with self.assertRaises(Exception):c.fetch(c.URLS['kikuna'],'kikuna.html',c.URLS['kikuna'])
                self.assertEqual(fetch.call_count,1);sleep.assert_not_called()

    def test_other_facilities_policy_unchanged(self):
        self.assertEqual(c.request_policy(c.URLS['hiranuma']),(25,(0,5)))
        self.assertEqual(c.request_policy(c.URLS['katabira']),(40,(0,10,20)))

    def test_failure_categories(self):
        self.assertEqual(c.failure_kind(urllib.error.URLError(socket.gaierror('dns'))),'dns')
        self.assertEqual(c.failure_kind(urllib.error.URLError(ConnectionResetError('reset'))),'connection')

    def test_open_and_read_failure_phases(self):
        with patch.object(c.urllib.request,'urlopen',side_effect=urllib.error.URLError(socket.timeout())):
            with self.assertRaises(Exception) as err:c.fetch_once(c.URLS['kikuna'],'kikuna.html',c.URLS['kikuna'])
            self.assertEqual(err.exception.fetch_phase,'connect_tls_or_headers')
        with patch.object(c.urllib.request,'urlopen') as open_mock:
            open_mock.return_value.__enter__.return_value.url=c.URLS['kikuna']
            open_mock.return_value.__enter__.return_value.read.side_effect=socket.timeout()
            with self.assertRaises(Exception) as err:c.fetch_once(c.URLS['kikuna'],'kikuna.html',c.URLS['kikuna'])
            self.assertEqual(err.exception.fetch_phase,'body_read')

if __name__=='__main__':unittest.main()
