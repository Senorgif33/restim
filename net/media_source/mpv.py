import json
import logging
import os
import sys
import time

from PySide6 import QtCore
from PySide6.QtCore import QUrl
from PySide6.QtNetwork import QLocalSocket

from net.media_source.mediasource import MediaSource, MediaStatusReport, MediaConnectionState
from qt_ui import settings

logger = logging.getLogger('restim.media.mpv')

REQ_PATH = 1
REQ_WORKING_DIR = 2
REQ_PAUSE = 3
REQ_TIME = 4
REQ_SPEED = 5
REQ_IDLE = 6


def local_socket_name(ipc_path: str) -> str:
    """
    Convert an mpv --input-ipc-server value into a QLocalSocket server name.
    On Windows, strip the \\\\.\\pipe\\ prefix if present.
    """
    if sys.platform == 'win32':
        prefixes = ('\\\\.\\pipe\\', '//./pipe/')
        lower = ipc_path.lower()
        for prefix in prefixes:
            if lower.startswith(prefix.lower()):
                return ipc_path[len(prefix):]
    return ipc_path


def resolve_media_path(path: str, working_directory: str | None) -> str:
    if not path:
        return ''

    url = QUrl(path)
    if url.isLocalFile():
        path = url.toLocalFile()

    if os.path.isabs(path):
        return path

    if working_directory:
        return os.path.normpath(os.path.join(working_directory, path))

    return path


class MPV(MediaSource):
    def __init__(self, parent):
        super(MPV, self).__init__(parent)

        self._enabled = False
        self.socket: QLocalSocket | None = None
        self._buffer = b''

        self.path = ''
        self.working_directory = None
        self.paused = True
        self.idle = True
        self.speed = 1.0
        self.time_pos = 0.0

        self.poll_timer = QtCore.QTimer(self)
        self.poll_timer.timeout.connect(self.poll)
        self.poll_timer.setSingleShot(False)
        self.poll_timer.setInterval(100)

        self.reconnect_timer = QtCore.QTimer(self)
        self.reconnect_timer.timeout.connect(self.reconnect_timeout)
        self.reconnect_timer.setSingleShot(False)
        self.reconnect_timer.setInterval(2000)

    def open_connection(self):
        if self.socket is not None:
            self.socket.abort()
            self.socket.deleteLater()
            self.socket = None

        ipc_path = settings.media_sync_mpv_ipc_path.get()
        name = local_socket_name(ipc_path)
        logger.info('connecting to mpv ipc: %s', name)

        self.socket = QLocalSocket(self)
        self.socket.connected.connect(self.on_connected)
        self.socket.errorOccurred.connect(self.on_error)
        self.socket.disconnected.connect(self.on_disconnected)
        self.socket.readyRead.connect(self.on_ready_read)
        self.socket.connectToServer(name)

    def on_connected(self):
        logger.info('connected')
        self._buffer = b''
        self.set_state(
            MediaStatusReport(
                time.time(),
                '',
                MediaConnectionState.CONNECTED_BUT_NO_FILE_LOADED,
            )
        )
        self.poll_timer.start()
        self.poll()

    def on_disconnected(self):
        logger.info('disconnected')
        self.poll_timer.stop()
        self.path = ''
        self.working_directory = None
        self.paused = True
        self.idle = True
        self.speed = 1.0
        self.time_pos = 0.0
        self.set_state(MediaStatusReport(time.time()))

    def on_error(self, _error):
        if self.socket is not None:
            logger.debug('socket error: %s', self.socket.errorString())
        self.poll_timer.stop()
        self.set_state(
            MediaStatusReport(
                time.time(),
                self.socket.errorString() if self.socket else '',
                MediaConnectionState.NOT_CONNECTED,
            )
        )

    def reconnect_timeout(self):
        if not self._enabled:
            return
        if self.socket is None or self.socket.state() == QLocalSocket.LocalSocketState.UnconnectedState:
            self.open_connection()

    def send_command(self, command, request_id: int):
        if self.socket is None or self.socket.state() != QLocalSocket.LocalSocketState.ConnectedState:
            return
        payload = json.dumps({'command': command, 'request_id': request_id}) + '\n'
        self.socket.write(payload.encode('utf-8'))

    def poll(self):
        if not self._enabled:
            return
        self.send_command(['get_property', 'path'], REQ_PATH)
        self.send_command(['get_property', 'working-directory'], REQ_WORKING_DIR)
        self.send_command(['get_property', 'pause'], REQ_PAUSE)
        self.send_command(['get_property', 'time-pos'], REQ_TIME)
        self.send_command(['get_property', 'speed'], REQ_SPEED)
        self.send_command(['get_property', 'idle-active'], REQ_IDLE)

    def on_ready_read(self):
        if self.socket is None:
            return

        self._buffer += bytes(self.socket.readAll())
        while True:
            newline = self._buffer.find(b'\n')
            if newline < 0:
                break
            line = self._buffer[:newline]
            self._buffer = self._buffer[newline + 1:]
            if not line:
                continue
            try:
                msg = json.loads(line.decode('utf-8'))
            except (UnicodeDecodeError, json.JSONDecodeError):
                logger.debug('unable to parse mpv reply: %r', line)
                continue
            self.handle_message(msg)

        self.emit_status()

    def handle_message(self, msg: dict):
        if 'event' in msg:
            return

        request_id = msg.get('request_id')
        error = msg.get('error', 'success')
        data = msg.get('data')

        if error != 'success':
            if request_id == REQ_PATH:
                self.path = ''
            elif request_id == REQ_TIME:
                self.time_pos = 0.0
            return

        if request_id == REQ_PATH:
            self.path = data if isinstance(data, str) else ''
        elif request_id == REQ_WORKING_DIR:
            self.working_directory = data if isinstance(data, str) else None
        elif request_id == REQ_PAUSE:
            self.paused = bool(data)
        elif request_id == REQ_TIME:
            try:
                self.time_pos = float(data)
            except (TypeError, ValueError):
                self.time_pos = 0.0
        elif request_id == REQ_SPEED:
            try:
                self.speed = float(data)
            except (TypeError, ValueError):
                self.speed = 1.0
        elif request_id == REQ_IDLE:
            self.idle = bool(data)

    def emit_status(self):
        file_path = resolve_media_path(self.path, self.working_directory)

        if self.idle or not file_path:
            connection_state = MediaConnectionState.CONNECTED_BUT_NO_FILE_LOADED
            file_path = ''
            claimed_position = -1
        elif self.paused:
            connection_state = MediaConnectionState.CONNECTED_AND_PAUSED
            claimed_position = self.time_pos
        else:
            connection_state = MediaConnectionState.CONNECTED_AND_PLAYING
            claimed_position = self.time_pos

        self.set_state(
            MediaStatusReport(
                timestamp=time.time(),
                connectionState=connection_state,
                filePath=file_path,
                playbackRate=self.speed,
                claimed_media_position=claimed_position,
            )
        )

    def enable(self):
        self._enabled = True
        self.reconnect_timer.start()
        self.open_connection()

    def disable(self):
        self._enabled = False
        self.poll_timer.stop()
        self.reconnect_timer.stop()
        if self.socket is not None:
            self.socket.abort()
            self.socket.deleteLater()
            self.socket = None
        self.path = ''
        self.working_directory = None
        self.paused = True
        self.idle = True
        self.speed = 1.0
        self.time_pos = 0.0
        self.set_state(MediaStatusReport(time.time()))

    def is_enabled(self) -> bool:
        return self._enabled
