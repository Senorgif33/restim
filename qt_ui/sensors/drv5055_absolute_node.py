import time

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QWidget, QFormLayout, QVBoxLayout, QGroupBox, QLabel, QPushButton

import pyqtgraph as pg
import numpy as np

from qt_ui.sensors import styles
from stim_math.sensors.drv5055 import DRV5055Data, DRV5055GapModel
from qt_ui import settings

from qt_ui.sensors.sensor_node_interface import SensorNodeInterface


class DRV5055AbsoluteSensorNode(QWidget, SensorNodeInterface):
    TITLE = "absolute"
    DESCRIPTION = ("Adjust signal intensity based on estimated magnet–sensor gap (mm)\r\n"
                   "\r\n"
                   "Larger gap = higher arousal (ring opening). Put the ring at rest and press “set rest”.\r\n"
                   "Gap uses a dipole model (approximate).")

    def __init__(self, gap_model: DRV5055GapModel):
        super().__init__()
        self.gap_model = gap_model
        self._latest_volts = None
        self._suppress_publish = False

        # setup UI
        self.verticalLayout = QVBoxLayout(self)
        self.groupbox = QGroupBox(self)
        self.groupbox.setTitle("Settings")
        self.verticalLayout.addWidget(self.groupbox)
        self.formLayout = QFormLayout(self.groupbox)

        self.spinbox_rest_gap = pg.SpinBox(None, 6.28, compactHeight=False, suffix='mm', siPrefix=False, dec=True, minStep=0.01, bounds=[0.1, None])
        self.button_set_rest = QPushButton("set rest")
        self.label_cal = QLabel("not calibrated")
        self.spinbox_threshold = pg.SpinBox(None, 6.28, compactHeight=False, suffix='mm', siPrefix=False, dec=True, minStep=0.01)
        self.spinbox_range = pg.SpinBox(None, 1.0, compactHeight=False, suffix='mm', siPrefix=False, dec=True, minStep=0.01, bounds=[0, None])
        self.spinbox_volume = pg.SpinBox(None, 0.0, compactHeight=False, suffix='%', step=0.1)
        self.spinbox_decay = pg.SpinBox(None, 1.0, compactHeight=False, suffix='s', siPrefix=True, dec=True, minStep=.1, bounds=[0, None])
        self.label_suppression = QLabel('0%')
        self.label_suppressed_value = QLabel('0.0%')
        self.label_gap = QLabel('—')

        self.spinbox_threshold.valueChanged.connect(self.update_lines)
        self.spinbox_range.valueChanged.connect(self.update_lines)
        self.spinbox_rest_gap.valueChanged.connect(self._rest_gap_changed)
        self.button_set_rest.clicked.connect(self._set_rest_clicked)

        self.formLayout.addRow('rest gap', self.spinbox_rest_gap)
        self.formLayout.addRow(self.button_set_rest, self.label_cal)
        self.formLayout.addRow('gap', self.label_gap)
        self.formLayout.addRow('threshold', self.spinbox_threshold)
        self.formLayout.addRow('threshold range', self.spinbox_range)
        label = QLabel('volume change (%)')
        label.setToolTip(
            "positive: increase volume when gap is large (ring opens / higher arousal)\r\n"
            "negative: decrease volume when gap is large")
        self.formLayout.addRow(label, self.spinbox_volume)
        self.formLayout.addRow('suppression', self.label_suppression)
        self.formLayout.addRow('suppressed value', self.label_suppressed_value)
        self.formLayout.addRow('decay', self.spinbox_decay)

        self.graph = pg.GraphicsLayoutWidget()
        self.verticalLayout.addWidget(self.graph)

        self.p1 = self.graph.addPlot()
        self.p1.setLabels(left=('Gap', 'mm'))
        self.p1.getAxis('left').enableAutoSIPrefix(False)
        self.p1.getAxis('left').setTickSpacing(major=1, minor=1)
        self.p1.addLegend(offset=(30, 5))

        self.gap_plot_item = pg.PlotDataItem(name='gap')
        self.gap_plot_item.setPen(styles.blue_line)
        self.p1.addItem(self.gap_plot_item)
        self.decayed_plot_item = pg.PlotDataItem(name='decayed')
        self.decayed_plot_item.setPen(styles.orange_line)
        self.p1.addItem(self.decayed_plot_item)

        self.low_marker = pg.InfiniteLine(1, 0, movable=False, pen=styles.yellow_line_solid)
        self.p1.addItem(self.low_marker)
        self.high_marker = pg.InfiniteLine(10, 0, movable=False, pen=styles.yellow_line_dash)
        self.p1.addItem(self.high_marker)
        self.p1.setXRange(-self.TRACE_WINDOW_SEC, 0, padding=0.05)

        self.gap_mm = 0.0
        self.decayed_gap = 0.0  # track peak gap (large gap = arousal)
        self.last_update = time.time()

        self.x = []
        self.y = []
        self.y_decay = []

        self.load_settings()
        self._refresh_cal_label()
        self.update_lines()

        self.spinbox_rest_gap.valueChanged.connect(self._emit_locked_change)
        self.spinbox_threshold.valueChanged.connect(self._emit_locked_change)
        self.spinbox_range.valueChanged.connect(self._emit_locked_change)
        self.button_set_rest.clicked.connect(self._emit_locked_change)

    def _emit_locked_change(self, *args):
        if self._suppress_publish:
            return
        self.locked_settings_changed.emit()

    def set_inherited_locked(self, locked: bool):
        enabled = not locked
        self.spinbox_rest_gap.setEnabled(enabled)
        self.button_set_rest.setEnabled(enabled)
        self.spinbox_threshold.setEnabled(enabled)
        self.spinbox_range.setEnabled(enabled)

    locked_settings_changed = Signal()

    def _rest_gap_changed(self, *args):
        self.gap_model.rest_gap_mm = self.spinbox_rest_gap.value()
        if self.gap_model.rest_volts is not None:
            self.gap_model.set_rest(self.gap_model.rest_volts, self.spinbox_rest_gap.value())
        self._refresh_cal_label()

    def _set_rest_clicked(self):
        if self._latest_volts is None:
            self.label_cal.setText("no signal yet")
            return
        self.gap_model.set_rest(self._latest_volts, self.spinbox_rest_gap.value())
        settings.sensor_drv5055_rest_volts.set(self._latest_volts)
        settings.sensor_drv5055_rest_gap_mm.set(self.spinbox_rest_gap.value())
        self._refresh_cal_label()

    def _refresh_cal_label(self):
        if self.gap_model.is_calibrated:
            self.label_cal.setText(f"ok @ {self.gap_model.rest_volts:.3f} V")
        else:
            self.label_cal.setText("not calibrated")

    def new_drv5055_sensor_data(self, data: DRV5055Data):
        self._latest_volts = data.volts
        if not self.is_node_enabled():
            return
        if np.isnan(data.gap_mm):
            self.label_gap.setText("— (set rest)")
            return

        now = time.time()
        dt = now - self.last_update
        alpha = np.exp(-dt / self.spinbox_decay.value()) if self.spinbox_decay.value() > 0 else 1
        self.last_update = now

        self.gap_mm = data.gap_mm
        self.decayed_gap = max(self.decayed_gap, self.gap_mm)
        self.decayed_gap += (self.gap_mm - self.decayed_gap) * (1 - alpha)

        self.label_gap.setText(f'{round(self.gap_mm)} mm')

        self.x.append(time.time())
        self.y.append(self.gap_mm)
        self.y_decay.append(self.decayed_gap)

        threshold = time.time() - self.TRACE_WINDOW_SEC
        while len(self.x) and self.x[0] < threshold:
            del self.x[0]
            del self.y[0]
            del self.y_decay[0]

        self.update_graph_data()

    def process(self, parameters):
        if 'volume' in parameters and self.gap_model.is_calibrated:
            # Larger gap → higher arousal. Threshold is rest / low arousal;
            # threshold+range is the open / high arousal end.
            low = self.spinbox_threshold.value()
            high = low + self.spinbox_range.value()
            xp = [low, high]
            if self.spinbox_volume.value() >= 0:
                # small gap: reduced, large gap: full
                yp = [1 - self.spinbox_volume.value() / 100, 1]
            else:
                # small gap: full, large gap: reduced
                yp = [1, 1 + self.spinbox_volume.value() / 100]
            adjustment = np.clip(np.interp(self.decayed_gap, xp, yp), 0, 1)
            parameters['volume'] *= adjustment

    def update_graph_data(self):
        x = np.array(self.x) - self.x[-1]
        self.gap_plot_item.setData(x=x, y=np.array(self.y))
        self.decayed_plot_item.setData(x=x, y=np.array(self.y_decay))

    def update_lines(self, *args, **kwargs):
        low = self.spinbox_threshold.value()
        high = low + self.spinbox_range.value()
        self.low_marker.setValue(low)
        self.high_marker.setValue(high)

    def update_suppression_display(self, suppression: float):
        self.label_suppression.setText(f'{suppression * 100:.0f}%')
        self.label_suppressed_value.setText(f'{self.spinbox_volume.value() * (1 - suppression):.1f}%')

    def get_remote_trace(self) -> dict:
        series = self._trace_series(self.y)
        series2 = self._trace_series(self.y_decay)
        times = self._trace_times(self.x)
        vol = float(self.spinbox_volume.value())
        gap = float(self.gap_mm) if series else None
        return {
            'unit': 'mm',
            'label': 'gap',
            'value': gap,
            'series': series,
            'times': times,
            'label2': 'decayed',
            'series2': series2,
            'params': {
                'restGapMm': float(self.spinbox_rest_gap.value()),
                'calibrated': bool(self.gap_model.is_calibrated),
                'gapMm': gap,
                'threshold': float(self.spinbox_threshold.value()),
                'thresholdRange': float(self.spinbox_range.value()),
                'volumePct': vol,
                'decaySec': float(self.spinbox_decay.value()),
                'suppression': 0.0,
                'suppressedValuePct': vol,
            },
        }

    def apply_inherited_settings(self, params: dict) -> list:
        params = dict(params)
        params.pop('volumePct', None)
        params.pop('decaySec', None)
        self._suppress_publish = True
        try:
            return self.apply_remote_settings(params)
        finally:
            self._suppress_publish = False

    def apply_remote_settings(self, params: dict) -> list:
        applied = []
        if 'restVolts' in params:
            try:
                volts = float(params['restVolts'])
                if volts > 0:
                    gap = float(params['restGapMm']) if 'restGapMm' in params else self.spinbox_rest_gap.value()
                    self.gap_model.set_rest(volts, gap)
                    applied.append('restVolts')
                    self._refresh_cal_label()
            except (TypeError, ValueError):
                pass
        self._set_spin(self.spinbox_rest_gap, 'restGapMm', params, applied)
        self._set_spin(self.spinbox_threshold, 'threshold', params, applied)
        self._set_spin(self.spinbox_range, 'thresholdRange', params, applied)
        self._set_spin(self.spinbox_volume, 'volumePct', params, applied)
        self._set_spin(self.spinbox_decay, 'decaySec', params, applied)
        if applied:
            self._rest_gap_changed()
            self.update_lines()
            self.save_settings()
        return applied

    def save_settings(self):
        settings.sensor_drv5055_rest_gap_mm.set(self.spinbox_rest_gap.value())
        if self.gap_model.rest_volts is not None:
            settings.sensor_drv5055_rest_volts.set(self.gap_model.rest_volts)
        settings.sensor_drv5055_absolute_threshold.set(self.spinbox_threshold.value())
        settings.sensor_drv5055_absolute_range.set(self.spinbox_range.value())
        settings.sensor_drv5055_absolute_volume.set(self.spinbox_volume.value())
        settings.sensor_drv5055_absolute_decay.set(self.spinbox_decay.value())

    def load_settings(self):
        self.spinbox_rest_gap.setValue(settings.sensor_drv5055_rest_gap_mm.get())
        rest_v = settings.sensor_drv5055_rest_volts.get()
        if rest_v > 0:
            self.gap_model.set_rest(rest_v, self.spinbox_rest_gap.value())
        self.spinbox_threshold.setValue(settings.sensor_drv5055_absolute_threshold.get())
        self.spinbox_range.setValue(settings.sensor_drv5055_absolute_range.get())
        self.spinbox_volume.setValue(settings.sensor_drv5055_absolute_volume.get())
        self.spinbox_decay.setValue(settings.sensor_drv5055_absolute_decay.get())
