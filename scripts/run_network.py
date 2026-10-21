"""Serve Identiq over HTTPS on one private LAN address."""
import argparse
import ipaddress
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import ssl
from werkzeug.serving import make_server, ThreadedWSGIServer

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


import shutil
import socket

NETWORK_SETTINGS=ROOT/'data'/'network.json'


def create_app(**options):
    # The supervisor only needs network settings, not a second OpenCV runtime.
    from app import create_app as factory
    return factory(**options)


def configured_address(value=None, *, auto=False, pinned=None, settings_path=None):
    """An explicit or remembered static address wins over interface discovery."""
    if value is not None:return lan_address(value)
    if pinned is not None:return lan_address(pinned)
    path=Path(settings_path) if settings_path is not None else NETWORK_SETTINGS
    if not auto and path.exists():
        try:
            saved=json.loads(path.read_text())
            if not isinstance(saved,dict) or not isinstance(saved.get('address'),str):raise ValueError('Expected an address string.')
            return lan_address(saved['address'])
        except (ValueError,KeyError,TypeError,OSError) as error:
            raise ValueError('Saved network address is invalid. Use --address YOUR-STATIC-IP or --auto-address.') from error
    return lan_address()


def remember_address(address, settings_path=None):
    address=lan_address(address)
    path=Path(settings_path) if settings_path is not None else NETWORK_SETTINGS
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    temporary=None
    try:
        with tempfile.NamedTemporaryFile(mode='w',dir=path.parent,delete=False) as file:
            temporary=Path(file.name)
            json.dump({'address':address},file);file.write('\n')
        temporary.chmod(0o600);os.replace(temporary,path)
    finally:
        if temporary and temporary.exists():temporary.unlink()


class NetworkListeners:
    """Treat all advertised listeners as one service, including HTTPS."""
    def __init__(self):self.servers=[];self.threads=[]

    def add(self,name,server):
        self.servers.append((name,server));return server

    def start(self):
        for name,server in self.servers:
            thread=threading.Thread(target=server.serve_forever,kwargs={'poll_interval':.2},name=name,daemon=True)
            thread.start();self.threads.append((name,thread))
        self.check()

    def check(self):
        for name,thread in self.threads:
            if not thread.is_alive():raise RuntimeError(f'{name} listener stopped. Restarting the network service.')

    def wait(self):
        while True:self.check();time.sleep(.5)

    def close(self):
        for index,(_,server) in enumerate(self.servers):
            if index<len(self.threads) and self.threads[index][1].is_alive():server.shutdown()
            server.server_close()
        for _,thread in self.threads:thread.join(timeout=2)


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
    address_options=parser.add_mutually_exclusive_group()
    address_options.add_argument('--address', help='Bind to and remember this laptop’s static private IPv4 address.')
    address_options.add_argument('--auto-address',action='store_true',help='Forget a saved static address and discover the current interface.')
    parser.add_argument('--worker',action='store_true',help=argparse.SUPPRESS)
    parser.add_argument('--port', type=int)
    parser.add_argument('--http', action='store_true', help='Serve plain HTTP on the LAN, plus a localhost camera-source listener.')
    parser.add_argument('--standard-http', action='store_true', help='Also serve the same HTTP app on port 80 for devices that block custom ports.')
    parser.add_argument('--https-port', type=int, help='Alongside HTTP, serve the same shared app over HTTPS for camera capture on other devices.')
    args = parser.parse_args()
    address=configured_address(args.address,auto=args.auto_address,pinned=os.environ.get('_IDENTIQ_BIND_ADDRESS'))
    args.port=args.port if args.port is not None else (8080 if args.http else 8443)
    if args.http and args.https_port is None:args.https_port=8443
    if not 1024 <= args.port <= 65535:
        parser.error('Use a port between 1024 and 65535.')
    if args.standard_http and not args.http:
        parser.error('--standard-http requires --http.')
    if args.https_port is not None and (not args.http or not 1024<=args.https_port<=65535 or args.https_port==args.port):
        parser.error('--https-port needs --http and a separate port between 1024 and 65535.')
    app=create_app(allowed_hosts={address})
    print(f"App version: {app.config['IDENTIQ_BUILD']}",flush=True)
    listeners=NetworkListeners()
    try:
        if args.http:
            app.config['PORTAL_COOKIE_SUFFIX']='_http'
            listeners.add('Local HTTP',make_server('127.0.0.1',args.port,app,threaded=True))
            listeners.add('Network HTTP',make_server(address,args.port,app,threaded=True))
            if args.standard_http:listeners.add('Standard HTTP',make_server(address,80,app,threaded=True))
            certificate,key=certificate_for(address)
            app.config['NETWORK_CERTIFICATE']=str(certificate)
            listeners.add('Tablet HTTPS',CameraHTTPSServer(address,args.https_port,app,str(certificate),str(key)))
        else:
            certificate,key=certificate_for(address)
            app.config['NETWORK_CERTIFICATE']=str(certificate)
            listeners.add('Network HTTPS',CameraHTTPSServer(address,args.port,app,str(certificate),str(key)))
        listeners.start()
        if args.address:
            remember_address(address)
            print('Static address saved in data/network.json.',flush=True)
        elif args.auto_address:NETWORK_SETTINGS.unlink(missing_ok=True)
        print(f'Listening on server address {address}. Other IPs at the start of request logs identify visiting devices.',flush=True)
        if args.http:
            print(f'Tablet camera address: https://{address}:{args.https_port}/',flush=True)
            print(f'Network staff / parent address: http://{address}:{args.port}/',flush=True)
            print(f'Laptop camera source: http://localhost:{args.port}/',flush=True)
            print(f'Certificate download: http://{address}:{args.port}/network-certificate',flush=True)
            print('Use HTTPS on the HTTPS port only. HTTP logins and previews are unencrypted.',flush=True)
            if args.standard_http:print(f'Standard HTTP address: http://{address}/',flush=True)
        else:
            print(f'Tablet / staff address: https://{address}:{args.port}/',flush=True)
            print(f'Public certificate to trust: {certificate}',flush=True)
        listeners.wait()
    except KeyboardInterrupt:
        print('Network server stopped.',flush=True)
    finally:listeners.close()



if __name__ == '__main__':
    try:
        if '--worker' in sys.argv or '--help' in sys.argv or '-h' in sys.argv:main()
        else:
            from watch_network import main as supervised_main
            sys.exit(supervised_main())
    except (ValueError, RuntimeError, OSError, subprocess.CalledProcessError) as error:
        print(f'Could not start network server: {error}', file=sys.stderr)
        sys.exit(1)
