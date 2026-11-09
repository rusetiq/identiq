"""Store the Worker admin credential locally and install it as a Worker secret."""
import argparse
import json
import os
from pathlib import Path
import secrets
import subprocess
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('url', help='Deployed HTTPS Worker origin')
    args = parser.parse_args()
    url = args.url.rstrip('/')
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or not parsed.netloc or parsed.path or parsed.query or parsed.fragment or parsed.username:
        parser.error('Use an HTTPS origin without a path.')
    path = ROOT / 'data' / 'cloud-api.json'
    config = json.loads(path.read_text()) if path.exists() else {'admin_token': secrets.token_urlsafe(48)}
    if config.get('url') and config['url'] != url:
        parser.error('A different Worker is already configured. Back up the existing configuration first.')
    config['url'] = url
    # Save the secret before deployment so an interrupted CLI remains recoverable.
    fd = os.open(path, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
    os.chmod(path, 0o600)
    with os.fdopen(fd, 'w') as file:
        json.dump(config, file)
    subprocess.run(['npx', '--yes', 'wrangler@4', 'secret', 'put', 'ADMIN_TOKEN', '--config', str(ROOT/'cloudflare/wrangler.jsonc')],
                   input=config['admin_token']+'\n', text=True, check=True, cwd=ROOT)
    print('Cloud admin credential saved privately. Restart Identiq and use Cloud API to create keys.')


if __name__ == '__main__':
    main()
