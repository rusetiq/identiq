"""Compare serial and parallel ArcFace group inference using synthetic crops.

Uses the installed model, never the identity database or camera. Detection is
stubbed so measurements isolate quality checks, alignment and model inference.
"""
import argparse
import json
from pathlib import Path
import statistics
import sys
import time

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from biometrics import ARCFACE_REFERENCE, FaceEngine, MAX_FACES


def benchmark(runs=5):
    engine = FaceEngine(ROOT / 'models')
    # This benchmark measures new inference, never exact-input cache hits.
    engine._embedding_cache = None
    pool = engine._embedding_pool
    picture = np.random.default_rng(42).integers(0, 256, (640, 800, 3), dtype=np.uint8)
    boxes = []
    for index in range(MAX_FACES):
        x, y = (index % 5) * 160, (index // 5) * 160
        landmarks = ARCFACE_REFERENCE * (128 / 112) + [x, y]
        boxes.append([x, y, 128, 128, *landmarks.flatten(), .99])
    try:
        for count in (1, 4, 8, 20):
            detected = np.asarray(boxes[:count], dtype=np.float32)
            engine._detect = lambda *args, **kwargs: detected
            timings = {False: [], True: []}
            outputs = {}
            for run in range(runs + 1):
                for parallel in (False, True) if run % 2 == 0 else (True, False):
                    engine._embedding_pool = pool if parallel else None
                    started = time.perf_counter()
                    result = engine.extract_attendance_group(picture)
                    elapsed = (time.perf_counter() - started) * 1000
                    assert result['ready_count'] == count
                    outputs[parallel] = np.stack([face['embedding'] for face in result['faces']])
                    if run:
                        timings[parallel].append(elapsed)
                np.testing.assert_allclose(outputs[False], outputs[True], rtol=1e-6, atol=1e-6)
            serial_ms, parallel_ms = (statistics.median(timings[key]) for key in (False, True))
            print(json.dumps({'faces': count, 'workers': engine.embedding_workers, 'runs': runs,
                              'serial_ms': round(serial_ms, 2), 'parallel_ms': round(parallel_ms, 2),
                              'speedup': round(serial_ms / parallel_ms, 2),
                              'max_embedding_difference': float(np.max(np.abs(outputs[False] - outputs[True])))}), flush=True)
    finally:
        pool.shutdown(wait=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runs', type=int, default=5)
    args = parser.parse_args()
    if not 1 <= args.runs <= 100:
        parser.error('--runs must be between 1 and 100')
    benchmark(args.runs)
