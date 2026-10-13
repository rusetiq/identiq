"""Serve Identiq over HTTPS on one private LAN address."""
import argparse
import ipaddress
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from werkzeug.serving import make_server

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app import create_app


def lan_address(value=None):
    if value is None:
        if sys.platform == 'darwin':
            result = subprocess.run(['ipconfig', 'getifaddr', 'en0'], capture_output=True, text=True)
            value = result.stdout.strip()
        if not value:
            # Cross-platform fallback for Windows, Linux, or secondary macOS interfaces
            import socket
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                    sock.connect(('8.8.8.8', 80))
                    value = sock.getsockname()[0]
            except Exception:
                value = ''
    try:
        address = ipaddress.IPv4Address(value)
    except ipaddress.AddressValueError as error:
        raise ValueError('No private LAN IPv4 address found. Use --address with your private LAN IP.') from error
    if not address.is_private or address.is_loopback or address.is_link_local or address.is_unspecified:
        raise ValueError('Choose a private LAN IPv4 address.')
    return str(address)


def certificate_for(address):
    directory = ROOT / 'data' / 'tls'
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)
    certificate = directory / f'lan-{address}.crt'
    key = directory / f'lan-{address}.key'
    if certificate.exists() and key.exists():
        key.chmod(0o600)
        return certificate, key
    config = f'''[req]
prompt = no
distinguished_name = dn
x509_extensions = extensions
[dn]
CN = Identiq local network
[extensions]
basicConstraints = critical, CA:TRUE
keyUsage = critical, digitalSignature, keyEncipherment, keyCertSign
extendedKeyUsage = serverAuth
subjectAltName = IP:{address},IP:127.0.0.1,DNS:localhost
'''
    with tempfile.NamedTemporaryFile(mode='w', suffix='.cnf') as file:
        file.write(config)
        file.flush()
        old_mask = os.umask(0o077)
        try:
            subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '365',
                            '-config', file.name, '-keyout', str(key), '-out', str(certificate)],
                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        finally:
            os.umask(old_mask)
    key.chmod(0o600)
    return certificate, key


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--address', help='Private IPv4 address of this laptop; defaults to Wi-Fi en0.')
    parser.add_argument('--port', type=int)
    parser.add_argument('--http', action='store_true', help='Serve plain HTTP on the LAN, plus a localhost camera-source listener.')
    parser.add_argument('--standard-http', action='store_true', help='Also serve the same HTTP app on port 80 for devices that block custom ports.')
    parser.add_argument('--https-port', type=int, help='Alongside HTTP, serve the same shared app over HTTPS for camera capture on other devices.')
    args = parser.parse_args()
    address = lan_address(args.address)
    args.port=args.port or (8080 if args.http else 8443)
    if args.http and args.https_port is None:args.https_port=8443
    if not 1024 <= args.port <= 65535:
        parser.error('Use a port between 1024 and 65535.')
    if args.standard_http and not args.http:
        parser.error('--standard-http requires --http.')
    if args.https_port and (not args.http or not 1024<=args.https_port<=65535 or args.https_port==args.port):
        parser.error('--https-port needs --http and a separate port between 1024 and 65535.')
    app=create_app(allowed_hosts={address})
    if args.http:
        app.config['PORTAL_COOKIE_SUFFIX']='_http'
        local=make_server('127.0.0.1',args.port,app,threaded=True)
        standard=make_server(address,80,app,threaded=True) if args.standard_http else None
        secure=None
        if args.https_port:
            certificate,key=certificate_for(address)
            app.config['NETWORK_CERTIFICATE']=str(certificate)
            secure=make_server(address,args.https_port,app,threaded=True,ssl_context=(str(certificate),str(key)))
        threading.Thread(target=local.serve_forever,daemon=True).start()
        if standard:
            threading.Thread(target=standard.serve_forever,daemon=True).start()
            print(f'Standard HTTP address: http://{address}/',flush=True)
        if secure:
            threading.Thread(target=secure.serve_forever,daemon=True).start()
            print(f'Tablet camera address: https://{address}:{args.https_port}/',flush=True)
            print(f'Public certificate download: http://{address}:{args.port}/network-certificate',flush=True)
        print(f'Shared camera source on this laptop: http://localhost:{args.port}/',flush=True)
        print(f'Network staff area: http://{address}:{args.port}/',flush=True)
        print(f'Parent portal: http://{address}:{args.port}/parents',flush=True)
        print('HTTP logins and the shared feed are unencrypted. Camera capture is available on the localhost source page.',flush=True)
        try:app.run(host=address,port=args.port,debug=False,threaded=True)
        finally:
            local.shutdown()
            if standard:standard.shutdown()
            if secure:secure.shutdown()
    else:
        certificate,key=certificate_for(address)
        print(f'Network staff area: https://{address}:{args.port}/',flush=True)
        print(f'Parent portal: https://{address}:{args.port}/parents',flush=True)
        print(f'Public certificate to trust on your devices: {certificate}',flush=True)
        app.run(host=address,port=args.port,debug=False,threaded=True,ssl_context=(str(certificate),str(key)))



if __name__ == '__main__':
    try:
        main()
    except (ValueError, subprocess.CalledProcessError) as error:
        print(f'Could not start network server: {error}', file=sys.stderr)
        sys.exit(1)
