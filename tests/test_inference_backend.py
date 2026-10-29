import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

import biometrics


class Session:
    def __init__(self, vector):
        self.vector = np.asarray(vector, dtype=np.float32)
    def get_inputs(self):
        return [SimpleNamespace(name='input')]
    def run(self, outputs, feed):
        return [self.vector.reshape(1, -1)]


def configure_mac(monkeypatch):
    monkeypatch.setattr(biometrics.platform, 'system', lambda: 'Darwin')
    monkeypatch.setattr(biometrics.platform, 'machine', lambda: 'arm64')
    monkeypatch.setattr(biometrics.ort, 'get_available_providers', lambda: ['CoreMLExecutionProvider', 'CPUExecutionProvider'])
    monkeypatch.delenv('IDENTIQ_INFERENCE_BACKEND', raising=False)


def test_accelerated_backend_must_match_cpu_before_use(tmp_path, monkeypatch):
    configure_mac(monkeypatch)
    model=tmp_path/'model.onnx';model.write_bytes(b'synthetic model')
    cpu=Session(np.arange(1, 513));accelerated=Session(np.arange(1, 513))
    monkeypatch.setattr(biometrics, '_cpu_recognizer', lambda path:cpu)
    observed=[]
    def create(path, sess_options, providers):
        observed.append(providers)
        return accelerated
    monkeypatch.setattr(biometrics.ort, 'InferenceSession', create)
    selected,enabled=biometrics._recognizer_for_device(model,tmp_path/'cache')
    assert selected is accelerated and enabled
    options=observed[0][0][1]
    assert options['ModelFormat']=='MLProgram'
    assert (tmp_path/'cache') in Path(options['ModelCacheDirectory']).parents


@pytest.mark.parametrize('failure', ['unavailable', 'inconsistent'])
def test_accelerated_backend_falls_back_when_not_usable(tmp_path, monkeypatch, failure):
    configure_mac(monkeypatch)
    model=tmp_path/'model.onnx';model.write_bytes(b'synthetic model')
    cpu=Session(np.arange(1, 513))
    monkeypatch.setattr(biometrics, '_cpu_recognizer', lambda path:cpu)
    def create(*args, **kwargs):
        if failure=='unavailable':raise RuntimeError('Cannot initialize backend')
        return Session(np.arange(512, 0, -1))
    monkeypatch.setattr(biometrics.ort, 'InferenceSession', create)
    selected,enabled=biometrics._recognizer_for_device(model,tmp_path/'cache')
    assert selected is cpu and not enabled


def test_cpu_override_avoids_accelerated_session(tmp_path, monkeypatch):
    configure_mac(monkeypatch)
    monkeypatch.setenv('IDENTIQ_INFERENCE_BACKEND','cpu')
    cpu=Session(np.arange(1, 513))
    monkeypatch.setattr(biometrics, '_cpu_recognizer', lambda path:cpu)
    monkeypatch.setattr(biometrics.ort, 'InferenceSession', lambda *a, **k:pytest.fail('Unexpected accelerated session'))
    assert biometrics._recognizer_for_device(tmp_path/'unused',tmp_path/'cache')==(cpu,False)


def test_invalid_accelerated_output_is_retried_on_cpu(monkeypatch):
    cpu=Session(np.arange(1, 513))
    monkeypatch.setattr(biometrics, '_cpu_recognizer', lambda path:cpu)
    engine=biometrics.FaceEngine.__new__(biometrics.FaceEngine)
    engine.recognizer=Session(np.zeros(512));engine._accelerated=True
    engine._model_path='model';engine._model_fallback_lock=threading.Lock()
    engine.recognizer_input='input';engine.recognizer_output='output'
    result=engine._run_recognizer(np.zeros((1,3,112,112)))
    assert engine.recognizer is cpu and not engine._accelerated
    assert np.array_equal(result,cpu.vector.reshape(1,-1))


def test_concurrent_runtime_failures_retry_on_one_cpu_session(monkeypatch):
    barrier=threading.Barrier(2)
    class FailingSession:
        def run(self, outputs, feed):
            barrier.wait(timeout=3)
            raise RuntimeError('Acceleration failed')
    cpu=Session(np.arange(1, 513));calls=[]
    monkeypatch.setattr(biometrics, '_cpu_recognizer', lambda path:(calls.append(path) or cpu))
    engine=biometrics.FaceEngine.__new__(biometrics.FaceEngine)
    engine.recognizer=FailingSession();engine._accelerated=True
    engine._model_path='model';engine._model_fallback_lock=threading.Lock()
    engine.recognizer_input='input';engine.recognizer_output='output'
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(engine._run_recognizer, [np.zeros((1,3,112,112))]*2))
    assert len(calls)==1 and engine.recognizer is cpu and not engine._accelerated
    assert all(np.array_equal(result,cpu.vector.reshape(1,-1)) for result in results)


def test_exact_face_input_cache_reuses_only_identical_inputs_and_expires(monkeypatch):
    from collections import OrderedDict
    engine = biometrics.FaceEngine.__new__(biometrics.FaceEngine)
    engine.recognizer = object()
    engine._embedding_cache = OrderedDict()
    engine._embedding_cache_lock = threading.Lock()
    calls = []
    engine._run_recognizer = lambda blob: calls.append(blob.copy()) or np.ones(512, dtype=np.float32)
    now = [100.0]
    monkeypatch.setattr(biometrics.time, 'monotonic', lambda:now[0])
    face = np.array([0, 0, 112, 112, *biometrics.ARCFACE_REFERENCE.flatten(), .99], dtype=np.float32)
    image = np.full((112,112,3), 100, dtype=np.uint8)
    first = engine._arcface_embedding(image, face)
    first[:] = 0  # Callers cannot corrupt another request's cached result.
    assert np.linalg.norm(engine._arcface_embedding(image, face)) == pytest.approx(1)
    assert len(calls) == 1
    changed = image.copy();changed[50,50,0] += 1
    engine._arcface_embedding(changed, face)
    assert len(calls) == 2
    now[0] += 1.51
    engine._arcface_embedding(image, face)
    assert len(calls) == 3
    assert len(engine._embedding_cache) == 1
    for value in range(90):
        engine._arcface_embedding(np.full_like(image,value), face)
    assert len(engine._embedding_cache) == biometrics.MAX_FACES * 4


def test_invalid_inference_is_never_cached():
    from collections import OrderedDict
    engine = biometrics.FaceEngine.__new__(biometrics.FaceEngine)
    engine.recognizer = object()
    engine._embedding_cache = OrderedDict()
    engine._embedding_cache_lock = threading.Lock()
    engine._run_recognizer = lambda blob: np.zeros(512, dtype=np.float32)
    face = np.array([0,0,112,112,*biometrics.ARCFACE_REFERENCE.flatten(),.99], dtype=np.float32)
    with pytest.raises(biometrics.FaceError):
        engine._arcface_embedding(np.full((112,112,3),100,dtype=np.uint8),face)
    assert not engine._embedding_cache
