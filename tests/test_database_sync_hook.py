"""Storage adapters can reconcile concurrent identities atomically."""
from flops_agent.entities.session import Session
from flops_agent.seams.database import InMemoryDatabase, sync_session


def test_adapter_sync_owns_the_entire_read_modify_write():
    calls = []
    class ConcurrentDatabase:
        def synchronize_session(self, session, *, keys=None):
            calls.append((session, keys))
            return 3
        def count_messages(self, *args, **kwargs):
            raise AssertionError('count-based diff must not run outside the adapter transaction')
    session = Session('session')
    keys = {'example': 'key'}
    assert sync_session(ConcurrentDatabase(), session, keys=keys) == 3
    assert calls == [(session, keys)]


def test_reference_database_retains_legacy_append_and_truncate():
    database = InMemoryDatabase()
    session = Session('session', messages=[{'role': 'user', 'content': 'hello'}])
    database.create_session('session')
    assert sync_session(database, session) == 1
    session.messages.clear()
    assert sync_session(database, session) == -1
