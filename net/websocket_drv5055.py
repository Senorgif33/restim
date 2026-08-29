import json
import logging

from PySide6 import QtNetwork
from PySide6.QtCore import Signal, QObject
from PySide6.QtWebSockets import QWebSocket

from stim_math.sensors.drv5055 import DRV5055Data

logger = logging.getLogger('restim.websocket')


class WebsocketDRV5055Handler(QObject):
    def __init__(self, websocket: QWebSocket):
        super().__init__()
        self.websocket = websocket

        self.websocket.textMessageReceived.connect(self.textMessageReceived)
        self.websocket.disconnected.connect(self.disconnected)

    def textMessageReceived(self, msg):
        try:
            js = json.loads(msg)
            data = DRV5055Data(
                delta=js['delta'],
                volts=js.get('volts', 0.0),
                raw=js.get('raw', 0),
            )
            self.new_drv5055_data.emit(data)
        except json.decoder.JSONDecodeError:
            pass
        except KeyError:
            pass

    def transmit_drv5055_data(self, data: DRV5055Data):
        self.websocket.sendTextMessage(
            f'{{"delta": {data.delta:.6f}, "volts": {data.volts:.6f}, "raw": {int(data.raw)}}}'
        )

    def is_connected(self):
        return self.websocket.state() != QtNetwork.QAbstractSocket.SocketState.UnconnectedState

    new_drv5055_data = Signal(DRV5055Data)
    disconnected = Signal()
