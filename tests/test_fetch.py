#!/usr/bin/env python3
import base64
import binascii
import datetime
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import fetch
from fetch import (
    Node, NotANode, UnsupportedType, Source, DomainTree,
    b64encodes, b64encodes_safe, b64decodes, b64decodes_safe,
    normpath, raw2fastly, DEFAULT_UUID,
)


class TestBase64Helpers(unittest.TestCase):
    def test_encode_decode_roundtrip(self):
        for s in ('hello', '中文测试', '', 'a', 'ab', 'abc'):
            self.assertEqual(b64decodes(b64encodes(s)), s)
            self.assertEqual(b64decodes_safe(b64encodes_safe(s)), s)

    def test_decode_without_padding(self):
        raw = base64.b64encode('hello'.encode()).decode().rstrip('=')
        self.assertEqual(b64decodes(raw), 'hello')

    def test_decode_urlsafe_chars(self):
        s = '\xfb\xff?>'
        encoded = base64.urlsafe_b64encode(s.encode()).decode()
        self.assertEqual(b64decodes_safe(encoded), s)

    def test_decode_invalid_raises(self):
        with self.assertRaises((binascii.Error, UnicodeDecodeError)):
            b64decodes('!!!invalid!!!')

    def test_decode_non_utf8_raises(self):
        encoded = base64.b64encode(b'\xff\xfe\xfd').decode()
        with self.assertRaises(UnicodeDecodeError):
            b64decodes(encoded)


class TestNormpath(unittest.TestCase):
    def test_http_url_unchanged(self):
        url = 'https://example.com/./path'
        self.assertEqual(normpath(url), url)

    def test_file_url_expanded(self):
        result = normpath('file:///./abpwhite.txt')
        basedir = os.path.dirname(os.path.abspath(fetch.__file__))
        self.assertTrue(result.endswith('/abpwhite.txt'))
        self.assertIn(basedir.lstrip('/').replace(os.sep, '/'), result)


class TestRaw2Fastly(unittest.TestCase):
    def test_not_local_returns_unchanged(self):
        url = 'https://raw.githubusercontent.com/owner/repo/master/file.txt'
        with mock.patch.object(fetch, 'LOCAL', False):
            self.assertEqual(raw2fastly(url), url)

    def test_local_converts_raw_github(self):
        url = 'https://raw.githubusercontent.com/owner/repo/master/dir/file.txt'
        with mock.patch.object(fetch, 'LOCAL', True):
            self.assertEqual(
                raw2fastly(url),
                'https://fastly.jsdelivr.net/gh/owner/repo@master/dir/file.txt')

    def test_local_other_url_unchanged(self):
        url = 'https://example.com/file.txt'
        with mock.patch.object(fetch, 'LOCAL', True):
            self.assertEqual(raw2fastly(url), url)


class TestNodeUrlparse(unittest.TestCase):
    def test_fragment_split(self):
        res = Node.urlparse('trojan://p@h:1?a=b#name#with#hash')
        self.assertEqual(res.fragment, 'hash')

    def test_no_fragment(self):
        res = Node.urlparse('trojan://p@h:1?a=b')
        self.assertEqual(res.fragment, '')


def make_vmess_url(**overrides):
    v = {
        'v': '2', 'ps': 'test-node', 'add': '1.2.3.4', 'port': '8080',
        'aid': '0', 'scy': 'auto', 'net': 'tcp', 'type': 'none',
        'tls': '', 'id': DEFAULT_UUID,
    }
    v.update(overrides)
    return 'vmess://' + b64encodes(json.dumps(v))


class TestNodeParsing(unittest.TestCase):
    def setUp(self):
        Node.gNames.clear()

    def test_not_a_node(self):
        with self.assertRaises(NotANode):
            Node('no-scheme-here')

    def test_unsupported_type(self):
        with self.assertRaises(UnsupportedType):
            Node('wireguard://something')

    def test_bad_input_type(self):
        with self.assertRaises(TypeError):
            Node(42)

    def test_vmess_basic(self):
        n = Node(make_vmess_url())
        self.assertEqual(n.type, 'vmess')
        self.assertEqual(n.data['server'], '1.2.3.4')
        self.assertEqual(n.data['port'], '8080')
        self.assertEqual(n.data['name'], 'test-node')
        self.assertEqual(n.data['uuid'], DEFAULT_UUID)
        self.assertFalse(n.data['tls'])
        self.assertEqual(n.data['alterId'], 0)

    def test_vmess_ws_opts(self):
        n = Node(make_vmess_url(net='ws', host='example.com', path='/ws'))
        self.assertEqual(n.data['ws-opts']['path'], '/ws')
        self.assertEqual(n.data['ws-opts']['headers']['Host'], 'example.com')

    def test_vmess_h2_opts(self):
        n = Node(make_vmess_url(net='h2', host='a.com,b.com', path='/h2'))
        self.assertEqual(n.data['h2-opts']['host'], ['a.com', 'b.com'])
        self.assertEqual(n.data['h2-opts']['path'], '/h2')

    def test_vmess_grpc_opts(self):
        n = Node(make_vmess_url(net='grpc', path='svc'))
        self.assertEqual(n.data['grpc-opts']['grpc-service-name'], 'svc')

    def test_vmess_invalid_payload(self):
        with self.assertRaises(UnsupportedType):
            Node('vmess://' + b64encodes('not json'))

    def test_vmess_url_roundtrip(self):
        url = make_vmess_url(net='ws', host='example.com', path='/ws', tls='tls')
        n = Node(url)
        n2 = Node(n.url)
        self.assertEqual(n, n2)
        self.assertEqual(n2.data['ws-opts']['headers']['Host'], 'example.com')
        self.assertTrue(n2.data['tls'])

    def test_ss_basic(self):
        info = b64encodes_safe('aes-256-gcm:passwd')
        n = Node(f'ss://{info}@1.2.3.4:8388#MyNode')
        self.assertEqual(n.type, 'ss')
        self.assertEqual(n.data['server'], '1.2.3.4')
        self.assertEqual(n.data['port'], 8388)
        self.assertEqual(n.data['cipher'], 'aes-256-gcm')
        self.assertEqual(n.data['password'], 'passwd')
        self.assertEqual(n.data['name'], 'MyNode')

    def test_ss_plain_userinfo(self):
        n = Node('ss://aes-128-gcm:pw@5.6.7.8:443#n')
        self.assertEqual(n.data['cipher'], 'aes-128-gcm')
        self.assertEqual(n.data['password'], 'pw')

    def test_ss_bad_port(self):
        info = b64encodes_safe('aes-256-gcm:passwd')
        with self.assertRaises(UnsupportedType):
            Node(f'ss://{info}@1.2.3.4:notaport#x')

    def test_ss_url_roundtrip(self):
        info = b64encodes_safe('aes-256-gcm:passwd')
        n = Node(f'ss://{info}@1.2.3.4:8388#MyNode')
        n2 = Node(n.url)
        self.assertEqual(n, n2)
        self.assertEqual(n2.data['name'], 'MyNode')

    def test_ssr_basic(self):
        passwd = b64encodes_safe('pw')
        body = f'1.2.3.4:8388:origin:aes-256-cfb:plain:{passwd}/?remarks=abc&obfsparam=op&protoparam=pp&group=g'
        n = Node('ssr://' + b64encodes_safe(body))
        self.assertEqual(n.type, 'ssr')
        self.assertEqual(n.data['server'], '1.2.3.4')
        self.assertEqual(n.data['port'], '8388')
        self.assertEqual(n.data['protocol'], 'origin')
        self.assertEqual(n.data['cipher'], 'aes-256-cfb')
        self.assertEqual(n.data['obfs'], 'plain')
        self.assertEqual(n.data['password'], 'pw')
        self.assertEqual(n.data['name'], 'abc')
        self.assertEqual(n.data['obfs-param'], 'op')
        self.assertEqual(n.data['protocol-param'], 'pp')
        self.assertEqual(n.data['group'], 'g')

    def test_trojan_basic(self):
        n = Node('trojan://mypass@example.com:443?sni=sni.com&allowInsecure=1&alpn=h2%2Chttp%2F1.1#TName')
        self.assertEqual(n.type, 'trojan')
        self.assertEqual(n.data['server'], 'example.com')
        self.assertEqual(n.data['port'], 443)
        self.assertEqual(n.data['password'], 'mypass')
        self.assertEqual(n.data['sni'], 'sni.com')
        self.assertTrue(n.data['skip-cert-verify'])
        self.assertEqual(n.data['alpn'], ['h2', 'http/1.1'])
        self.assertEqual(n.data['name'], 'TName')

    def test_trojan_ws(self):
        n = Node('trojan://p@h.com:443?type=ws&host=ws.com&path=/p#n')
        self.assertEqual(n.data['network'], 'ws')
        self.assertEqual(n.data['ws-opts']['headers']['Host'], 'ws.com')
        self.assertEqual(n.data['ws-opts']['path'], '/p')

    def test_trojan_grpc(self):
        n = Node('trojan://p@h.com:443?type=grpc&serviceName=svc#n')
        self.assertEqual(n.data['grpc-opts']['grpc-service-name'], 'svc')

    def test_trojan_url_roundtrip(self):
        n = Node('trojan://mypass@example.com:443?sni=sni.com&allowInsecure=1#TName')
        n2 = Node(n.url)
        self.assertEqual(n, n2)
        self.assertEqual(n2.data['sni'], 'sni.com')

    def test_vless_basic(self):
        n = Node('vless://%s@h.com:443?security=tls&sni=v.com&fp=chrome&type=ws&host=w.com&path=/x#VN'
                 % DEFAULT_UUID)
        self.assertEqual(n.type, 'vless')
        self.assertEqual(n.data['uuid'], DEFAULT_UUID)
        self.assertTrue(n.data['tls'])
        self.assertEqual(n.data['servername'], 'v.com')
        self.assertEqual(n.data['client-fingerprint'], 'chrome')
        self.assertEqual(n.data['network'], 'ws')
        self.assertEqual(n.data['ws-opts']['headers']['Host'], 'w.com')

    def test_vless_flow_marking(self):
        n = Node('vless://u@h.com:443?flow=xtls-rprx-vision#n')
        self.assertEqual(n.data['flow'], 'xtls-rprx-vision!')
        n = Node('vless://u@h.com:443?flow=xtls-rprx-vision-udp443#n')
        self.assertEqual(n.data['flow'], 'xtls-rprx-vision-udp443')

    def test_vless_reality(self):
        n = Node('vless://u@h.com:443?security=reality&pbk=KEY&sid=42#n')
        self.assertEqual(n.data['reality-opts']['public-key'], 'KEY')
        self.assertEqual(n.data['reality-opts']['short-id'], '42')

    def test_hy2_alias(self):
        n = Node('hy2://pw@h.com:443#n')
        self.assertEqual(n.type, 'hysteria2')

    def test_hysteria2_basic(self):
        n = Node('hysteria2://pw@h.com:8443?insecure=1&sni=s.com&obfs=salamander&obfs-password=op#HN')
        self.assertEqual(n.data['port'], 8443)
        self.assertEqual(n.data['password'], 'pw')
        self.assertTrue(n.data['skip-cert-verify'])
        self.assertEqual(n.data['sni'], 's.com')
        self.assertEqual(n.data['obfs'], 'salamander')
        self.assertEqual(n.data['obfs-password'], 'op')

    def test_hysteria2_default_port(self):
        n = Node('hysteria2://pw@h.com#n')
        self.assertEqual(n.data['port'], 443)

    def test_tuic_basic(self):
        n = Node('tuic://%s:pw@h.com:8443?congestion_control=bbr&udp_relay_mode=native&alpn=h3#TU'
                 % DEFAULT_UUID)
        self.assertEqual(n.type, 'tuic')
        self.assertEqual(n.data['uuid'], DEFAULT_UUID)
        self.assertEqual(n.data['password'], 'pw')
        self.assertEqual(n.data['port'], 8443)
        self.assertEqual(n.data['congestion-controller'], 'bbr')
        self.assertEqual(n.data['udp-relay-mode'], 'native')
        self.assertEqual(n.data['alpn'], ['h3'])

    def test_http_legacy(self):
        n = Node('http://user:pw@1.2.3.4:8080#n')
        self.assertEqual(n.type, 'http')
        self.assertEqual(n.data['username'], 'user')
        self.assertEqual(n.data['password'], 'pw')
        self.assertFalse(n.data['tls'])

    def test_https_legacy(self):
        n = Node('https://1.2.3.4:8080#n')
        self.assertEqual(n.type, 'https')
        self.assertTrue(n.data['tls'])

    def test_socks_legacy(self):
        n = Node('socks://1.2.3.4:1080#n')
        self.assertEqual(n.type, 'socks5')

    def test_legacy_url_roundtrip(self):
        n = Node('http://user:pw@1.2.3.4:8080#n')
        self.assertEqual(n.url, 'http://user:pw@1.2.3.4:8080')

    def test_ipv6_server_bracketed(self):
        n = Node('trojan://p@[2001:db8::1]:443#n')
        self.assertEqual(n.data['server'], '[2001:db8::1]')

    def test_unnamed_node_gets_default_name(self):
        n = Node('trojan://p@h.com:443')
        self.assertEqual(n.data['name'], '未命名')

    def test_nonascii_scheme_fixed(self):
        n = Node('中文trojan://p@h.com:443#n')
        self.assertEqual(n.type, 'trojan')


class TestNodeHashEq(unittest.TestCase):
    def setUp(self):
        Node.gNames.clear()

    def test_same_node_equal(self):
        a = Node(make_vmess_url(ps='A'))
        b = Node(make_vmess_url(ps='B'))
        self.assertEqual(a, b)
        self.assertEqual(hash(a), hash(b))

    def test_different_port_not_equal(self):
        a = Node(make_vmess_url(port='8080'))
        b = Node(make_vmess_url(port='9090'))
        self.assertNotEqual(a, b)

    def test_not_equal_to_other_types(self):
        self.assertNotEqual(Node(make_vmess_url()), 'a string')

    def test_update_merges_data(self):
        a = Node(make_vmess_url())
        b = Node(make_vmess_url())
        b.data['extra'] = 'x'
        a.update(b)
        self.assertEqual(a.data['extra'], 'x')


class TestNodeName(unittest.TestCase):
    def setUp(self):
        Node.gNames.clear()

    def _node(self, name='n'):
        return Node({'name': name, 'type': 'trojan', 'server': 'h.com',
                     'port': 443, 'password': 'p'})

    def test_name_prefers_lower_rate(self):
        n = self._node('normal')
        n.names = {'@spam', 'normal'}
        self.assertEqual(n.name, '@spam')

    def test_format_name_truncates(self):
        n = self._node('x' * 50)
        n.format_name(max_len=30)
        self.assertEqual(n.data['name'], 'x' * 30 + '...')

    def test_format_name_dedupes(self):
        a = self._node('dup')
        a.format_name()
        Node.gNames.add(a.data['name'])
        b = self._node('dup')
        b.format_name()
        self.assertEqual(b.data['name'], 'dup #1')

    def test_format_name_replaces_banned_words(self):
        if not fetch.BANNED_WORDS:
            self.skipTest('BANNED_WORDS empty (STOP mode)')
        word = fetch.BANNED_WORDS[0]
        n = self._node(f'ab{word}cd')
        n.format_name()
        self.assertEqual(n.data['name'], 'ab' + '*' * len(word) + 'cd')

    def test_format_name_normalizes_math_bold(self):
        n = self._node('\N{MATHEMATICAL BOLD CAPITAL A}\N{MATHEMATICAL BOLD SMALL B}')
        n.format_name()
        self.assertEqual(n.data['name'], 'Ab')

    def test_format_name_arrow_replacement(self):
        n = self._node('HK' + chr(10144) + 'US')
        n.format_name()
        self.assertEqual(n.data['name'], 'HK->US')


class TestNodeFake(unittest.TestCase):
    def setUp(self):
        Node.gNames.clear()

    def _node(self, **kw):
        data = {'name': 'n', 'type': 'trojan', 'server': 'h.com',
                'port': 443, 'password': 'p'}
        data.update(kw)
        return Node(data)

    def test_normal_not_fake(self):
        self.assertFalse(self._node().isfake)

    def test_fake_ip(self):
        self.assertTrue(self._node(server='8.8.8.8').isfake)

    def test_low_port(self):
        self.assertTrue(self._node(port=1).isfake)

    def test_fake_domain(self):
        self.assertTrue(self._node(server='www.google.com').isfake)
        self.assertTrue(self._node(server='github.com').isfake)

    def test_missing_server(self):
        n = self._node()
        del n.data['server']
        self.assertTrue(n.isfake)

    def test_google_sni_rewritten(self):
        n = self._node(sni='www.google.com')
        self.assertFalse(n.isfake)
        self.assertEqual(n.data['sni'], 'www.bing.com')


class TestClashData(unittest.TestCase):
    def setUp(self):
        Node.gNames.clear()

    def test_ipv6_brackets_removed(self):
        n = Node('trojan://p@[2001:db8::1]:443#n')
        self.assertEqual(n.clash_data['server'], '2001:db8::1')

    def test_numeric_password_marked_str(self):
        n = Node({'name': 'n', 'type': 'trojan', 'server': 'h.com',
                  'port': 443, 'password': '12345'})
        self.assertEqual(n.clash_data['password'], '!!str 12345')

    def test_invalid_uuid_replaced(self):
        n = Node({'name': 'n', 'type': 'vless', 'server': 'h.com',
                  'port': 443, 'uuid': 'short'})
        self.assertEqual(n.clash_data['uuid'], DEFAULT_UUID)

    def test_group_removed(self):
        n = Node({'name': 'n', 'type': 'ss', 'server': 'h.com', 'port': 443,
                  'password': 'p', 'cipher': 'aes-256-gcm', 'group': 'g'})
        self.assertNotIn('group', n.clash_data)

    def test_empty_cipher_defaults_auto(self):
        n = Node({'name': 'n', 'type': 'vmess', 'server': 'h.com',
                  'port': 443, 'uuid': DEFAULT_UUID, 'cipher': ''})
        self.assertEqual(n.clash_data['cipher'], 'auto')

    def test_vless_flow_suffixes_stripped(self):
        n = Node('vless://u@h.com:443?flow=xtls-rprx-vision#n')
        self.assertEqual(n.clash_data['flow'], 'xtls-rprx-vision')
        n = Node('vless://u@h.com:443?flow=xtls-rprx-vision-udp443#n')
        self.assertEqual(n.clash_data['flow'], 'xtls-rprx-vision')

    def test_alpn_string_split(self):
        n = Node({'name': 'n', 'type': 'trojan', 'server': 'h.com',
                  'port': 443, 'password': 'p', 'alpn': 'h2, http/1.1'})
        self.assertEqual(n.clash_data['alpn'], ['h2', 'http/1.1'])

    def test_reality_short_id_marked_str(self):
        n = Node('vless://u@h.com:443?security=reality&pbk=K&sid=07#n')
        self.assertEqual(n.clash_data['reality-opts']['short-id'], '!!str 07')


class TestSupports(unittest.TestCase):
    def setUp(self):
        Node.gNames.clear()

    def _node(self, **kw):
        data = {'name': 'n', 'server': 'h.com', 'port': 443}
        data.update(kw)
        return Node(data)

    def test_trojan_supported(self):
        self.assertTrue(self._node(type='trojan', password='p').supports_clash())

    def test_vmess_supported_cipher(self):
        n = self._node(type='vmess', uuid=DEFAULT_UUID, cipher='auto')
        self.assertTrue(n.supports_clash())

    def test_vmess_unsupported_cipher(self):
        n = self._node(type='vmess', uuid=DEFAULT_UUID, cipher='zero')
        self.assertFalse(n.supports_clash())

    def test_vless_meta_only(self):
        n = self._node(type='vless', uuid=DEFAULT_UUID)
        self.assertFalse(n.supports_clash())
        self.assertTrue(n.supports_meta())

    def test_ssr_unsupported_obfs(self):
        n = self._node(type='ssr', password='p', cipher='aes-256-cfb',
                       obfs='bad_obfs', protocol='origin')
        self.assertFalse(n.supports_clash())

    def test_ssr_unsupported_protocol(self):
        n = self._node(type='ssr', password='p', cipher='aes-256-cfb',
                       obfs='plain', protocol='bad_proto')
        self.assertFalse(n.supports_clash())

    def test_ss_supported(self):
        n = self._node(type='ss', password='p', cipher='aes-256-gcm')
        self.assertTrue(n.supports_clash())

    def test_fake_not_supported(self):
        n = self._node(type='trojan', password='p', server='8.8.8.8')
        self.assertFalse(n.supports_clash())
        self.assertFalse(n.supports_ray())

    def test_h2_grpc_forces_tls(self):
        n = self._node(type='vmess', uuid=DEFAULT_UUID, cipher='auto', network='grpc')
        self.assertTrue(n.supports_clash())
        self.assertTrue(n.data['tls'])

    def test_socks5_tls_not_ray(self):
        n = self._node(type='socks5', tls=True)
        self.assertFalse(n.supports_ray())
        n = self._node(type='socks5')
        self.assertTrue(n.supports_ray())

    def test_obfs_without_password_unsupported(self):
        n = self._node(type='trojan', password='p', obfs='salamander')
        self.assertFalse(n.supports_clash())


class TestDomainTree(unittest.TestCase):
    def test_insert_and_get(self):
        t = DomainTree()
        t.insert('ads.example.com')
        t.insert('track.example.org')
        self.assertEqual(sorted(t.get()),
                         ['ads.example.com', 'track.example.org'])

    def test_parent_domain_collapses_children(self):
        t = DomainTree()
        t.insert('a.example.com')
        t.insert('example.com')
        self.assertEqual(t.get(), ['example.com'])

    def test_remove(self):
        t = DomainTree()
        t.insert('ads.example.com')
        t.insert('other.com')
        t.remove('ads.example.com')
        self.assertEqual(t.get(), ['other.com'])

    def test_remove_parent_clears_subtree(self):
        t = DomainTree()
        t.insert('a.example.com')
        t.insert('b.example.com')
        t.remove('example.com')
        self.assertEqual(t.get(), [])

    def test_remove_missing_is_noop(self):
        t = DomainTree()
        t.insert('example.com')
        t.remove('nonexistent.org')
        self.assertEqual(t.get(), ['example.com'])


class TestSource(unittest.TestCase):
    def test_plain_url(self):
        s = Source('https://example.com/sub')
        self.assertEqual(s.url, 'https://example.com/sub')
        self.assertIsNone(s.url_source)

    def test_dynamic_source(self):
        def myfun(): return []
        s = Source(myfun)
        self.assertEqual(s.url, 'dynamic://myfun')
        self.assertIs(s.url_source, myfun)

    def test_gen_url_date(self):
        s = Source('+date https://example.com/%Y/%m/%d.txt')
        # gen_url decrements self.date after use, so compare with the day after
        used_date = s.date + datetime.timedelta(days=1)
        self.assertEqual(s.url, used_date.strftime('https://example.com/%Y/%m/%d.txt'))

    def test_gen_url_regenerates_previous_day(self):
        s = Source('+date https://example.com/%Y%m%d.txt')
        first = s.url
        s.gen_url()
        second = s.url
        self.assertNotEqual(first, second)

    def test_parse_clash_config(self):
        s = Source('https://example.com/x')
        s.content = 'proxies:\n- {name: a, type: ss, server: h, port: 1}\n'
        s.parse()
        self.assertEqual(len(s.sub), 1)
        self.assertEqual(s.sub[0]['name'], 'a')

    def test_parse_raw_list(self):
        s = Source('https://example.com/x')
        s.content = 'trojan://p@h:443#a\ntrojan://p@h:444#b'
        s.parse()
        self.assertEqual(len(s.sub), 2)

    def test_parse_b64_sub(self):
        s = Source('https://example.com/x')
        s.content = b64encodes('trojan://p@h:443#a\ntrojan://p@h:444#b')
        s.parse()
        self.assertEqual(len(s.sub), 2)

    def test_parse_max_limit_exceeded(self):
        s = Source('https://example.com/x')
        s.cfg = {'max': 1}
        s.content = 'trojan://p@h:443#a\ntrojan://p@h:444#b'
        s.parse()
        self.assertEqual(s.sub, [])
        self.assertTrue(s.exc_queue)

    def test_parse_ignore_types(self):
        s = Source('https://example.com/x')
        s.cfg = {'ignore': ['ss']}
        s.content = 'ss://x@h:1#a\ntrojan://p@h:443#b'
        s.parse()
        self.assertEqual(s.sub, ['trojan://p@h:443#b'])

    def test_parse_ignore_types_dict(self):
        s = Source('https://example.com/x')
        s.cfg = {'ignore': ['ss']}
        s.content = 'proxies:\n- {name: a, type: ss, server: h, port: 1}\n- {name: b, type: trojan, server: h, port: 2}\n'
        s.parse()
        self.assertEqual(len(s.sub), 1)
        self.assertEqual(s.sub[0]['type'], 'trojan')

    def test_get_file_url_with_cfg(self):
        with tempfile.NamedTemporaryFile('w', suffix='.txt', delete=False) as f:
            f.write('ss://YWVzLTI1Ni1nY206cA==@h:8388#a\ntrojan://p@h:443#b\n')
            path = f.name
        try:
            s = Source(f'file://{path}#ignore=ss')
            s.get()
            self.assertEqual(s.sub, ['trojan://p@h:443#b'])
        finally:
            os.unlink(path)

    def test_get_bad_max_cfg(self):
        with tempfile.NamedTemporaryFile('w', suffix='.txt', delete=False) as f:
            f.write('trojan://p@h:443#a\n')
            path = f.name
        try:
            s = Source(f'file://{path}#max=notanumber')
            s.get()
            self.assertNotIn('max', s.cfg)
            self.assertTrue(any('最大节点数' in e for e in s.exc_queue))
        finally:
            os.unlink(path)


class TestExtract(unittest.TestCase):
    def _mock_get(self, text, status=200):
        res = mock.Mock()
        res.status_code = status
        res.text = text
        return mock.patch.object(fetch.session, 'get', return_value=res)

    def test_extract_urls(self):
        with self._mock_get('https://a.com/sub\nnot-a-url\nhttp://b.com/x\n'):
            urls = fetch.extract('https://example.com/list')
        self.assertEqual(urls, {'https://a.com/sub', 'http://b.com/x'})

    def test_extract_propagates_mark(self):
        with self._mock_get('https://a.com/sub\n'):
            urls = fetch.extract('https://example.com/list#max=5')
        self.assertEqual(urls, {'https://a.com/sub#max=5'})

    def test_extract_error_status(self):
        with self._mock_get('', status=404):
            self.assertEqual(fetch.extract('https://example.com/list'), 404)


class TestMerge(unittest.TestCase):
    def setUp(self):
        Node.gNames.clear()
        self._merged = fetch.merged
        self._used = fetch.used
        self._unknown = fetch.unknown
        fetch.merged = {}
        fetch.used = {}
        fetch.unknown = set()

    def tearDown(self):
        fetch.merged = self._merged
        fetch.used = self._used
        fetch.unknown = self._unknown

    def _source(self, sub):
        s = Source('https://example.com/x')
        s.sub = sub
        return s

    def test_merge_dedupes(self):
        fetch.merge(self._source([make_vmess_url(ps='A'), make_vmess_url(ps='B')]))
        self.assertEqual(len(fetch.merged), 1)

    def test_merge_unknown_type(self):
        fetch.merge(self._source(['wireguard://x@h:1#n']))
        self.assertEqual(len(fetch.merged), 0)
        self.assertEqual(len(fetch.unknown), 1)

    def test_merge_skips_non_urls(self):
        fetch.merge(self._source(['not a node', make_vmess_url()]))
        self.assertEqual(len(fetch.merged), 1)

    def test_merge_empty_sub(self):
        fetch.merge(self._source([]))
        self.assertEqual(len(fetch.merged), 0)

    def test_merge_tracks_source_ids(self):
        fetch.merge(self._source([make_vmess_url()]), sourceId=7)
        self.assertEqual(len(fetch.used), 1)
        self.assertIn(7, list(fetch.used.values())[0])


if __name__ == '__main__':
    unittest.main()
