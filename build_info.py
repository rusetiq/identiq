"""fingerprint shipped code only, independent of local databases and credentials"""
import hashlib
from pathlib import Path

ROOT=Path(__file__).resolve().parent


def installed_build(root=ROOT):
    root=Path(root)
    paths=list(root.glob('*.py'))+list((root/'templates').glob('*.html'))
    paths+=list((root/'static').glob('*.js'))+list((root/'static').glob('*.css'))
    paths+=list((root/'scripts').glob('*.py'))
    paths += [root/name for name in ('run-network.sh','run.sh','requirements.txt') if (root/name).is_file()]
    digest=hashlib.sha256()
    for path in sorted(paths,key=lambda value:value.relative_to(root).as_posix()):
        digest.update(path.relative_to(root).as_posix().encode()+b'\0')
        digest.update(path.read_bytes().replace(b'\r\n',b'\n')+b'\0')
    return digest.hexdigest()[:12]
