import json
import logging

from PySide6 import QtNetwork
from PySide6.QtCore import Signal, QObject
from PySide6.QtWebSockets import QWebSocket

from stim_math.sensors.drv5055 import DRV5055Data

logger = logging.getLogger('restim.websocket')


def parse_drv5055_message(msg):
    try:
        js = json.loads(msg)
    except json.decoder.JSONDecodeError:
        return None
    if not isinstance(js, dict):
        return None
    if js.get('type') == 'settings':
        return 'settings', js
    if 'delta' not in js:
        return None
    return 'sample', DRV5055Data(
        delta=js['delta'],
        volts=js.get('volts', 0.0),
        raw=js.get('raw', 0),
    )


class WebsocketDRV5055Handler(QObject):
    def __init__(self, websocket: QWebSocket):
        super().__init__()
        self.websocket = websocket

        self.websocket.textMessageReceived.connect(self.textMessageReceived)
        self.websocket.disconnected.connect(self.disconnected)

    def textMessageReceived(self, msg):
        parsed = parse_drv5055_message(msg)
        if not parsed:
            return
        kind, value = parsed
        if kind == 'settings':
            self.new_drv5055_settings.emit(value)
        else:
            self.new_drv5055_data.emit(value)

    def transmit_drv5055_data(self, data: DRV5055Data):
        self.websocket.sendTextMessage(
            f'{{"delta": {data.delta:.6f}, "volts": {data.volts:.6f}, "raw": {int(data.raw)}}}'
        )

    def transmit_settings(self, payload: dict):
        if not self.is_connected():
            return
        self.websocket.sendTextMessage(json.dumps(payload))

    def is_connected(self):
        return self.websocket.state() != QtNetwork.QAbstractSocket.SocketState.UnconnectedState

    new_drv5055_data = Signal(DRV5055Data)
    new_drv5055_settings = Signal(object)
    disconnected = Signal()
