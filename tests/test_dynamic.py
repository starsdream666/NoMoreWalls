#!/usr/bin/env python3
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import dynamic


def _response(text, raise_exc=None):
    res = mock.Mock()
    res.text = text
    if raise_exc:
        res.raise_for_status.side_effect = raise_exc
    else:
        res.raise_for_status.return_value = None
    return res


class TestFakeyou(unittest.TestCase):
    def test_returns_subscription_url(self):
        index = '<html><a href="/post/123/">latest</a></html>'
        post = 'header\n  https://image.fakeyou.top/sub/abc.txt<br>\nfooter\n'
        with mock.patch.object(dynamic, 'session') as sess:
            sess.get.side_effect = [_response(index), _response(post)]
            result = dynamic.fakeyou()
        self.assertEqual(result,
                         'https://image.fakeyou.top/sub/abc.txt#ignore=ss,vless')
        sess.get.assert_any_call('https://fakeyou.top')
        sess.get.assert_any_call('https://fakeyou.top/post/123/')

    def test_no_post_link_returns_none(self):
        with mock.patch.object(dynamic, 'session') as sess:
            sess.get.return_value = _response('<html>no posts</html>')
            self.assertIsNone(dynamic.fakeyou())

    def test_no_subscription_line_returns_none(self):
        index = '<a href="/post/1/">x</a>'
        post = 'nothing relevant here\n'
        with mock.patch.object(dynamic, 'session') as sess:
            sess.get.side_effect = [_response(index), _response(post)]
            self.assertIsNone(dynamic.fakeyou())

    def test_http_error_propagates(self):
        with mock.patch.object(dynamic, 'session') as sess:
            sess.get.return_value = _response('', raise_exc=RuntimeError('500'))
            with self.assertRaises(RuntimeError):
                dynamic.fakeyou()


class TestAutoLists(unittest.TestCase):
    def test_fakeyou_registered(self):
        self.assertIn(dynamic.fakeyou, dynamic.AUTOURLS)

    def test_autofetch_entries_callable(self):
        for fun in dynamic.AUTOURLS + dynamic.AUTOFETCH:
            self.assertTrue(callable(fun))


if __name__ == '__main__':
    unittest.main()
