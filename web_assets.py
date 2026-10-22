"""Small, frozen public assets; private pages and records are never cached."""
import base64
import gzip
import hashlib

from flask import Response, request


class WebAssets:
    def __init__(self, directory, build):
        self.build = build
        self.files = {}
        for path in sorted(directory.iterdir()):
            if not path.is_file() or path.suffix not in ('.js','.css'):
                continue
            raw = path.read_bytes()
            packed = gzip.compress(raw,compresslevel=6,mtime=0)
            self.files[path.name] = {
                'raw':raw, 'gzip':packed if len(packed)<len(raw) else None,
                'mime':'text/css' if path.suffix=='.css' else 'text/javascript',
                'etag':hashlib.sha256(raw).hexdigest(),
            }
        # Deliver styling with the page itself, so a separate failed CSS download
        # cannot leave a usable HTML page unstyled on a roaming tablet.
        self.stylesheet = self.files['style.css']['raw'].decode('utf-8').replace('\r\n','\n')
        self.style_hash = base64.b64encode(hashlib.sha256(self.stylesheet.encode()).digest()).decode()

    def contains(self, filename):
        return filename in self.files

    @staticmethod
    def accepts_gzip():
        return request.accept_encodings.best_match(['gzip','identity'],default='identity') == 'gzip'

    def serve(self, filename):
        asset = self.files[filename]
        compressed = asset['gzip'] is not None and self.accepts_gzip()
        response = Response(asset['gzip'] if compressed else asset['raw'],mimetype=asset['mime'])
        response.headers['Vary'] = 'Accept-Encoding'
        if compressed:
            response.headers['Content-Encoding'] = 'gzip'
        response.set_etag(asset['etag'] + ('-gzip' if compressed else '-identity'))
        response.headers['Cache-Control'] = ('public, max-age=31536000, immutable'
            if request.args.get('v') == self.build else 'no-cache, max-age=0, must-revalidate')
        return response.make_conditional(request)

    def compress_page(self, response):
        if (response.status_code != 200 or response.mimetype != 'text/html' or response.is_streamed
                or 'Content-Encoding' in response.headers):
            return
        response.vary.add('Accept-Encoding')
        if self.accepts_gzip():
            raw = response.get_data()
            packed = gzip.compress(raw,compresslevel=6,mtime=0)
            if len(packed)<len(raw):
                response.set_data(packed)
                response.headers['Content-Encoding'] = 'gzip'
