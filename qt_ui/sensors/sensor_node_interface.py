

class SensorNodeInterface:
    TITLE = ""
    DESCRIPTION = ""
    # Seconds of history kept for desktop graphs + remote traces.
    TRACE_WINDOW_SEC = 10.0
    # Soft cap only — buffers are already pruned by TRACE_WINDOW_SEC.
    # Must be high enough for ~10s at high sensor rates (200 was ~5s @40Hz).
    TRACE_SERIES_MAX = 2500

    def __init__(self):
        super().__init__()
        self._node_enabled = False

    # implement these functions in your subclass:
    # def new_as5311_sensor_data(self, data: AS5311Data):
    # def new_imu_sensor_data(self, data: IMUData):
    # def new_pressure_sensor_data(self, data: PressureData):

    def enable_node(self):
        self._node_enabled = True

    def disable_node(self):
        self._node_enabled = False

    def is_node_enabled(self):
        return self._node_enabled

    def process(self, parameters):
        """
        :param parameters: a dict such as {'volume': 0.5, 'alpha': -0.4}
        :return:

        Modify the parameter dict in-place.

        For safety reasons, it is strongly recommended only to decrease the volume,
        and never increase it in this function.
        """
        pass

    def get_remote_trace(self) -> dict:
        """
        Live signal + settings for LAN remotes (Sensors page).

        Common keys:
            unit, label, value, series, times (−TRACE_WINDOW_SEC…0 like desktop graphs)
            label2, series2 (optional secondary plot)
            params: settings + readouts (threshold, volumePct, suppression, …)
        """
        return {
            'unit': '',
            'label': 'value',
            'value': None,
            'series': [],
            'times': [],
            'params': {},
        }

    def apply_remote_settings(self, params: dict) -> list:
        """
        Apply writable settings from a remote patch.
        Returns list of applied param keys.
        """
        return []

    @staticmethod
    def _trace_series(values, n=None):
        """Full time-window buffer for remotes (same history desktop graphs show)."""
        if not values:
            return []
        if n is None:
            n = SensorNodeInterface.TRACE_SERIES_MAX
        return [float(v) for v in values[-n:]]

    @staticmethod
    def _trace_times(x_list, n=None):
        """Seconds relative to newest sample (desktop graphs use −TRACE_WINDOW_SEC…0)."""
        if not x_list:
            return []
        if n is None:
            n = SensorNodeInterface.TRACE_SERIES_MAX
        xs = x_list[-n:]
        t0 = xs[-1]
        return [float(t - t0) for t in xs]

    @staticmethod
    def _set_spin(spinbox, key, params, applied):
        if key not in params:
            return
        try:
            spinbox.setValue(float(params[key]))
            applied.append(key)
        except Exception:
            pass

    @staticmethod
    def _set_check(checkbox, key, params, applied):
        if key not in params:
            return
        try:
            checkbox.setChecked(bool(params[key]))
            applied.append(key)
        except Exception:
            pass

    def update_suppression_display(self, suppression: float):
        """
        Called periodically from the UI thread to update suppression display labels.
        :param suppression: current sensor_suppression axis value, clamped to [0, 1]
        """
        pass

    def save_settings(self):
        """
        called before application shutdown
        """
        pass
