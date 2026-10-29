"""One in-memory camera source, latest-frame delivery and authenticated staff controls."""
import io
import secrets
import threading
import time
import uuid
from PIL import Image, UnidentifiedImageError
from biometrics import FaceError


class SharedCamera:
    def __init__(self, attendance, clock=time.monotonic):
        self.attendance=attendance;self.clock=clock;self.lock=threading.RLock()
        self.clients=set();self.source=None;self.source_key=None;self.ready=False
        self.running=False;self.session_id=None;self.scanner_id=None;self.epoch=0
        self.action='automatic';self.axis='x';self.direction='positive'
        self.armed=False;self.lease=None;self.retired_lease=None;self.resumed=False
        self.revision=0;self.attendance_revision=0;self.frame=None;self.frame_sequence=0;self.frame_time=0

    def connect(self):
        with self.lock:
            if len(self.clients)>=32:raise FaceError('shared_full','Too many staff tabs are connected.')
            client=str(uuid.uuid4());self.clients.add(client);self.revision+=1;return client

    def disconnect(self, client):
        with self.lock:
            self.clients.discard(client)
            if self.source==client:
                self.lease={'key':self.source_key,'armed':self.armed,'session_id':self.session_id,'expires':self.clock()+30}
                self._release()
            self.revision+=1

    def _release(self):
        self.source=None;self.source_key=None;self.ready=False;self.running=False;self.armed=False;self.session_id=None
        self.frame=None;self.frame_sequence+=1;self.epoch+=1

    def _expire(self):
        if self.lease and self.clock()>self.lease['expires']:
            self.retired_lease=self.lease;self.lease=None;self.revision+=1
        if self.ready and self.clock()-self.frame_time>5:
            self.ready=False;self.running=False;self.frame=None;self.frame_sequence+=1;self.epoch+=1;self.revision+=1

    def snapshot(self, client):
        with self.lock:
            self._expire()
            data={'type':'state','revision':self.revision,'attendance_revision':self.attendance_revision,
                  'client_id':client,'source_id':self.source,'ready':self.ready,'running':self.running,
                  'session_id':self.session_id,'scanner_id':self.scanner_id,'epoch':self.epoch,
                  'action':self.action,'axis':self.axis,'direction':self.direction,'staff_tabs':len(self.clients)}
            data.update(armed=self.armed or bool(self.lease and self.lease['armed']),recovering=bool(self.lease),resumed=self.resumed,viewers=max(0,len(self.clients)-(1 if self.source else 0)))
            if client==self.source:data['source_key']=self.source_key
            return data

    def command(self, client, message):
        kind=message.get('type')
        active=self.attendance.active_session_id() if kind=='start' else None
        with self.lock:
            self._expire()
            if client not in self.clients:raise FaceError('shared_missing','Reconnect to the shared camera.')
            if kind=='claim':
                if self.source and self.source!=client:raise FaceError('source_busy','Another staff tab is already sharing its camera.')
                if self.source!=client:
                    lease=self.lease
                    if lease and (not isinstance(message.get('resume_key'),str) or not secrets.compare_digest(message['resume_key'],lease['key'])):
                        raise FaceError('source_reconnecting','The camera is reconnecting. Please wait a moment.')
                    if not lease and self.retired_lease and isinstance(message.get('resume_key'),str) and secrets.compare_digest(message['resume_key'],self.retired_lease['key']):
                        lease=self.retired_lease
                    options=self.attendance.movement_options(message.get('action',self.action),message.get('axis',self.axis),message.get('direction',self.direction))
                    self._release();self.source=client;self.source_key=secrets.token_urlsafe(32)
                    self.resumed=bool(lease)
                    self.retired_lease=None
                    if lease:
                        self.armed=lease['armed'];self.session_id=lease['session_id'];self.lease=None
                    else:self.action,self.axis,self.direction=options
            elif kind=='release':
                if client!=self.source:raise FaceError('source_required','Only the camera source can stop sharing.')
                self.lease=None;self._release()
            elif kind=='start':
                if not self.source or not self.ready:raise FaceError('source_missing','Enable and share a camera from the laptop first.')
                if active is None:raise FaceError('session_missing','Start an attendance session first.')
                if message.get('session_id') is not None and message['session_id']!=active:
                    raise FaceError('session_closed','The previous attendance session has ended.')
                if self.running:return
                self.running=self.armed=True;self.session_id=active;self.scanner_id=str(uuid.uuid4());self.epoch+=1
            elif kind=='pause':
                self.running=self.armed=False;self.epoch+=1
                if self.lease:self.lease['armed']=False
                if self.retired_lease:self.retired_lease['armed']=False
            elif kind=='configure':
                action,axis,direction=self.attendance.movement_options(message.get('action'),message.get('axis'),message.get('direction'))
                self.action=action;self.axis=axis;self.direction=direction;self.running=self.armed=False;self.epoch+=1
                if self.lease:self.lease['armed']=False
                if self.retired_lease:self.retired_lease['armed']=False
            else:raise FaceError('invalid_shared_command','Choose a valid shared camera action.')
            self.revision+=1

    def publish(self, client, frame):
        with self.lock:
            if client!=self.source:raise FaceError('source_required','Only the camera source can publish frames.')
            if self.clock()-self.frame_time<.15 and self.ready:return
            if not isinstance(frame,bytes) or not 4<=len(frame)<=350000:
                raise FaceError('invalid_frame','Shared frame size is invalid.')
            try:
                with Image.open(io.BytesIO(frame)) as image:
                    if image.format!='JPEG' or not 1<=image.width<=1280 or not 1<=image.height<=1280:
                        raise FaceError('invalid_frame','Send a small JPEG preview.')
                    image.verify()
            except (UnidentifiedImageError,OSError,ValueError) as error:
                raise FaceError('invalid_frame','Shared preview is not a valid JPEG.') from error
            self.frame=frame;self.frame_sequence+=1;self.frame_time=self.clock()
            if not self.ready:
                self.ready=True;self.revision+=1
                if self.armed:
                    self.running=True;self.scanner_id=str(uuid.uuid4());self.epoch+=1

    def latest(self):
        with self.lock:
            self._expire();return self.frame_sequence,self.frame

    def guard(self, data):
        """Caller holds the lock through the attendance write to reject late/duplicate producers."""
        self._expire()
        if self.source is None:
            if self.lease or data.get('shared_key') is not None:raise FaceError('shared_scan_stopped','The shared camera disconnected.')
            return
        key=data.get('shared_key')
        if (not self.running or not isinstance(key,str) or not secrets.compare_digest(key,self.source_key)
            or data.get('shared_epoch')!=self.epoch or data.get('attendance_session_id')!=self.session_id
            or data.get('scanner_id')!=self.scanner_id):
            raise FaceError('shared_scan_stopped','Use the active shared camera source for this scan.')
        data.update(attendance_action=self.action,crossing_axis=self.axis,entry_direction=self.direction)

    def attendance_changed(self):
        active=self.attendance.active_session_id()
        with self.lock:
            self.attendance_revision+=1
            if self.armed and self.session_id!=active:
                self.running=self.armed=False;self.epoch+=1;self.revision+=1
            if self.lease and self.lease['session_id']!=active:self.lease['armed']=False
            if self.retired_lease and self.retired_lease['session_id']!=active:self.retired_lease['armed']=False
