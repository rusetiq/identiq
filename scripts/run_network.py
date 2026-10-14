"""Serve Identiq over HTTPS on one private LAN address."""
import argparse
import ipaddress
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import ssl
from werkzeug.serving import make_server, ThreadedWSGIServer

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app import create_app


import shutil
import socket


class CameraHTTPSServer(ThreadedWSGIServer):
    """Keep slow TLS handshakes out of the listener's accept loop."""
    def __init__(self, host, port, app, certificate, key):
        super().__init__(host,port,app)
        self.ssl_context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.ssl_context.load_cert_chain(certificate,key)

    def process_request_thread(self, connection, address):
        try:
            connection.settimeout(8)
            connection=self.ssl_context.wrap_socket(connection,server_side=True)
            connection.settimeout(None)
        except (ssl.SSLError,OSError):
            connection.close();return
        super().process_request_thread(connection,address)


def lan_address(value=None):
    if value is None:
        if sys.platform == 'darwin':
            try:
                result = subprocess.run(['ipconfig', 'getifaddr', 'en0'], capture_output=True, text=True)
                value = result.stdout.strip()
            except Exception:
                value = None
        if not value:
            # Cross-platform socket discovery (works reliably on Windows, Linux, and macOS)
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                    sock.connect(('8.8.8.8', 80))
                    value = sock.getsockname()[0]
            except Exception:
                value = None
    try:
        address = ipaddress.IPv4Address(value)
    except (ipaddress.AddressValueError, TypeError) as error:
        raise ValueError('No private LAN IPv4 address found. Use --address with your private LAN IP.') from error
    if not address.is_private or address.is_loopback or address.is_link_local or address.is_unspecified:
        raise ValueError('Choose a private LAN IPv4 address.')
    return str(address)


def _find_openssl():
    cmd = shutil.which('openssl')
    if cmd:
        return cmd
    if sys.platform == 'win32':
        candidates = [
            Path(os.environ.get('ProgramFiles', 'C:\\Program Files')) / 'Git' / 'usr' / 'bin' / 'openssl.exe',
            Path(os.environ.get('ProgramFiles(x86)', 'C:\\Program Files (x86)')) / 'Git' / 'usr' / 'bin' / 'openssl.exe',
            Path(os.environ.get('LocalAppData', 'C:\\Users\\Default\\AppData\\Local')) / 'Programs' / 'Git' / 'usr' / 'bin' / 'openssl.exe',
            Path('C:\\Program Files\\OpenSSL-Win64\\bin\\openssl.exe'),
            Path('C:\\OpenSSL-Win64\\bin\\openssl.exe'),
            Path('C:\\Program Files (x86)\\OpenSSL-Win32\\bin\\openssl.exe'),
        ]
        for candidate in candidates:
            if candidate.exists():
                return str(candidate)
    return None


def certificate_for(address):
    directory = ROOT / 'data' / 'tls'
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        directory.chmod(0o700)
    except Exception:
        pass
    certificate = directory / f'lan-{address}.crt'
    key = directory / f'lan-{address}.key'
    if certificate.exists() and key.exists():
        try:
            key.chmod(0o600)
        except Exception:
            pass
        return certificate, key

    openssl_bin = _find_openssl()
    if not openssl_bin:
        raise RuntimeError(
            'openssl command not found. Install OpenSSL or Git for Windows, '
            'or ensure openssl.exe is in your PATH.'
        )

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
    with tempfile.NamedTemporaryFile(mode='w', suffix='.cnf', delete=False) as file:
        file.write(config)
        cnf_path = file.name

    try:
        old_mask = os.umask(0o077) if hasattr(os, 'umask') else None
        try:
            subprocess.run([openssl_bin, 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '365',
                            '-config', cnf_path, '-keyout', str(key), '-out', str(certificate)],
                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        finally:
            if old_mask is not None:
                os.umask(old_mask)
    finally:
        try:
            os.remove(cnf_path)
        except OSError:
            pass

    try:
        key.chmod(0o600)
    except Exception:
        pass
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
    print(f"App version: {app.config['IDENTIQ_BUILD']}",flush=True)
    if args.http:
        app.config['PORTAL_COOKIE_SUFFIX']='_http'
        local=make_server('127.0.0.1',args.port,app,threaded=True)
        standard=make_server(address,80,app,threaded=True) if args.standard_http else None
        secure=None
        if args.https_port:
            certificate,key=certificate_for(address)
            app.config['NETWORK_CERTIFICATE']=str(certificate)
            secure=CameraHTTPSServer(address,args.https_port,app,str(certificate),str(key))
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
        CameraHTTPSServer(address,args.port,app,str(certificate),str(key)).serve_forever()



if __name__ == '__main__':
    try:
        main()
    except (ValueError, subprocess.CalledProcessError) as error:
        print(f'Could not start network server: {error}', file=sys.stderr)
        sys.exit(1)
