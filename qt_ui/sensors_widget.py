import functools
import os
import pathlib
import time

import numpy as np
from PySide6.QtCore import Qt, Signal, QTimer
from pyqtgraph import GraphicsLayoutWidget

from funscript.collect_funscripts import Resource
from net.media_source.vlc import VLC
from net.media_source.kodi import Kodi
from qt_ui.additional_search_paths_dialog import AdditionalSearchPathsDialog
from qt_ui.device_wizard.axes import AxisEnum

from PySide6 import QtCore, QtWidgets
from PySide6.QtGui import QIcon, QFont
from PySide6.QtWidgets import QAbstractItemView, QFileDialog, QWidget, QTreeWidgetItem, QFormLayout, QVBoxLayout, \
    QGroupBox, QLabel

from net.media_source.heresphere import HereSphere
from net.media_source.interface import MediaConnectionState
from qt_ui.file_dialog import FileDialog
from qt_ui.sensors.as5311_absolute_node import AS5311AbsoluteSensorNode
from qt_ui.sensors.as5311_highpass_node import AS5311HighPassNode
from qt_ui.sensors.as5311_edging_node import AS5311EdgingNode
from qt_ui.sensors.as5311_velocity_node import AS5311VelocitySensorNode
from qt_ui.sensors.imu_hip_thrust_node import IMUHipThrustNode
from qt_ui.sensors.imu_velocity_node import IMUVelocityNode
from qt_ui.sensors.pressure_absolute_node import PressureAbsoluteSensorNode
from qt_ui.sensors.pressure_depletion_node import PressureDepletionSensorNode
from qt_ui.sensors.drv5055_delta_node import DRV5055DeltaSensorNode
from qt_ui.sensors.drv5055_absolute_node import DRV5055AbsoluteSensorNode
from qt_ui.sensors.sensor_category import SensorCategory, SensorCategoryPressure, SensorCategoryAS5311, \
    SensorCategoryIMU, SensorCategoryDRV5055
from qt_ui.sensors.sensor_node_interface import SensorNodeInterface
from qt_ui.sensors_widget_ui import Ui_SensorsWidget


from stim_math.sensors.as5311 import AS5311Data
from stim_math.sensors.imu import IMUData
from stim_math.sensors.pressure import PressureData
from stim_math.sensors.drv5055 import DRV5055Data, DRV5055GapModel
from qt_ui import settings


class SensorsWidget(QWidget, Ui_SensorsWidget):
    def __init__(self):
        QWidget.__init__(self)
        self.setupUi(self)

        self.treeWidget.header().resizeSection(0, 120)
        self.treeWidget.header().resizeSection(1, 40)

        # remove any useless pages
        while self.stackedWidget.count():
            self.stackedWidget.removeWidget(self.stackedWidget.currentWidget())

        self.page_empty = QWidget()
        self.stackedWidget.addWidget(self.page_empty)
        self.stackedWidget.setCurrentIndex(0)

        larger_font = QFont()
        larger_font.setPointSize(12)

        category_imu = SensorCategoryIMU()
        self.new_imu_sensor_data_from_device.connect(category_imu.sensor_data_from_device)
        self.new_imu_sensor_data_from_network.connect(category_imu.sensor_data_from_network)
        category_as5311 = SensorCategoryAS5311()
        self.new_as5311_sensor_data_from_device.connect(category_as5311.sensor_data_from_device)
        self.new_as5311_sensor_data_from_network.connect(category_as5311.sensor_data_from_network)
        category_pressure = SensorCategoryPressure()
        self.new_pressure_sensor_data_from_device.connect(category_pressure.sensor_data_from_device)
        self.new_pressure_sensor_data_from_network.connect(category_pressure.sensor_data_from_network)
        rest_volts = settings.sensor_drv5055_rest_volts.get()
        self._drv5055_gap_model = DRV5055GapModel(
            rest_gap_mm=settings.sensor_drv5055_rest_gap_mm.get(),
            rest_volts=rest_volts if rest_volts > 0 else None,
        )
        category_drv5055 = SensorCategoryDRV5055(self._drv5055_gap_model)
        self.new_drv5055_sensor_data_from_device.connect(self._on_drv5055_from_device)
        self.new_drv5055_sensor_data_from_network.connect(self._on_drv5055_from_network)
        self._drv5055_to_category = category_drv5055

        self.structure = {
            category_imu : [
                IMUHipThrustNode(),
                IMUVelocityNode(),
            ],
            category_as5311: [
                AS5311AbsoluteSensorNode(),
                AS5311HighPassNode(),
                AS5311VelocitySensorNode(),
                # AS5311EdgingNode(),
            ],
            category_pressure: [
                PressureAbsoluteSensorNode(),
                PressureDepletionSensorNode(),
            ],
            category_drv5055: [
                DRV5055AbsoluteSensorNode(self._drv5055_gap_model),
                DRV5055DeltaSensorNode(self._drv5055_gap_model),
            ],
        }

        self.treeWidget.clear()
        for category_class, nodes in self.structure.items():
            category_class: SensorCategory
            category_item = QTreeWidgetItem()
            category_item.setData(0, Qt.ItemDataRole.UserRole, category_class)
            category_item.setData(0, Qt.ItemDataRole.DisplayRole, category_class.TITLE)
            category_item.setFont(0, larger_font)
            self.treeWidget.addTopLevelItem(category_item)
            self.stackedWidget.addWidget(category_class)

            for node_class in nodes:
                node_class: SensorNodeInterface
                node_item = QTreeWidgetItem()
                node_item.setData(0, Qt.ItemDataRole.UserRole, node_class)
                node_item.setData(0, Qt.ItemDataRole.DisplayRole, node_class.TITLE)
                category_item.addChild(node_item)
                self.stackedWidget.addWidget(node_class)

                try:
                    category_as5311.new_sensor_data.connect(node_class.new_as5311_sensor_data)
                except AttributeError:
                    pass

                try:
                    category_imu.new_sensor_data.connect(node_class.new_imu_sensor_data)
                except AttributeError:
                    pass

                try:
                    category_pressure.new_sensor_data.connect(node_class.new_pressure_sensor_data)
                except AttributeError:
                    pass

                try:
                    category_drv5055.new_sensor_data.connect(node_class.new_drv5055_sensor_data)
                except AttributeError:
                    pass

        self.treeWidget.expandAll()
        self._drv5055_absolute = self.structure[category_drv5055][0]
        self._drv5055_delta = self.structure[category_drv5055][1]
        category_drv5055.buttonGroup.buttonToggled.connect(self._drv5055_source_changed)
        category_drv5055.remote_settings.connect(self.apply_drv5055_remote_settings)
        self._drv5055_absolute.locked_settings_changed.connect(self.publish_drv5055_settings)
        self._drv5055_delta.locked_settings_changed.connect(self.publish_drv5055_settings)
        self._drv5055_source_changed()
        self.treeWidget.itemSelectionChanged.connect(self.index_changed)
        self.checkBox.clicked.connect(self.checkbox_clicked)

        self.axis_sensor_suppression = None
        self._last_suppression = 0.0
        # Fraction of volume removed by enabled sensor nodes (0 = none, 1 = muted).
        self._last_volume_ducking = 0.0

        self._suppression_display_timer = QTimer()
        self._suppression_display_timer.setInterval(200)
        self._suppression_display_timer.timeout.connect(self._refresh_suppression_display)
        self._suppression_display_timer.start()

        self.index_changed()
        self.refresh_labels()

    def index_changed(self):
        # Update the description, enabled button and display widget
        # to those of the currently selected tree item.
        index = self.treeWidget.currentIndex()
        widget = index.siblingAtColumn(0).data(Qt.ItemDataRole.UserRole)
        if isinstance(widget, QWidget):
            if isinstance(widget, SensorCategory):
                self.stackedWidget.setCurrentWidget(widget)
                self.checkBox.setEnabled(False)
            else:
                # switch to the corresponding widget
                self.stackedWidget.setCurrentWidget(widget)
                self.checkBox.setChecked(widget.is_node_enabled())
                self.checkBox.setEnabled(True)
        else:
            self.stackedWidget.setCurrentWidget(self.page_empty)
            self.checkBox.setChecked(False)
            self.checkBox.setEnabled(False)

        try:
            self.textBrowser.setText(widget.DESCRIPTION)
        except AttributeError:
            self.textBrowser.setText('')

    def refresh_labels(self):
        # update all the enabled labels in the tree
        for category_index in range(self.treeWidget.topLevelItemCount()):
            category = self.treeWidget.topLevelItem(category_index)
            # category_widget = category.data(0, Qt.ItemDataRole.UserRole)
            for node_index in range(category.childCount()):
                node = category.child(node_index)
                node_widget: SensorNodeInterface = node.data(0, Qt.ItemDataRole.UserRole)
                if node_widget.is_node_enabled():
                    node.setData(1, Qt.ItemDataRole.DisplayRole, 'enabled')
                else:
                    node.setData(1, Qt.ItemDataRole.DisplayRole, '')

    def checkbox_clicked(self):
        items = self.treeWidget.selectedItems()
        if len(items):
            node = items[0].data(0, Qt.ItemDataRole.UserRole)
            try:
                if self.checkBox.isChecked():
                    node.enable_node()
                else:
                    node.disable_node()
            except AttributeError:
                self.checkBox.setChecked(False)

        self.refresh_labels()

    def set_sensor_suppression_axis(self, axis):
        self.axis_sensor_suppression = axis

    def _refresh_suppression_display(self):
        suppression = self._last_suppression
        for category_label, nodes in self.structure.items():
            for node in nodes:
                node.update_suppression_display(suppression)

    def process(self, parameters):
        suppression = 0.0
        if self.axis_sensor_suppression is not None:
            suppression = float(np.clip(self.axis_sensor_suppression.interpolate(time.time()), 0, 1))
        self._last_suppression = suppression

        volume_before = parameters['volume'] if 'volume' in parameters else None

        for category_label, nodes in self.structure.items():
            for node in nodes:
                if node.is_node_enabled():
                    if suppression > 0 and 'volume' in parameters:
                        node_before = parameters['volume']
                        node.process(parameters)
                        if node_before > 0:
                            adjustment = parameters['volume'] / node_before
                            suppressed_adjustment = adjustment + (1 - adjustment) * suppression
                            parameters['volume'] = node_before * suppressed_adjustment
                    else:
                        node.process(parameters)

        # Only update ducking when volume was in the parameter dict (FOC / audio path).
        if volume_before is not None and volume_before > 0 and 'volume' in parameters:
            self._last_volume_ducking = float(
                np.clip(1.0 - float(parameters['volume']) / float(volume_before), 0.0, 1.0)
            )

    def _drv5055_source_changed(self, *args):
        locked = not self._drv5055_to_category.radio_device.isChecked()
        self._drv5055_absolute.set_inherited_locked(locked)
        self._drv5055_delta.set_inherited_locked(locked)

    def apply_drv5055_remote_settings(self, payload):
        if self._drv5055_to_category.radio_device.isChecked():
            return
        if not isinstance(payload, dict):
            return
        absolute = payload.get('absolute') if isinstance(payload.get('absolute'), dict) else {}
        inherited = {}
        if 'restVolts' in payload:
            inherited['restVolts'] = payload['restVolts']
        if 'restGapMm' in payload:
            inherited['restGapMm'] = payload['restGapMm']
        for key in ('threshold', 'thresholdRange'):
            if key in absolute:
                inherited[key] = absolute[key]
        if inherited:
            self._drv5055_absolute.apply_inherited_settings(inherited)

        delta = payload.get('delta') if isinstance(payload.get('delta'), dict) else {}
        inherited_delta = {}
        for key in ('threshold', 'thresholdRange', 'absolute'):
            if key in delta:
                inherited_delta[key] = delta[key]
        if inherited_delta:
            self._drv5055_delta.apply_inherited_settings(inherited_delta)

    def publish_drv5055_settings(self):
        rest_volts = self._drv5055_gap_model.rest_volts
        payload = {
            'type': 'settings',
            'restVolts': float(rest_volts) if rest_volts is not None else 0.0,
            'restGapMm': float(self._drv5055_absolute.spinbox_rest_gap.value()),
            'absolute': {
                'threshold': float(self._drv5055_absolute.spinbox_threshold.value()),
                'thresholdRange': float(self._drv5055_absolute.spinbox_range.value()),
            },
            'delta': {
                'threshold': float(self._drv5055_delta.spinbox_threshold.value()),
                'thresholdRange': float(self._drv5055_delta.spinbox_range.value()),
                'absolute': bool(self._drv5055_delta.checkbox.isChecked()),
            },
        }
        self.drv5055_settings_snapshot.emit(payload)

    def _on_drv5055_from_device(self, data: DRV5055Data):
        self._drv5055_gap_model.update(data)
        self._drv5055_to_category.sensor_data_from_device(data)

    def _on_drv5055_from_network(self, data: DRV5055Data):
        self._drv5055_gap_model.update(data)
        self._drv5055_to_category.sensor_data_from_network(data)

    def save_settings(self):
        for category_label, nodes in self.structure.items():
            category_label.save_settings()
            for node in nodes:
                node.save_settings()

    new_as5311_sensor_data_from_device = Signal(AS5311Data)
    new_as5311_sensor_data_from_network = Signal(AS5311Data)
    new_imu_sensor_data_from_device = Signal(IMUData)
    new_imu_sensor_data_from_network = Signal(IMUData)
    new_pressure_sensor_data_from_device = Signal(PressureData)
    new_pressure_sensor_data_from_network = Signal(PressureData)
    new_drv5055_sensor_data_from_device = Signal(DRV5055Data)
    new_drv5055_sensor_data_from_network = Signal(DRV5055Data)
    drv5055_settings_snapshot = Signal(object)