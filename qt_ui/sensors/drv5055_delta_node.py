import time

from PySide6.QtWidgets import QWidget, QFormLayout, QVBoxLayout, QGroupBox, QLabel, QCheckBox

import pyqtgraph as pg
import numpy as np

from qt_ui.sensors import styles
from stim_math.sensors.drv5055 import DRV5055Data, DRV5055GapModel
from qt_ui import settings

from qt_ui.sensors.sensor_node_interface import SensorNodeInterface


class DRV5055DeltaSensorNode(QWidget, SensorNodeInterface):
    TITLE = "delta"
    DESCRIPTION = ("Adjust signal intensity based on gap change (mm per sample)\r\n"
                   "\r\n"
                   "Requires rest calibration on the absolute page first.\r\n"
                   "Use absolute mode so motion in either direction edges volume.")

    def __init__(self, gap_model: DRV5055GapModel):
        super().__init__()
        self.gap_model = gap_model

        # setup UI
        self.verticalLayout = QVBoxLayout(self)
        self.groupbox = QGroupBox(self)
        self.groupbox.setTitle("Settings")
        self.verticalLayout.addWidget(self.groupbox)
        self.formLayout = QFormLayout(self.groupbox)

        self.spinbox_threshold = pg.SpinBox(None, 0.05, compactHeight=False, suffix='mm', siPrefix=False, dec=True, minStep=0.001)
        self.spinbox_range = pg.SpinBox(None, 0.2, compactHeight=False, suffix='mm', siPrefix=False, dec=True, minStep=0.001, bounds=[0, None])
        self.spinbox_volume = pg.SpinBox(None, 0.0, compactHeight=False, suffix='%', step=0.1)
        self.checkbox = QCheckBox()
        self.label_suppression = QLabel('0%')
        self.label_suppressed_value = QLabel('0.0%')

        self.spinbox_threshold.valueChanged.connect(self.update_lines)
        self.spinbox_range.valueChanged.connect(self.update_lines)

        self.formLayout.addRow('threshold', self.spinbox_threshold)
        self.formLayout.addRow('threshold range', self.spinbox_range)
        label = QLabel('volume change (%)')
        label.setToolTip(
            "positive: increase volume when gap change exceeds threshold\r\n"
            "negative: decrease volume when gap change exceeds threshold")
        self.formLayout.addRow(label, self.spinbox_volume)
        self.formLayout.addRow('suppression', self.label_suppression)
        self.formLayout.addRow('suppressed value', self.label_suppressed_value)
        self.formLayout.addRow('absolute', self.checkbox)

        self.graph = pg.GraphicsLayoutWidget()
        self.verticalLayout.addWidget(self.graph)

        self.p1 = self.graph.addPlot()
        self.graph.nextRow()
        self.p2 = self.graph.addPlot()
        self.p2.setXLink(self.p1)

        self.p1.setLabels(left=('Gap', 'mm'))
        self.p2.setLabels(left=('Δ gap', 'mm'))

        self.p1.addLegend(offset=(30, 5))
        self.p2.addLegend(offset=(30, 5))

        self.gap_plot_item = pg.PlotDataItem(name='gap')
        self.gap_plot_item.setPen(styles.blue_line)
        self.p1.addItem(self.gap_plot_item)

        self.delta_plot_item = pg.PlotDataItem(name='Δ gap')
        self.delta_plot_item.setPen(styles.orange_line)
        self.p2.addItem(self.delta_plot_item)

        self.low_marker = pg.InfiniteLine(1, 0, movable=False, pen=styles.yellow_line_solid)
        self.p2.addItem(self.low_marker)
        self.high_marker = pg.InfiniteLine(10, 0, movable=False, pen=styles.yellow_line_dash)
        self.p2.addItem(self.high_marker)

        self.p1.setXRange(-10, 0, padding=0.05)

        self.delta_mm = 0.0

        self.x = []
        self.y_gap = []
        self.y_delta = []

        self.load_settings()
        self.update_lines()

    def new_drv5055_sensor_data(self, data: DRV5055Data):
        if not self.is_node_enabled():
            return
        if np.isnan(data.gap_mm) or np.isnan(data.gap_delta_mm):
            return

        value = data.gap_delta_mm
        if self.checkbox.isChecked():
            value = abs(value)
        self.delta_mm = value

        self.x.append(time.time())
        self.y_gap.append(data.gap_mm)
        self.y_delta.append(self.delta_mm)

        threshold = time.time() - 10
        while len(self.x) and self.x[0] < threshold:
            del self.x[0]
            del self.y_gap[0]
            del self.y_delta[0]

        self.update_graph_data()

    def process(self, parameters):
        if 'volume' in parameters and self.gap_model.is_calibrated:
            low = self.spinbox_threshold.value()
            high = low + self.spinbox_range.value()
            xp = [low, high]
            if self.spinbox_volume.value() >= 0:
                yp = [1 - self.spinbox_volume.value() / 100, 1]
            else:
                yp = [1, 1 + self.spinbox_volume.value() / 100]
            adjustment = np.clip(np.interp(self.delta_mm, xp, yp), 0, 1)
            parameters['volume'] *= adjustment

    def update_graph_data(self):
        x = np.array(self.x) - self.x[-1]
        self.gap_plot_item.setData(x=x, y=np.array(self.y_gap))
        self.delta_plot_item.setData(x=x, y=np.array(self.y_delta))

    def update_lines(self, *args, **kwargs):
        low = self.spinbox_threshold.value()
        high = low + self.spinbox_range.value()
        self.low_marker.setValue(low)
        self.high_marker.setValue(high)

    def update_suppression_display(self, suppression: float):
        self.label_suppression.setText(f'{suppression * 100:.0f}%')
        self.label_suppressed_value.setText(f'{self.spinbox_volume.value() * (1 - suppression):.1f}%')

    def save_settings(self):
        settings.sensor_drv5055_delta_threshold.set(self.spinbox_threshold.value())
        settings.sensor_drv5055_delta_range.set(self.spinbox_range.value())
        settings.sensor_drv5055_delta_volume.set(self.spinbox_volume.value())
        settings.sensor_drv5055_delta_absolute.set(self.checkbox.isChecked())

    def load_settings(self):
        self.spinbox_threshold.setValue(settings.sensor_drv5055_delta_threshold.get())
        self.spinbox_range.setValue(settings.sensor_drv5055_delta_range.get())
        self.spinbox_volume.setValue(settings.sensor_drv5055_delta_volume.get())
        self.checkbox.setChecked(settings.sensor_drv5055_delta_absolute.get())
