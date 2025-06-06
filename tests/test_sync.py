import os

from unittest.mock import Mock, patch

from patroni import global_config
from patroni.collections import CaseInsensitiveSet
from patroni.dcs import Cluster, ClusterConfig, Status, SyncState
from patroni.postgresql import Postgresql

from . import BaseTestPostgresql, mock_available_gucs, psycopg_connect


@patch('subprocess.call', Mock(return_value=0))
@patch('patroni.psycopg.connect', psycopg_connect)
@patch.object(Postgresql, 'available_gucs', mock_available_gucs)
class TestSync(BaseTestPostgresql):

    @patch('subprocess.call', Mock(return_value=0))
    @patch('os.rename', Mock())
    @patch('patroni.postgresql.CallbackExecutor', Mock())
    @patch.object(Postgresql, 'get_major_version', Mock(return_value=140000))
    @patch.object(Postgresql, 'is_running', Mock(return_value=True))
    @patch.object(Postgresql, 'available_gucs', mock_available_gucs)
    def setUp(self):
        super(TestSync, self).setUp()
        self.p.config.write_postgresql_conf()
        self.s = self.p.sync_handler
        config = ClusterConfig(1, {'synchronous_mode': True}, 1)
        self.cluster = Cluster(True, config, self.leader, Status.empty(), [self.me, self.other, self.leadermem],
                               None, SyncState(0, self.me.name, self.leadermem.name, 0), None, None, None)
        global_config.update(self.cluster)

    @patch.object(Postgresql, 'last_operation', Mock(return_value=1))
    def test_pick_sync_standby(self):
        pg_stat_replication = [
            {'pid': 100, 'application_name': self.leadermem.name, 'sync_state': 'sync', 'flush_lsn': 1},
            {'pid': 101, 'application_name': self.me.name, 'sync_state': 'async', 'flush_lsn': 2},
            {'pid': 102, 'application_name': self.other.name, 'sync_state': 'async', 'flush_lsn': 2}]

        # sync node is a bit behind of async, but we prefer it anyway
        with patch.object(Postgresql, "_cluster_info_state_get", side_effect=[self.leadermem.name,
                                                                              'on', pg_stat_replication]):
            self.assertEqual(self.s.current_state(self.cluster), ('priority', 1,
                                                                  CaseInsensitiveSet([self.leadermem.name]),
                                                                  CaseInsensitiveSet([self.leadermem.name]),
                                                                  CaseInsensitiveSet([self.leadermem.name])))

        # prefer node with sync_state='potential', even if it is slightly behind of async
        pg_stat_replication[0]['sync_state'] = 'potential'
        for r in pg_stat_replication:
            r['write_lsn'] = r.pop('flush_lsn')
        with patch.object(Postgresql, "_cluster_info_state_get", side_effect=['', 'remote_write', pg_stat_replication]):
            self.assertEqual(self.s.current_state(self.cluster), ('off', 0, CaseInsensitiveSet(), CaseInsensitiveSet(),
                                                                  CaseInsensitiveSet([self.leadermem.name])))

        # when there are no sync or potential candidates we pick async with the minimal replication lag
        for i, r in enumerate(pg_stat_replication):
            r.update(replay_lsn=3 - i, application_name=r['application_name'].upper())
        missing = pg_stat_replication.pop(0)
        with patch.object(Postgresql, "_cluster_info_state_get", side_effect=['', 'remote_apply', pg_stat_replication]):
            self.assertEqual(self.s.current_state(self.cluster), ('off', 0, CaseInsensitiveSet(), CaseInsensitiveSet(),
                                                                  CaseInsensitiveSet([self.me.name])))

        # unknown sync node is ignored
        missing.update(application_name='missing', sync_state='sync')
        pg_stat_replication.insert(0, missing)
        with patch.object(Postgresql, "_cluster_info_state_get", side_effect=['', 'remote_apply', pg_stat_replication]):
            self.assertEqual(self.s.current_state(self.cluster), ('off', 0, CaseInsensitiveSet(), CaseInsensitiveSet(),
                                                                  CaseInsensitiveSet([self.me.name])))

        # invalid synchronous_standby_names and empty pg_stat_replication
        with patch.object(Postgresql, "_cluster_info_state_get", side_effect=['a b', 'remote_apply', None]):
            self.p._major_version = 90400
            self.assertEqual(self.s.current_state(self.cluster), ('off', 0, CaseInsensitiveSet(), CaseInsensitiveSet(),
                                                                  CaseInsensitiveSet()))

    @patch.object(Postgresql, 'last_operation', Mock(return_value=1))
    def test_current_state_quorum(self):
        self.cluster.config.data['synchronous_mode'] = 'quorum'
        global_config.update(self.cluster)

        pg_stat_replication = [
            {'pid': 100, 'application_name': self.leadermem.name, 'sync_state': 'quorum', 'flush_lsn': 1},
            {'pid': 101, 'application_name': self.me.name, 'sync_state': 'quorum', 'flush_lsn': 2}]

        # sync node is a bit behind of async, but we prefer it anyway
        with patch.object(Postgresql, "_cluster_info_state_get",
                          side_effect=['ANY 1 ({0},"{1}")'.format(self.leadermem.name, self.me.name),
                                       'on', pg_stat_replication]):
            self.assertEqual(self.s.current_state(self.cluster),
                             ('quorum', 1, CaseInsensitiveSet([self.me.name, self.leadermem.name]),
                              CaseInsensitiveSet([self.me.name, self.leadermem.name]),
                              CaseInsensitiveSet([self.leadermem.name, self.me.name])))

    @patch.object(Postgresql, 'last_operation', Mock(return_value=1))
    def test_current_state_cascading(self):
        pg_stat_replication = [
            {'pid': 100, 'application_name': self.me.name, 'sync_state': 'async', 'flush_lsn': 1},
            {'pid': 101, 'application_name': self.other.name, 'sync_state': 'sync', 'flush_lsn': 2}]

        # nodes that are supposed to replicate from other standby nodes are not
        # returned if at least one standby in a chain is streaming from primary
        self.leadermem.data['tags'] = {'replicatefrom': self.me.name}
        with patch.object(Postgresql, "_cluster_info_state_get",
                          side_effect=['2 ({0},"{1}")'.format(self.leadermem.name, self.other.name),
                                       'on', pg_stat_replication]):
            self.assertEqual(self.s.current_state(self.cluster),
                             ('priority', 2, CaseInsensitiveSet([self.other.name, self.leadermem.name]),
                              CaseInsensitiveSet(), CaseInsensitiveSet([self.me.name])))

    @patch('time.sleep', Mock())
    def test_set_sync_standby(self):
        def value_in_conf():
            with open(os.path.join(self.p.data_dir, 'postgresql.conf')) as f:
                for line in f:
                    if line.startswith('synchronous_standby_names'):
                        return line.strip()

        mock_reload = self.p.reload = Mock()
        self.s.set_synchronous_standby_names(CaseInsensitiveSet(['n1']))
        self.assertEqual(value_in_conf(), "synchronous_standby_names = 'n1'")
        mock_reload.assert_called()

        mock_reload.reset_mock()
        self.s.set_synchronous_standby_names(CaseInsensitiveSet(['n1']))
        mock_reload.assert_not_called()
        self.assertEqual(value_in_conf(), "synchronous_standby_names = 'n1'")

        mock_reload.reset_mock()
        self.s.set_synchronous_standby_names(CaseInsensitiveSet(['n1', 'n2']))
        mock_reload.assert_called()
        self.assertEqual(value_in_conf(), "synchronous_standby_names = '2 (n1,n2)'")

        mock_reload.reset_mock()
        self.s.set_synchronous_standby_names(CaseInsensitiveSet([]))
        mock_reload.assert_called()
        self.assertEqual(value_in_conf(), None)

        mock_reload.reset_mock()
        self.s.set_synchronous_standby_names(CaseInsensitiveSet('*'))
        mock_reload.assert_called()
        self.assertEqual(value_in_conf(), "synchronous_standby_names = '*'")

        self.cluster.config.data['synchronous_mode'] = 'quorum'
        global_config.update(self.cluster)
        mock_reload.reset_mock()
        self.s.set_synchronous_standby_names([], 1)
        mock_reload.assert_called()
        self.assertEqual(value_in_conf(), "synchronous_standby_names = 'ANY 1 (*)'")

        mock_reload.reset_mock()
        self.s.set_synchronous_standby_names(['any', 'b'], 1)
        mock_reload.assert_called()
        self.assertEqual(value_in_conf(), "synchronous_standby_names = 'ANY 1 (\"any\",b)'")

        mock_reload.reset_mock()
        self.s.set_synchronous_standby_names(['a', 'b'], 3)
        mock_reload.assert_called()
        self.assertEqual(value_in_conf(), "synchronous_standby_names = 'ANY 3 (a,b)'")

        self.p._major_version = 90601
        mock_reload.reset_mock()
        self.s.set_synchronous_standby_names([], 1)
        mock_reload.assert_called()
        self.assertEqual(value_in_conf(), "synchronous_standby_names = '1 (*)'")

        mock_reload.reset_mock()
        self.s.set_synchronous_standby_names(['a', 'b'], 1)
        mock_reload.assert_called()
        self.assertEqual(value_in_conf(), "synchronous_standby_names = '1 (a,b)'")

        mock_reload.reset_mock()
        self.s.set_synchronous_standby_names(['a', 'b'], 3)
        mock_reload.assert_called()
        self.assertEqual(value_in_conf(), "synchronous_standby_names = '3 (a,b)'")

        self.p._major_version = 90501
        mock_reload.reset_mock()
        self.s.set_synchronous_standby_names([], 1)
        mock_reload.assert_called()
        self.assertEqual(value_in_conf(), "synchronous_standby_names = '*'")

        mock_reload.reset_mock()
        self.s.set_synchronous_standby_names(['a-1'], 1)
        mock_reload.assert_called()
        self.assertEqual(value_in_conf(), "synchronous_standby_names = '\"a-1\"'")

    @patch.object(Postgresql, 'last_operation', Mock(return_value=1))
    def test_do_not_prick_yourself(self):
        self.p.name = self.leadermem.name
        cluster = Cluster(True, None, self.leader, 0, [self.me, self.other, self.leadermem], None,
                          SyncState(0, self.me.name, self.leadermem.name, 0), None, None, None)

        pg_stat_replication = [
            {'pid': 100, 'application_name': self.leadermem.name, 'sync_state': 'sync', 'flush_lsn': 1},
            {'pid': 101, 'application_name': self.me.name, 'sync_state': 'async', 'flush_lsn': 2},
            {'pid': 102, 'application_name': self.other.name, 'sync_state': 'async', 'flush_lsn': 2}]

        # Faulty case, application_name of the current primary is in synchronous_standby_names and in
        # the pg_stat_replication. We need to check that primary is not selected as the synchronous node.
        with patch.object(Postgresql, "_cluster_info_state_get", side_effect=[self.leadermem.name,
                                                                              'on', pg_stat_replication]):
            self.assertEqual(self.s.current_state(cluster), ('priority', 1, CaseInsensitiveSet([self.leadermem.name]),
                                                             CaseInsensitiveSet(), CaseInsensitiveSet([self.me.name])))
