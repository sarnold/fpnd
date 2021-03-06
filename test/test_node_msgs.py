# -*- coding: utf-8 -*-
import unittest

import pytest

from nanoservice import Subscriber
from nanoservice import Publisher

from node_tools.msg_queues import clean_from_queue
from node_tools.msg_queues import handle_announce_msg
from node_tools.msg_queues import handle_node_queues
from node_tools.msg_queues import lookup_node_id
from node_tools.msg_queues import make_cfg_msg
from node_tools.msg_queues import make_version_msg
from node_tools.msg_queues import manage_incoming_nodes
from node_tools.msg_queues import parse_version_msg
from node_tools.msg_queues import process_hold_queue
from node_tools.msg_queues import valid_announce_msg
from node_tools.msg_queues import valid_cfg_msg
from node_tools.msg_queues import valid_version
from node_tools.msg_queues import wait_for_cfg_msg
from node_tools.network_funcs import drain_msg_queue
from node_tools.network_funcs import publish_cfg_msg
from node_tools.sched_funcs import check_return_status
from node_tools.trie_funcs import find_dangling_nets
from node_tools.trie_funcs import trie_is_empty
from node_tools.trie_funcs import update_id_trie


def test_invalid_msg():
    msgs = ['deadbeeh00', 'deadbeef0', 'deadbeef000']
    for msg in msgs:
        with pytest.raises(AssertionError):
            res = valid_announce_msg(msg)


def test_valid_msg():
    res = valid_announce_msg('deadbeef00')
    assert res is True


def test_invalid_cfg_msg():

    msg_list = [
                '{"node_id": "02beefdead"}',
                '{"node_id": "02beefdead", "net0_id": ["7ac4235ec5d3d938"]}',
                '{"node_id": "022beefdead", "networks": ["7ac4235ec5d3d938"]}',
                '{"node": "02beefdead", "networks": ["7ac4235ec5d3d938"]}',
                '{"node_id": "02beefhead", "networks": ["7ac4235ec5d3d938"]}'
               ]

    for msg in msg_list:
        with pytest.raises(AssertionError):
            valid_cfg_msg(msg)


def test_valid_cfg_msg():
    res = valid_cfg_msg('{"node_id": "02beefdead", "networks": ["7ac4235ec5d3d938"]}')
    assert res is True


def test_invalid_version():
    base_ver = '0.9.5'
    res = valid_version(base_ver, None)
    assert res is False
    res = valid_version(base_ver, '1.0')
    assert res is False
    res = valid_version(base_ver, '1.1.b')
    assert res is False


def test_valid_version():
    base_ver = '0.9.5'
    res = valid_version(base_ver, '0.9.6')
    assert res is True
    res = valid_version(base_ver, '0.9.4')
    assert res is False
    res = valid_version(base_ver, base_ver)
    assert res is True


def test_make_cfg_msg():
    # the char set used for trie keys is string.hexdigits
    import json
    from node_tools import ctlr_data as ct
    assert trie_is_empty(ct.id_trie) is True

    node_id = '02beefdead'
    needs = [False, True]
    net_list = ['7ac4235ec5d3d938']
    ct.id_trie[node_id] = (net_list, needs)
    cfg_msg = '{"node_id": "02beefdead", "networks": ["7ac4235ec5d3d938"]}'

    res = make_cfg_msg(ct.id_trie, node_id)
    assert type(res) is str
    assert json.loads(res) == json.loads(cfg_msg)
    assert valid_cfg_msg(res)
    ct.id_trie.clear()
    assert trie_is_empty(ct.id_trie) is True


def test_make_version_msg():
    import json
    from node_tools import __version__

    node_id = '02beefdead'
    d = {
        "node_id": "{}".format(node_id),
        "version": __version__
    }
    ver_msg = json.dumps(d)

    res = make_version_msg(node_id)
    assert type(res) is str
    assert json.loads(res) == json.loads(ver_msg)

    res = make_version_msg(node_id, '0.9.4')
    assert '0.9.4' in res
    res = make_version_msg(node_id, 'UPGRADE_REQUIRED')
    assert 'UPGRADE' in res


def test_parse_version_msg():
    import json
    from node_tools import __version__

    node_id = '02beefdead'
    d = {
        "node_id": "{}".format(node_id),
        "version": __version__
    }
    ver_msg = json.dumps(d)
    # print(ver_msg)

    res = parse_version_msg(ver_msg)
    assert type(res) is list
    assert res == [node_id , __version__]

    res = parse_version_msg(node_id)
    assert type(res) is list
    assert res == [node_id , None]


class BaseTestCase(unittest.TestCase):

    def setUp(self):
        import diskcache as dc
        from node_tools import ctlr_data as ct

        self.node1 = 'deadbeef01'
        self.node2 = '20beefdead'
        self.needs = [False, True]
        self.net_list = ['7ac4235ec5d3d940']
        self.trie = ct.id_trie
        self.node_q = dc.Deque(directory='/tmp/test-nq')
        self.off_q = dc.Deque(directory='/tmp/test-oq')
        self.pub_q = dc.Deque(directory='/tmp/test-pq')
        self.tmp_q = dc.Deque(directory='/tmp/test-tq')
        self.node_q.clear()
        self.off_q.clear()
        self.pub_q.clear()
        self.tmp_q.clear()
        self.trie.clear()

        self.addr = '127.0.0.1'
        self.tcp_addr = 'tcp://{}:9442'.format(self.addr)
        self.active_list = []
        self.off_list = []
        self.sub_list = []

        def handle_msg(msg):
            self.sub_list.append(msg)
            return self.sub_list

        def handle_cfg(msg):
            self.active_list.append(msg)
            return self.active_list

        def offline(msg):
            if msg not in self.off_list:
                self.off_list.append(msg)
            return self.off_list

        self.service = Subscriber(self.tcp_addr)
        self.service.subscribe('handle_node', handle_msg)
        self.service.subscribe('cfg_msgs', handle_cfg)
        self.service.subscribe('offline', offline)

    def tearDown(self):
        self.service.socket.close()
        self.node_q.clear()
        self.off_q.clear()
        self.pub_q.clear()
        self.tmp_q.clear()


class TestPubCfg(BaseTestCase):

    def test_node_cfg(self):
        import json
        self.pub_q.append(self.node1)
        self.pub_q.append(self.node2)
        self.trie[self.node1] = (self.net_list, self.needs)
        self.cfg_msg = '{"node_id": "deadbeef01", "networks": ["7ac4235ec5d3d940"]}'
        self.cfg_dict = json.loads(self.cfg_msg)

        # Client side
        publish_cfg_msg(self.trie, self.node1)

        # server side
        self.assertEqual(list(self.pub_q), [self.node1, self.node2])
        res = self.service.process()
        self.assertEqual(res, self.active_list)
        res_msg = json.loads(res[0])
        self.assertEqual(res_msg, self.cfg_dict)


class TestPubSub(BaseTestCase):

    def test_node_pub(self):
        self.node_q.append(self.node1)
        self.node_q.append(self.node2)

        # Client side
        drain_msg_queue(self.node_q, self.pub_q)

        # server side
        res = self.service.process()
        res = self.service.process()
        self.assertEqual(list(self.node_q), [])
        self.assertEqual(list(self.pub_q), ['deadbeef01', '20beefdead'])
        self.assertEqual(res, [self.node1, self.node2])

    def test_node_pub_addr(self):
        self.node_q.append(self.node1)
        self.node_q.append(self.node2)

        # Client side
        drain_msg_queue(self.node_q, self.pub_q, addr=self.addr)

        # server side
        res = self.service.process()
        res = self.service.process()
        self.assertEqual(list(self.node_q), [])
        self.assertEqual(list(self.pub_q), ['deadbeef01', '20beefdead'])
        self.assertEqual(res, [self.node1, self.node2])

    def test_node_pub_offline(self):
        self.node_q.append(self.node1)
        self.node_q.append(self.node2)
        self.node_q.append(self.node1)

        # Client side
        drain_msg_queue(self.node_q, addr=self.addr, method='offline')

        # server side
        res = self.service.process()
        res = self.service.process()
        res = self.service.process()
        # print(res)
        # print(list(self.node_q))
        # print(self.off_list)
        self.assertEqual(list(self.node_q), [])
        self.assertEqual(self.off_list, ['deadbeef01', '20beefdead'])
        self.assertEqual(res, [self.node1, self.node2])


class QueueHandlingTest(unittest.TestCase):
    """
    Test managing node queues.
    """
    def setUp(self):
        super(QueueHandlingTest, self).setUp()
        import diskcache as dc

        self.node_q = dc.Deque(directory='/tmp/test-nq')
        self.reg_q = dc.Deque(directory='/tmp/test-rq')
        self.wait_q = dc.Deque(directory='/tmp/test-wq')
        self.node1 = 'deadbeef01'
        self.node2 = '20beefdead'
        self.node3 = 'beef03dead'

    def tearDown(self):

        self.node_q.clear()
        self.reg_q.clear()
        self.wait_q.clear()
        super(QueueHandlingTest, self).tearDown()

    def test_clean_from_queue(self):
        self.node_q.append(self.node1)
        self.node_q.append(self.node2)
        self.node_q.append(self.node3)
        self.node_q.append(self.node1)

        clean_from_queue(self.node1, self.node_q)
        self.assertNotIn(self.node1, list(self.node_q))
        # print(list(self.node_q))

        self.dict1 = {self.node1: '127.0.0.1'}
        self.dict2 = {self.node2: '127.0.0.1'}
        self.node_q.append(self.dict1)
        self.node_q.append(self.dict2)
        self.node_q.append(self.dict1)
        self.node_q.append(self.dict2)
        res = lookup_node_id(self.node1, self.node_q)
        self.assertEqual(res, self.dict1)
        clean_from_queue(self.dict1, self.node_q)
        # print(list(self.node_q))
        res = lookup_node_id(self.node1, self.node_q)
        self.assertIsNone(res)
        self.assertNotIn(self.dict1, list(self.node_q))
        self.assertIn(self.dict2, list(self.node_q))

    def test_handle_node_queues(self):
        self.node_q.append(self.node1)
        self.node_q.append(self.node2)

        self.assertEqual(list(self.node_q), [self.node1, self.node2])
        self.assertEqual(list(self.reg_q), [])
        self.assertEqual(list(self.wait_q), [])

        handle_node_queues(self.node_q, self.wait_q)
        self.assertEqual(list(self.node_q), [])
        self.assertEqual(list(self.wait_q), [self.node1, self.node2])

    def test_manage_fpn_nodes(self):
        self.node_q.append(self.node1)
        self.node_q.append(self.node2)

        self.assertEqual(list(self.node_q), [self.node1, self.node2])
        self.assertEqual(list(self.reg_q), [])
        self.assertEqual(list(self.wait_q), [])

        # register node1
        self.reg_q.append(self.node1)

        manage_incoming_nodes(self.node_q, self.reg_q, self.wait_q)
        self.assertEqual(list(self.node_q), [])
        self.assertEqual(list(self.reg_q), [self.node1])
        self.assertEqual(list(self.wait_q), [self.node2])

        # register node2
        self.node_q.append(self.node2)
        self.reg_q.append(self.node2)

        manage_incoming_nodes(self.node_q, self.reg_q, self.wait_q)
        self.assertEqual(list(self.node_q), [])
        self.assertEqual(list(self.reg_q), [self.node1, self.node2])
        self.assertEqual(list(self.wait_q), [])

    def test_manage_other_nodes(self):
        self.assertFalse(check_return_status(self.node_q))
        self.node_q.append(self.node1)
        self.node_q.append(self.node2)

        self.assertEqual(list(self.node_q), [self.node1, self.node2])
        self.assertEqual(list(self.reg_q), [])
        self.assertEqual(list(self.wait_q), [])

        manage_incoming_nodes(self.node_q, self.reg_q, self.wait_q)
        self.assertEqual(list(self.node_q), [])
        self.assertEqual(list(self.reg_q), [])
        self.assertEqual(list(self.wait_q), [self.node1, self.node2])

        # unregistered nodes are still peers
        self.node_q.append(self.node1)
        self.node_q.append(self.node2)

        manage_incoming_nodes(self.node_q, self.reg_q, self.wait_q)
        self.assertEqual(list(self.node_q), [])
        self.assertEqual(list(self.reg_q), [])
        self.assertEqual(list(self.wait_q), [self.node1,
                                             self.node2,
                                             self.node1,
                                             self.node2])

        # node1 still not seen yet, late register from node2
        self.node_q.append(self.node1)
        self.node_q.append(self.node2)
        self.reg_q.append(self.node2)

        manage_incoming_nodes(self.node_q, self.reg_q, self.wait_q)
        self.assertEqual(list(self.node_q), [])
        self.assertEqual(list(self.reg_q), [self.node2])
        self.assertEqual(list(self.wait_q), [self.node1,
                                             self.node1,
                                             self.node1])

        # node2 registered and node1 expired
        manage_incoming_nodes(self.node_q, self.reg_q, self.wait_q)
        self.assertEqual(list(self.node_q), [])
        self.assertEqual(list(self.reg_q), [self.node2])
        self.assertEqual(list(self.wait_q), [])


class QueueMsgHandlingTest(unittest.TestCase):
    """
    Test announce msg handling/node queueing.
    """
    def setUp(self):
        super(QueueMsgHandlingTest, self).setUp()
        import diskcache as dc

        self.node_q = dc.Deque(directory='/tmp/test-nq')
        self.reg_q = dc.Deque(directory='/tmp/test-rq')
        self.wait_q = dc.Deque(directory='/tmp/test-wq')
        self.node1 = 'beef01dead'
        self.node2 = '02beefdead'
        self.node3 = 'deadbeef03'

    def tearDown(self):

        self.node_q.clear()
        self.reg_q.clear()
        self.wait_q.clear()
        super(QueueMsgHandlingTest, self).tearDown()

    def test_handle_msgs(self):
        self.assertEqual(list(self.node_q), [])
        self.node_q.append(self.node1)
        self.node_q.append(self.node2)
        self.node_q.append(self.node3)
        self.wait_q.append(self.node3)

        self.assertEqual(list(self.node_q), [self.node1, self.node2, self.node3])
        self.assertEqual(list(self.reg_q), [])
        self.assertEqual(list(self.wait_q), [self.node3])

        handle_announce_msg(self.node_q, self.reg_q, self.wait_q, self.node1)
        self.assertEqual(list(self.node_q), [self.node1, self.node2, self.node3])
        self.assertEqual(list(self.reg_q), [self.node1])
        self.assertEqual(list(self.wait_q), [self.node3])

        handle_announce_msg(self.node_q, self.reg_q, self.wait_q, self.node2)
        self.assertEqual(list(self.node_q), [self.node1, self.node2, self.node3])
        self.assertEqual(list(self.reg_q), [self.node1, self.node2])
        self.assertEqual(list(self.wait_q), [self.node3])

        # we now allow duplicate IDs in the reg queue
        handle_announce_msg(self.node_q, self.reg_q, self.wait_q, self.node3)
        self.assertIn(self.node3, list(self.reg_q))
        self.assertEqual(list(self.wait_q), [self.node3])
        # print(list(self.node_q), list(self.reg_q), list(self.wait_q))


class TrieHandlingTest(unittest.TestCase):
    """
    Test net_id cfg msg handling/node queueing.
    """
    def setUp(self):
        super(TrieHandlingTest, self).setUp()
        from node_tools import ctlr_data as ct

        self.trie = ct.id_trie
        self.node1 = ['beef01dead']
        self.node2 = ['beef02dead']
        self.node3 = ['beef03dead']
        self.node4 = '02beefdead'
        self.nodes = ['beef01dead', 'beef02dead']
        self.nodes2 = ['beef02dead', 'beef03dead']
        self.nodess = ['01beefdead', '02beefdead', '02beefdead']
        self.net1 = ['7ac4235ec5d3d938']
        self.net2 = ['7ac4235ec5d3d947']
        self.net3 = ['7ac4235ec53f3198']
        self.nets = ['7ac4235ec5d3d938', '7ac4235ec5d3d947']
        self.nets2 = ['7ac4235ec5d3d947', '7ac4235ec53f3198']
        self.netss = ['7ac4235ec5d3d938', '7ac4235ec5d3d947', 'bb8dead3c64dfb30']

    def tearDown(self):

        self.trie.clear()
        super(TrieHandlingTest, self).tearDown()

    def test_find_dangling_nets(self):
        update_id_trie(self.trie, self.net1, self.node1, [False, False])
        update_id_trie(self.trie, self.net2, self.nodes, [False, False], nw=True)
        res = find_dangling_nets(self.trie)
        self.assertEqual(res, [])
        update_id_trie(self.trie, self.net1, self.node1, [False, True], nw=True)
        res = find_dangling_nets(self.trie)
        self.assertEqual(len(res), 2)
        self.assertEqual(res, [self.net1[0], self.node1[0]])

    def test_update_id_trie_net(self):
        update_id_trie(self.trie, self.net1, [self.node2], nw=True)
        res = self.trie[self.net1[0]]
        self.assertIsInstance(res, tuple)
        self.assertEqual(len(res), 2)
        for thing in res:
            self.assertIsInstance(thing, list)
        self.assertEqual(res, ([self.node2], []))

    def test_update_id_trie_node(self):
        update_id_trie(self.trie, self.nets, self.node1)
        res = self.trie[self.node1[0]]
        self.assertIsInstance(res, tuple)
        self.assertEqual(len(res), 2)
        for thing in res:
            self.assertIsInstance(thing, list)
        self.assertEqual(res, (self.nets, []))

    def test_update_id_trie_none(self):
        with self.assertRaises(AssertionError):
            update_id_trie(self.trie, self.net1, self.node1, needs=[True])
            update_id_trie(self.trie, self.netss, self.node1)
            update_id_trie(self.trie, self.nets, self.node4)


class WaitForMsgHandlingTest(unittest.TestCase):
    """
    Test net_id cfg msg handling/node queueing.
    """
    def setUp(self):
        super(WaitForMsgHandlingTest, self).setUp()
        import diskcache as dc

        self.cfg_q = dc.Deque(directory='/tmp/test-aq')
        self.hold_q = dc.Deque(directory='/tmp/test-hq')
        self.reg_q = dc.Deque(directory='/tmp/test-rq')
        self.node1 = 'beef01dead'
        self.node2 = '02beefdead'
        self.node3 = 'deadbeef03'
        self.cfg1 = '{"node_id": "beef01dead", "networks": ["7ac4235ec5d3d938", "bb8dead3c63cea29"]}'
        self.cfg2 = '{"node_id": "02beefdead", "networks": ["7ac4235ec5d3d938"]}'

        self.cfg_q.append(self.cfg1)
        self.cfg_q.append(self.cfg2)

    def tearDown(self):

        self.reg_q.clear()
        self.cfg_q.clear()
        self.hold_q.clear()
        super(WaitForMsgHandlingTest, self).tearDown()

    def show_state(self):

        print('')
        print(list(self.cfg_q))
        print(list(self.hold_q))
        print(list(self.reg_q))

    def test_wait_for_cfg(self):
        import json
        self.assertIn(self.cfg1, self.cfg_q)
        res = wait_for_cfg_msg(self.cfg_q, self.hold_q, self.reg_q, self.node1)
        self.assertNotIn(self.cfg1, self.cfg_q)
        self.assertIsInstance(res, str)
        self.assertIn(self.node1, res)
        self.assertEqual(len(json.loads(res)['networks']), 2)

    def test_wait_for_cfg_none(self):
        import json
        res = wait_for_cfg_msg(self.cfg_q, self.hold_q, self.reg_q, self.node3)
        self.assertIsNone(res)
        self.assertIn(self.cfg2, self.cfg_q)
        res = wait_for_cfg_msg(self.cfg_q, self.hold_q, self.reg_q, self.node2)
        self.assertNotIn(self.cfg2, self.cfg_q)
        self.assertIn(self.node2, res)
        self.assertEqual(len(json.loads(res)['networks']), 1)
        res = wait_for_cfg_msg(self.cfg_q, self.hold_q, self.reg_q, self.node3)
        self.assertIsNone(res)
        self.assertEqual(len(self.hold_q), 3)
        res = wait_for_cfg_msg(self.cfg_q, self.hold_q, self.reg_q, self.node3)
        self.assertIsNone(res)
        self.assertEqual(len(self.hold_q), 0)
        self.assertEqual(len(self.reg_q), 1)
        self.assertIn(self.node3, list(self.reg_q))
        self.cfg_q.clear()
        res = wait_for_cfg_msg(self.cfg_q, self.hold_q, self.reg_q, self.node3)
        self.assertIsNone(res)
        self.assertEqual(len(self.hold_q), 1)
        self.assertIn(self.node3, list(self.hold_q))
