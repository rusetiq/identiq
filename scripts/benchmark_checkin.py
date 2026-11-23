"""Time fresh check-ins against a temporary database with a supplied face photo."""
import argparse
import base64
import json
from pathlib import Path
import re
import statistics
import sys
import tempfile
import time
import uuid

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app import create_app
from biometrics import FaceEngine


def benchmark(path, runs):
    picture = cv2.imread(str(path))
    if picture is None:
        raise ValueError('Could not read the benchmark image.')
    engine = FaceEngine(ROOT / 'models')
    totals, frame_counts = [], []
    with tempfile.TemporaryDirectory() as directory:
        app = create_app(Path(directory) / 'benchmark.sqlite', engine)
        attendance = app.extensions['attendance']
        client = app.test_client()
        login = app.extensions['portal_access'].staff_login_path
        client.post('/api/staff/login', json=json.loads(login.read_text()),
                    headers={'X-Portal-Request': '1'})
        token = re.search(r'name="local-token" content="([^"]+)', client.get('/').text)[1]
        vector, _ = engine.extract(picture)
        attendance.store.enroll('Benchmark', [vector] * 5)
        for run in range(runs + 1):
            session = attendance.start('Benchmark')['id']
            scanner = str(uuid.uuid4())
            started = time.perf_counter()
            for index in range(100):
                image = cv2.convertScaleAbs(picture, alpha=1 + (run % 20)*.003, beta=index + run % 20)
                jpeg = cv2.imencode('.jpg', image)[1]
                result = client.post('/api/recognize/group', headers={'X-Local-Token': token}, json={
                    'image': 'data:image/jpeg;base64,' + base64.b64encode(jpeg).decode(),
                    'attendance_session_id': session, 'scanner_id': scanner, 'attendance_action': 'check_in',
                }).get_json()
                if any(face.get('attendance', {}).get('newly_moved') for face in result.get('faces', [])):
                    elapsed = 1000 * (time.perf_counter() - started)
                    break
            else:
                raise RuntimeError('This fixture did not produce a check-in.')
            assert len(attendance.detail(session)['movements']) == 1
            attendance.finish(session)
            if run:
                totals.append(elapsed)
                frame_counts.append(index + 1)
    return {'runs': runs, 'one_frame_saves': frame_counts.count(1),
            'median_ms': round(statistics.median(totals), 1),
            'p95_ms': round(float(np.percentile(totals, 95)), 1),
            'under_30_ms': sum(value < 30 for value in totals)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('image', type=Path)
    parser.add_argument('--runs', type=int, default=20)
    args = parser.parse_args()
    if not 1 <= args.runs <= 100:
        parser.error('Choose between 1 and 100 runs.')
    print(json.dumps(benchmark(args.image, args.runs)))
