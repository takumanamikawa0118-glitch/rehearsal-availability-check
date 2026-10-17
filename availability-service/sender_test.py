import io
import json
import unittest
import urllib.request
from collect_and_send import send, NoRedirect, ENDPOINT

class Response(io.BytesIO):
    status = 200
class Opener:
    def __init__(self, result): self.result = result; self.request = None
    def open(self, request, timeout):
        self.request = request
        return Response(json.dumps(self.result).encode())
class SenderTests(unittest.TestCase):
    def test_fixed_destination_and_header_only_token(self):
        fake = Opener({'accepted': True, 'updated': True})
        token = 'test-only-secret-with-no-real-access-00000'
        self.assertTrue(send({'cells': []}, token, fake))
        self.assertEqual(fake.request.full_url, ENDPOINT)
        self.assertNotIn(token, fake.request.full_url)
        self.assertNotIn(token.encode(), fake.request.data)
        self.assertEqual(fake.request.get_header('Authorization'), 'Bearer ' + token)
    def test_redirect_refused(self):
        self.assertIsNone(NoRedirect().redirect_request(None, None, 302, '', {}, 'https://other.test'))
    def test_invalid_token_and_payload_limit(self):
        for token in ['', 'short', 'x'*32+'\n']:
            with self.assertRaises(ValueError): send({}, token, Opener({}))
        with self.assertRaises(ValueError): send({'x':'a'*262144}, 'x'*64, Opener({}))
    def test_unexpected_response_rejected(self):
        with self.assertRaises(RuntimeError): send({}, 'x'*64, Opener({'accepted':False}))
        self.assertFalse(send({}, 'x'*64, Opener({'accepted':True,'updated':False})))
if __name__ == '__main__': unittest.main()
