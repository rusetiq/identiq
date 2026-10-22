"""Compare candidate selection using a disposable, synthetic identity directory.

This measures matching only, excluding camera, Wi-Fi and model inference.
"""
import argparse
import json
from pathlib import Path
import statistics
import sys
import tempfile
import time

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from biometrics import Store


def benchmark(identities=1000, faces=20, runs=30):
    rng = np.random.default_rng(42)
    bases = rng.normal(size=(identities,128)).astype(np.float32)
    bases /= np.linalg.norm(bases,axis=1,keepdims=True)
    samples = bases[:,None,:] + rng.normal(0,.02,(identities,5,128)).astype(np.float32)
    samples /= np.linalg.norm(samples,axis=2,keepdims=True)
    queries = bases[np.arange(faces) % identities]
    with tempfile.TemporaryDirectory(prefix='identiq-matching-') as directory:
        store = Store(Path(directory)/'synthetic.sqlite3')
        with store.connect() as db:
            db.executemany('INSERT INTO people(id,name) VALUES(?,?)',
                           [(i+1,f'Synthetic {i+1}') for i in range(identities)])
            db.executemany('INSERT INTO embeddings(person_id,vector) VALUES(?,?)',
                           [(i+1,sample.tobytes()) for i,group in enumerate(samples) for sample in group])
        full = store.candidates_batch(queries)
        compact = store.candidates_batch(queries,top_two=True)
        assert compact == [row[:2] for row in full], 'Optimized candidates differ from the full ranking.'
        timings = {False:[], True:[]}
        for run in range(runs):
            for top_two in (False,True) if run%2 == 0 else (True,False):
                started = time.perf_counter()
                store.candidates_batch(queries,top_two=top_two)
                timings[top_two].append((time.perf_counter()-started)*1000)
        full_ms = statistics.median(timings[False])
        compact_ms = statistics.median(timings[True])
    return {'phase':'cached matching only; excludes camera, Wi-Fi and inference',
            'identities':identities, 'embeddings':identities*5, 'faces':faces, 'runs':runs,
            'full_ranking_median_ms':round(full_ms,3), 'top_two_median_ms':round(compact_ms,3),
            'speedup':round(full_ms/compact_ms,2), 'same_top_two':True,
            'full_candidate_objects':sum(map(len,full)), 'top_two_candidate_objects':sum(map(len,compact))}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--identities',type=int,default=1000)
    parser.add_argument('--faces',type=int,default=20)
    parser.add_argument('--runs',type=int,default=30)
    args = parser.parse_args()
    if not 1 <= args.identities <= 20000 or not 1 <= args.faces <= 20 or not 1 <= args.runs <= 1000:
        parser.error('Use 1–20000 identities, 1–20 faces and 1–1000 runs.')
    print(json.dumps(benchmark(args.identities,args.faces,args.runs),indent=2))
