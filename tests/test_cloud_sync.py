import json
from biometrics import Store
from attendance import Attendance
from cloud_sync import CloudSync


def test_cloud_snapshot_omits_face_vectors_and_retries(tmp_path, monkeypatch):
    monkeypatch.delenv('IDENTIQ_CLOUD_URL', raising=False)
    store = Store(tmp_path / 'test.sqlite3')
    store.save_profile({'name': 'Example Student', 'person_type': 'student', 'admission_number': 'S1',
                        'grade': '7', 'section': 'A', 'academic_year': '2026'})
    sync = CloudSync(store, Attendance(store), lambda: None)
    sync.url, sync.key = 'https://example.workers.dev', 'test'
    sync.snapshot()
    sent = []
    def request(path, method='GET', data=None, **kwargs):
        if path == '/v1/commands':
            return {'commands': []}
        sent.append(data)
        raise OSError('offline')
    sync.request = request
    try:
        sync.tick()
    except OSError:
        pass
    assert sync.status()['pending'] > 0
    sync.request = lambda path, method='GET', data=None, **kwargs: {'commands': []} if path == '/v1/commands' else sent.append(data) or {'saved': True}
    sync.tick()
    assert sync.status()['pending'] == 0
    assert sent[0]['id'] == sent[1]['id']
    assert 'embeddings' not in json.dumps(sent)


def test_cloud_commands_use_local_validation_and_are_idempotent(tmp_path, monkeypatch):
    monkeypatch.delenv('IDENTIQ_CLOUD_URL', raising=False)
    store = Store(tmp_path / 'test.sqlite3')
    attendance = Attendance(store)
    sync = CloudSync(store, attendance, lambda: None)
    command = {'id': 'profile-1', 'kind': 'profile', 'data': {'name': 'Example', 'person_type': 'staff', 'employee_code': 'E1'}}
    first = sync.process_command(command)
    assert first['status'] == 'applied'
    assert sync.process_command(command) == first
    assert len(store.people()) == 1
    rejected = sync.process_command({'id': 'bad-1', 'kind': 'correction', 'data': {'session_id': 999, 'record_id': 1, 'status': 'present'}})
    assert rejected['status'] == 'rejected'
