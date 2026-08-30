"""
REST control API helpers: snapshot / patch Restim UI state for LAN remotes.

Shapes intentionally align with Restim-vector-live-remote-control `RestimState` /
`RestimPatch` (see that repo's src/types/api.ts).
"""
from __future__ import annotations

import json
import logging
import time
from collections import deque

import numpy as np
from PySide6.QtHttpServer import QHttpServerRequest

from net.http_server import json_response
from qt_ui.device_wizard.enums import DeviceConfiguration, DeviceType
from qt_ui.three_phase_settings_widget import Interface
from stim_math.axis import WriteProtectedAxis
from stim_math.threephase import ThreePhaseSignalGenerator

logger = logging.getLogger('restim.rest_api')


def _complex_ohm(z) -> dict:
    if z is None:
        return {'re': 0.0, 'im': 0.0}
    return {'re': float(np.real(z)), 'im': float(np.imag(z))}


def _phase_mode(config: DeviceConfiguration) -> str:
    if config.device_type == DeviceType.FOCSTIM_FOUR_PHASE:
        return 'four'
    return 'three'


def _is_foc(config: DeviceConfiguration) -> bool:
    return config.device_type in (
        DeviceType.FOCSTIM_THREE_PHASE,
        DeviceType.FOCSTIM_FOUR_PHASE,
    )


def _pattern_speed(window) -> float:
    return float(window.doubleSpinBox.value())


def _pattern_list(window) -> list:
    return [
        window.comboBox_patternSelect.itemText(i)
        for i in range(window.comboBox_patternSelect.count())
    ]


def _pattern_mode(window) -> str:
    text = window.comboBox_patternSelect.currentText()
    return text if text else ''


def _set_pattern_mode(window, mode: str) -> bool:
    if not mode:
        return False
    index = window.comboBox_patternSelect.findText(mode)
    if index < 0:
        # case-insensitive / partial fallback
        for i in range(window.comboBox_patternSelect.count()):
            if window.comboBox_patternSelect.itemText(i).lower() == mode.lower():
                index = i
                break
    if index < 0:
        return False
    window.comboBox_patternSelect.setCurrentIndex(index)
    return True


def _volume_state(window) -> dict:
    tab = window.tab_volume
    now = time.time()
    latency = getattr(tab, 'latency', 0.0)

    master = float(window.doubleSpinBox_volume.value())
    if issubclass(tab.axis_funscript_volume.__class__, WriteProtectedAxis):
        api = float(tab.axis_funscript_volume.interpolate(now - latency))
    else:
        api = float(tab.axis_api_volume.interpolate(now - latency))
    external = float(tab.axis_external_volume.interpolate(now - latency))
    inactivity_live = float(tab.inactivity_multiplier * (tab.slow_start_multiplier if tab.playing else 1.0))
    if not tab.playing:
        inactivity_live = float(tab.inactivity_multiplier)
    effective = master * api * inactivity_live * external

    return {
        'masterPct': master,
        'effectivePct': effective,
        'apiPct': api * 100.0,
        'externalPct': external * 100.0,
        'inactivityLivePct': inactivity_live * 100.0,
        'rampEnabled': tab.checkBox_ramp_enabled.isChecked(),
        'rampTargetPct': float(tab.doubleSpinBox_ramp_target.value()),
        'rampRatePerMin': float(tab.doubleSpinBox_ramp_rate.value()),
        'inactivityThresholdSec': float(tab.doubleSpinBox_inactivity_threshold.value()),
        'inactivityRampSec': float(tab.doubleSpinBox_inactivity_ramp_time.value()),
        'inactivityReducePct': float(tab.doubleSpinBox_inactivity_volume.value()),
        'slowStartSec': float(tab.doubleSpinBox_slow_start.value()),
        'tauUs': float(tab.doubleSpinBox_tau.value()),
        'pulseFreqNormalization': tab.checkBox_pulse_frequency_enable.isChecked(),
        'burstGap': tab.checkbox_burst_gap_enable.isChecked(),
    }


def _pulse_state(window) -> dict:
    pulse = window.tab_pulse_settings
    return {
        'carrierHz': float(pulse.carrier.value()),
        'pulseHz': float(pulse.pulse_freq_slider.value()),
        'widthCycles': float(pulse.pulse_width_slider.value()),
        'intervalRandomPct': float(pulse.pulse_interval_random.value()),
        'riseTimeCycles': float(pulse.pulse_rise_time.value()),
    }


def _device_stats(window, electrodes: int) -> dict:
    foc = window.foc_device_stats
    skin = {
        'a': _complex_ohm(getattr(foc, 'last_resistance_a', 0)),
        'b': _complex_ohm(getattr(foc, 'last_resistance_b', 0)),
        'c': _complex_ohm(getattr(foc, 'last_resistance_c', 0)),
    }
    if electrodes >= 4:
        skin['d'] = _complex_ohm(getattr(foc, 'last_resistance_d', 0))
    return {
        'transformerPct': float(getattr(foc, 'last_transformer', 0.0)) * 100.0,
        'transformerMaxPct': float(getattr(foc, 'transformer_max', 0.0)) * 100.0,
        'voltagePct': float(getattr(foc, 'last_voltage', 0.0)) * 100.0,
        'voltageMaxPct': float(getattr(foc, 'voltage_max', 0.0)) * 100.0,
        'skinOhm': skin,
    }


def _three_phase_live(window) -> dict:
    now = time.time()
    alpha = float(window.alpha.interpolate(now))
    beta = float(window.beta.interpolate(now))
    n, l, r = ThreePhaseSignalGenerator.electrode_amplitude(
        np.array([alpha], dtype=np.float64),
        np.array([beta], dtype=np.float64),
    )
    return {
        'alpha': alpha,
        'beta': beta,
        'electrodes': {
            'a': float(n[0]),
            'b': float(l[0]),
            'c': float(r[0]),
        },
        'pattern': {
            'mode': _pattern_mode(window),
            'speed': _pattern_speed(window),
        },
        'device': _device_stats(window, electrodes=3),
    }


def _four_phase_live(window) -> dict:
    now = time.time()
    return {
        'electrodes': {
            'a': float(window.intensity_a.interpolate(now)),
            'b': float(window.intensity_b.interpolate(now)),
            'c': float(window.intensity_c.interpolate(now)),
            'd': float(window.intensity_d.interpolate(now)),
        },
        'pattern': {
            'mode': _pattern_mode(window),
            'speed': _pattern_speed(window),
        },
        'device': _device_stats(window, electrodes=4),
    }


def _four_phase_cal(window) -> dict:
    tab = window.tab_fourphase
    return {
        'aDb': float(tab.a_power.value()),
        'bDb': float(tab.b_power.value()),
        'cDb': float(tab.c_power.value()),
        'dDb': float(tab.d_power.value()),
        'centerReductionPct': float(tab.center_reduction.value()),
    }


def _three_phase_cal(window) -> dict:
    tab = window.tab_threephase
    iface = tab.comboBox_interface.currentData()
    interface = 'classic' if iface == Interface.Classic else 'modern'
    return {
        'interface': interface,
        'neutralDb': float(tab.neutral.value()),
        'rightDb': float(tab.right.value()),
        'aDb': float(tab.a_power.value()),
        'bDb': float(tab.b_power.value()),
        'cDb': float(tab.c_power.value()),
        'centerReductionPct': float(tab.center_reduction.value()),
        'transformEnabled': tab.groupBox_2.isChecked(),
        'rotationDeg': float(tab.rotation.value()),
        'mirror': tab.mirror.isChecked(),
        'limitTop': float(tab.limit_top.value()),
        'limitBottom': float(tab.limit_bottom.value()),
        'limitLeft': float(tab.limit_left.value()),
        'limitRight': float(tab.limit_right.value()),
    }


def _sensor_nodes(window) -> list:
    nodes = []
    sensors = window.page_sensors
    for category, node_list in sensors.structure.items():
        cat_title = getattr(category, 'TITLE', category.__class__.__name__)
        for node in node_list:
            nodes.append({
                'id': f"{cat_title}/{node.TITLE}",
                'category': cat_title,
                'title': node.TITLE,
                'enabled': bool(node.is_node_enabled()),
            })
    return nodes


def _sensors_state(window) -> dict:
    nodes = _sensor_nodes(window)
    any_enabled = any(n['enabled'] for n in nodes)
    ducking = float(np.clip(window.sensor_suppression.interpolate(time.time()), 0.0, 1.0))
    # Keep history short; remote client can also accumulate from ducking
    hist = list(getattr(window, '_rest_sensor_history', []) or [])
    return {
        'enabled': any_enabled,
        'level': ducking,
        'ducking': ducking,
        'history': hist[-60:],
        'nodes': nodes,
    }


def _driven_by_vector(window) -> bool:
    """True when funscript/T-code ownership has disabled pattern axes or volume API axis."""
    if issubclass(window.tab_volume.axis_funscript_volume.__class__, WriteProtectedAxis):
        return True
    if getattr(window.motion_3, 'script_alpha', None) is not None:
        return True
    if getattr(window.motion_4, 'script_a', None) is not None:
        return True
    return False


def _foc_toolbar(window) -> dict:
    config = DeviceConfiguration.from_settings()
    battery = float(window.battery_bar.value())
    device_vol = window.last_device_volume
    return {
        'available': _is_foc(config),
        'batteryPct': battery,
        'deviceVolumePct': float(device_vol) * 100.0 if device_vol is not None else 0.0,
    }


def build_live(window) -> dict:
    """Lightweight snapshot for high-rate visualizer polling (skip cal/pulse/history/nodes)."""
    config = DeviceConfiguration.from_settings()
    mode = _phase_mode(config)
    ducking = float(np.clip(window.sensor_suppression.interpolate(time.time()), 0.0, 1.0))
    vol = _volume_state(window)
    four = _four_phase_live(window)
    three = _three_phase_live(window)
    # Drop expensive / slow-changing skin resistance from the hot path
    four_device = {
        'transformerPct': four['device']['transformerPct'],
        'transformerMaxPct': four['device']['transformerMaxPct'],
        'voltagePct': four['device']['voltagePct'],
        'voltageMaxPct': four['device']['voltageMaxPct'],
    }
    three_device = {
        'transformerPct': three['device']['transformerPct'],
        'transformerMaxPct': three['device']['transformerMaxPct'],
        'voltagePct': three['device']['voltagePct'],
        'voltageMaxPct': three['device']['voltageMaxPct'],
    }
    from qt_ui.mainwindow import PlayState
    any_sensor = any(
        node.is_node_enabled()
        for nodes in window.page_sensors.structure.values()
        for node in nodes
    )
    return {
        'transport': {
            'playing': window.playstate == PlayState.PLAYING,
        },
        'phaseMode': mode,
        'patterns': _pattern_list(window),
        'foc': _foc_toolbar(window),
        'volume': {
            'masterPct': vol['masterPct'],
            'effectivePct': vol['effectivePct'],
            'apiPct': vol['apiPct'],
            'externalPct': vol['externalPct'],
            'inactivityLivePct': vol['inactivityLivePct'],
        },
        'fourPhase': {
            'electrodes': four['electrodes'],
            'pattern': four['pattern'],
            'device': four_device,
        },
        'threePhase': {
            'alpha': three['alpha'],
            'beta': three['beta'],
            'electrodes': three['electrodes'],
            'pattern': three['pattern'],
            'device': three_device,
        },
        'sensors': {
            'enabled': any_sensor,
            'level': ducking,
            'ducking': ducking,
        },
        'drivenByVector': _driven_by_vector(window),
    }


def build_state(window) -> dict:
    config = DeviceConfiguration.from_settings()
    mode = _phase_mode(config)

    # Sample sensor history at most ~10 Hz so LAN polls stay light
    hist = getattr(window, '_rest_sensor_history', None)
    if hist is None:
        hist = deque(maxlen=60)
        window._rest_sensor_history = hist
    ducking = float(np.clip(window.sensor_suppression.interpolate(time.time()), 0.0, 1.0))
    now = time.time()
    last_t = getattr(window, '_rest_sensor_history_t', 0.0)
    if now - last_t >= 0.1:
        hist.append(ducking)
        window._rest_sensor_history_t = now

    from qt_ui.mainwindow import PlayState
    state = {
        'connection': 'online',
        'transport': {
            'playing': window.playstate == PlayState.PLAYING,
        },
        'foc': _foc_toolbar(window),
        'volume': _volume_state(window),
        'pulse': _pulse_state(window),
        'fourPhase': _four_phase_live(window),
        'phaseMode': mode,
        'patterns': _pattern_list(window),
        'threePhase': _three_phase_live(window),
        'fourPhaseCal': _four_phase_cal(window),
        'threePhaseCal': _three_phase_cal(window),
        'sensors': _sensors_state(window),
        'output': {
            'waveform': [],
            'peak': 0.0,
        },
        'drivenByVector': _driven_by_vector(window),
    }
    return state


def build_schema(window) -> dict:
    config = DeviceConfiguration.from_settings()
    mode = _phase_mode(config)
    patterns = _pattern_list(window)

    pulse_visible = window.tabWidget.isTabVisible(window.tabWidget.indexOf(window.tab_pulse_settings))
    four_cal_visible = window.tabWidget.isTabVisible(window.tabWidget.indexOf(window.tab_fourphase))
    three_cal_visible = window.tabWidget.isTabVisible(window.tabWidget.indexOf(window.tab_threephase))

    return {
        'version': 1,
        'phaseMode': mode,
        'deviceType': config.device_type.name if hasattr(config.device_type, 'name') else str(config.device_type),
        'waveformType': config.waveform_type.name if hasattr(config.waveform_type, 'name') else str(config.waveform_type),
        'foc': _is_foc(config),
        'panels': {
            'transport': {'read': True, 'write': False, 'actions': ['start', 'stop']},
            'volume': {'read': True, 'write': True},
            'pulse': {'read': True, 'write': pulse_visible, 'visible': pulse_visible},
            'live': {
                'read': True,
                'write': ['pattern'],
                'phaseMode': mode,
                'patterns': patterns,
            },
            'fourPhaseCal': {'read': True, 'write': four_cal_visible, 'visible': four_cal_visible},
            'threePhaseCal': {'read': True, 'write': three_cal_visible, 'visible': three_cal_visible},
            'sensors': {'read': True, 'write': True},
        },
        'actions': list(window.http_server.actions),
    }


def apply_patch(window, patch: dict) -> dict:
    """Apply RestimPatch-shaped dict. Returns {ok, applied, ignored, errors}."""
    applied = []
    ignored = []
    errors = []

    if not isinstance(patch, dict):
        return {'ok': False, 'applied': [], 'ignored': [], 'errors': ['body must be a JSON object']}

    if 'phaseMode' in patch:
        ignored.append('phaseMode')  # device-owned

    # --- volume ---
    vol = patch.get('volume')
    if isinstance(vol, dict):
        tab = window.tab_volume
        spin = window.doubleSpinBox_volume
        mapping = [
            ('masterPct', lambda v: spin.setValue(float(v))),
            ('rampEnabled', lambda v: tab.checkBox_ramp_enabled.setChecked(bool(v))),
            ('rampTargetPct', lambda v: tab.doubleSpinBox_ramp_target.setValue(float(v))),
            ('rampRatePerMin', lambda v: tab.doubleSpinBox_ramp_rate.setValue(float(v))),
            ('inactivityThresholdSec', lambda v: tab.doubleSpinBox_inactivity_threshold.setValue(float(v))),
            ('inactivityRampSec', lambda v: tab.doubleSpinBox_inactivity_ramp_time.setValue(float(v))),
            ('inactivityReducePct', lambda v: tab.doubleSpinBox_inactivity_volume.setValue(float(v))),
            ('slowStartSec', lambda v: tab.doubleSpinBox_slow_start.setValue(float(v))),
            ('tauUs', lambda v: tab.doubleSpinBox_tau.setValue(float(v))),
            ('pulseFreqNormalization', lambda v: tab.checkBox_pulse_frequency_enable.setChecked(bool(v))),
            ('burstGap', lambda v: tab.checkbox_burst_gap_enable.setChecked(bool(v))),
        ]
        readonly = {'effectivePct', 'apiPct', 'externalPct', 'inactivityLivePct'}
        for key, setter in mapping:
            if key in vol:
                try:
                    setter(vol[key])
                    applied.append(f'volume.{key}')
                except Exception as e:
                    errors.append(f'volume.{key}: {e}')
        for key in vol:
            if key in readonly:
                ignored.append(f'volume.{key}')
            elif key not in {m[0] for m in mapping} and key not in readonly:
                ignored.append(f'volume.{key}')

    # --- pulse ---
    pulse_patch = patch.get('pulse')
    if isinstance(pulse_patch, dict):
        pulse = window.tab_pulse_settings
        if not pulse.carrier.isEnabled():
            ignored.append('pulse')
        else:
            mapping = [
                ('carrierHz', pulse.carrier),
                ('pulseHz', pulse.pulse_freq_slider),
                ('widthCycles', pulse.pulse_width_slider),
                ('intervalRandomPct', pulse.pulse_interval_random),
                ('riseTimeCycles', pulse.pulse_rise_time),
            ]
            for key, widget in mapping:
                if key in pulse_patch:
                    try:
                        widget.setValue(float(pulse_patch[key]))
                        applied.append(f'pulse.{key}')
                    except Exception as e:
                        errors.append(f'pulse.{key}: {e}')
            for key in pulse_patch:
                if key not in {m[0] for m in mapping}:
                    ignored.append(f'pulse.{key}')

    # --- pattern (live) ---
    for section in ('fourPhase', 'threePhase'):
        sec = patch.get(section)
        if not isinstance(sec, dict):
            continue
        pattern = sec.get('pattern')
        if not isinstance(pattern, dict):
            for key in sec:
                ignored.append(f'{section}.{key}')
            continue
        if 'mode' in pattern:
            if _set_pattern_mode(window, str(pattern['mode'])):
                applied.append(f'{section}.pattern.mode')
            else:
                errors.append(f'{section}.pattern.mode: unknown pattern')
        if 'speed' in pattern:
            try:
                window.doubleSpinBox.setValue(float(pattern['speed']))
                applied.append(f'{section}.pattern.speed')
            except Exception as e:
                errors.append(f'{section}.pattern.speed: {e}')
        for key in sec:
            if key != 'pattern':
                ignored.append(f'{section}.{key}')  # electrodes/device read-only

    # --- four-phase calibration ---
    fcal = patch.get('fourPhaseCal')
    if isinstance(fcal, dict):
        tab = window.tab_fourphase
        mapping = [
            ('aDb', tab.a_power),
            ('bDb', tab.b_power),
            ('cDb', tab.c_power),
            ('dDb', tab.d_power),
            ('centerReductionPct', tab.center_reduction),
        ]
        for key, widget in mapping:
            if key in fcal:
                try:
                    widget.setValue(float(fcal[key]))
                    applied.append(f'fourPhaseCal.{key}')
                except Exception as e:
                    errors.append(f'fourPhaseCal.{key}: {e}')
        # Do not call tab.normalize() here — that forces max(A,B,C,D) to 0 dB
        # (relative calibration). Remote clients set absolute dB like the spinboxes.

    # --- three-phase calibration ---
    tcal = patch.get('threePhaseCal')
    if isinstance(tcal, dict):
        tab = window.tab_threephase
        if 'interface' in tcal:
            want = str(tcal['interface']).lower()
            for i in range(tab.comboBox_interface.count()):
                data = tab.comboBox_interface.itemData(i)
                label = 'classic' if data == Interface.Classic else 'modern'
                if label == want:
                    tab.comboBox_interface.setCurrentIndex(i)
                    applied.append('threePhaseCal.interface')
                    break
        if 'neutralDb' in tcal:
            tab.neutral.setValue(float(tcal['neutralDb']))
            applied.append('threePhaseCal.neutralDb')
        if 'rightDb' in tcal:
            tab.right.setValue(float(tcal['rightDb']))
            applied.append('threePhaseCal.rightDb')
        if 'aDb' in tcal:
            tab.a_power.setValue(float(tcal['aDb']))
            applied.append('threePhaseCal.aDb')
        if 'bDb' in tcal:
            tab.b_power.setValue(float(tcal['bDb']))
            applied.append('threePhaseCal.bDb')
        if 'cDb' in tcal:
            tab.c_power.setValue(float(tcal['cDb']))
            applied.append('threePhaseCal.cDb')
        if 'centerReductionPct' in tcal:
            tab.center_reduction.setValue(float(tcal['centerReductionPct']))
            applied.append('threePhaseCal.centerReductionPct')
        if 'transformEnabled' in tcal:
            tab.groupBox_2.setChecked(bool(tcal['transformEnabled']))
            applied.append('threePhaseCal.transformEnabled')
        if 'rotationDeg' in tcal:
            tab.rotation.setValue(float(tcal['rotationDeg']))
            applied.append('threePhaseCal.rotationDeg')
        if 'mirror' in tcal:
            tab.mirror.setChecked(bool(tcal['mirror']))
            applied.append('threePhaseCal.mirror')
        if 'limitTop' in tcal:
            tab.limit_top.setValue(float(tcal['limitTop']))
            applied.append('threePhaseCal.limitTop')
        if 'limitBottom' in tcal:
            tab.limit_bottom.setValue(float(tcal['limitBottom']))
            applied.append('threePhaseCal.limitBottom')
        if 'limitLeft' in tcal:
            tab.limit_left.setValue(float(tcal['limitLeft']))
            applied.append('threePhaseCal.limitLeft')
        if 'limitRight' in tcal:
            tab.limit_right.setValue(float(tcal['limitRight']))
            applied.append('threePhaseCal.limitRight')
        # refresh axis bindings
        try:
            tab.classic_calibration_params_changed()
            tab.modern_calibration_params_changed()
            tab.center_reduction_changed()
            tab.adjust_limits_changed()
        except Exception:
            pass

    # --- sensors ---
    sensors = patch.get('sensors')
    if isinstance(sensors, dict):
        if 'enabled' in sensors:
            enable = bool(sensors['enabled'])
            for category, node_list in window.page_sensors.structure.items():
                for node in node_list:
                    try:
                        if enable:
                            node.enable_node()
                        else:
                            node.disable_node()
                    except Exception as e:
                        errors.append(f'sensors.enabled: {e}')
            window.page_sensors.refresh_labels()
            applied.append('sensors.enabled')
        nodes_patch = sensors.get('nodes')
        if isinstance(nodes_patch, list):
            id_to_node = {}
            for category, node_list in window.page_sensors.structure.items():
                cat_title = getattr(category, 'TITLE', category.__class__.__name__)
                for node in node_list:
                    id_to_node[f"{cat_title}/{node.TITLE}"] = node
            for item in nodes_patch:
                if not isinstance(item, dict) or 'id' not in item:
                    continue
                node = id_to_node.get(item['id'])
                if node is None:
                    errors.append(f"sensors.nodes: unknown id {item.get('id')}")
                    continue
                if 'enabled' in item:
                    try:
                        if item['enabled']:
                            node.enable_node()
                        else:
                            node.disable_node()
                        applied.append(f"sensors.nodes[{item['id']}].enabled")
                    except Exception as e:
                        errors.append(f"sensors.nodes[{item['id']}]: {e}")
            window.page_sensors.refresh_labels()
        for key in sensors:
            if key not in ('enabled', 'nodes'):
                ignored.append(f'sensors.{key}')

    for key in patch:
        if key not in (
            'volume', 'pulse', 'fourPhase', 'threePhase', 'phaseMode',
            'fourPhaseCal', 'threePhaseCal', 'sensors', 'transport', 'foc',
            'output', 'drivenByVector', 'connection',
        ):
            ignored.append(key)
        elif key in ('transport', 'foc', 'output', 'drivenByVector', 'connection'):
            ignored.append(key)

    return {
        'ok': len(errors) == 0,
        'applied': applied,
        'ignored': ignored,
        'errors': errors,
        'state': build_state(window),
    }


def parse_json_body(request: QHttpServerRequest):
    raw = bytes(request.body())
    if not raw:
        return {}, None
    try:
        data = json.loads(raw.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        return None, str(e)
    return data, None


def handle_live_request(window, request: QHttpServerRequest):
    if request.method() != QHttpServerRequest.Method.Get:
        return json_response({'ok': False, 'errors': ['method not allowed']})
    return json_response(build_live(window))


def handle_state_request(window, request: QHttpServerRequest):
    method = request.method()
    if method == QHttpServerRequest.Method.Get:
        return json_response(build_state(window))
    if method == QHttpServerRequest.Method.Post:
        body, err = parse_json_body(request)
        if err:
            return json_response({'ok': False, 'errors': [err]})
        result = apply_patch(window, body)
        return json_response(result)
    return json_response({'ok': False, 'errors': ['method not allowed']})


def handle_schema_request(window, request: QHttpServerRequest):
    if request.method() != QHttpServerRequest.Method.Get:
        return json_response({'ok': False, 'errors': ['method not allowed']})
    return json_response(build_schema(window))
