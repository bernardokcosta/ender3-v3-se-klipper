# Creality Ender 3 V3 SE stock display integration
#
# Copyright (C) 2026 Bernardo Costa
#
# This file may be distributed under the terms of the GNU GPLv3 license.

import binascii
import logging

from .model import validate_filename
from .tjc3224 import TJC3224
from .transport import DisplayTransport
from .ui import DisplayUI


class E3V3SEDisplayMacro:
    def __init__(self, config):
        self.printer = config.get_printer()
        parts = config.get_name().split()
        if len(parts) < 3 or parts[1].lower() != "macro":
            raise config.error(
                "Display macro sections must use "
                "[e3v3se_display macro <name>]")
        self.name = " ".join(parts[2:])
        self.label = config.get("label")
        gcode_macro = self.printer.load_object(config, "gcode_macro")
        self.template = gcode_macro.load_template(config, "gcode")
        display = self.printer.load_object(config, "e3v3se_display")
        display.add_macro(self)


class E3V3SEDisplay:
    UPDATE_INTERVAL = 1.0

    def __init__(self, config):
        self.config = config
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.gcode = self.printer.lookup_object("gcode")
        required = ("virtual_sdcard", "pause_resume", "display_status")
        missing = [section for section in required
                   if not config.has_section(section)]
        if missing:
            raise config.error(
                "[e3v3se_display] requires these config sections: %s"
                % ", ".join("[%s]" % name for name in missing))
        if not config.get("encoder_pins", None):
            raise config.error(
                "[e3v3se_display] requires encoder_pins (normally "
                "^PA12,^PA11)")
        if not config.get("click_pin", None):
            raise config.error(
                "[e3v3se_display] requires click_pin (normally ^!PB1)")

        self.virtual_sdcard = self.printer.load_object(
            config, "virtual_sdcard")
        self.pause_resume = self.printer.load_object(config, "pause_resume")
        self.display_status = self.printer.load_object(config, "display_status")
        self.manual_probe = self.printer.load_object(config, "manual_probe")
        self.macros = []
        self.ready = False
        self.last_status = {}
        self._manual_probe_was_active = False

        self.max_hotend_temp = config.getfloat(
            "max_hotend_temp", 260., minval=0.)
        self.max_bed_temp = config.getfloat(
            "max_bed_temp", 100., minval=0.)
        self.z_offset_min = config.getfloat("z_offset_min", -5.)
        self.z_offset_max = config.getfloat(
            "z_offset_max", 5., above=self.z_offset_min)
        self.preheat_pla_temps = (
            config.getint("preheat_pla_nozzle", 200, minval=0,
                          maxval=int(self.max_hotend_temp)),
            config.getint("preheat_pla_bed", 60, minval=0,
                          maxval=int(self.max_bed_temp)))
        self.preheat_petg_temps = (
            config.getint("preheat_petg_nozzle", 230, minval=0,
                          maxval=int(self.max_hotend_temp)),
            config.getint("preheat_petg_bed", 70, minval=0,
                          maxval=int(self.max_bed_temp)))

        self.transport = DisplayTransport(config, self._handle_display_data)
        self.lcd = TJC3224(self.transport)
        self.ui = DisplayUI(self.lcd, self)
        from ..display.menu_keys import MenuKeys
        self.keys = MenuKeys(config, self._handle_key)
        self.update_timer = self.reactor.register_timer(self._update_event)

        self.gcode.register_command(
            "ENDER3V3SE_DISPLAY_REFRESH",
            self.cmd_ENDER3V3SE_DISPLAY_REFRESH,
            desc="Redraw the Ender 3 V3 SE stock display")
        self.gcode.register_command(
            "ENDER3V3SE_DISPLAY_STATUS", self.cmd_ENDER3V3SE_DISPLAY_STATUS,
            desc="Report the Ender 3 V3 SE display transport status")
        self.printer.register_event_handler("klippy:ready", self._handle_ready)
        self.printer.register_event_handler("klippy:disconnect",
                                            self._handle_disconnect)
        self.printer.register_event_handler("klippy:shutdown",
                                            self._handle_shutdown)
        self.printer.register_event_handler("klippy:notify_mcu_error",
                                            self._handle_mcu_error)

    def add_macro(self, macro):
        if any(item.name == macro.name for item in self.macros):
            raise self.printer.config_error(
                "Duplicate e3v3se display macro '%s'" % macro.name)
        self.macros.append(macro)

    def has_macros(self):
        return bool(self.macros)

    def get_macros(self):
        return list(self.macros)

    def _handle_ready(self):
        self.extruder = self.printer.lookup_object("extruder")
        self.heater_bed = self.printer.lookup_object("heater_bed")
        self.fan = self.printer.lookup_object("fan", None)
        self.toolhead = self.printer.lookup_object("toolhead")
        self.gcode_move = self.printer.lookup_object("gcode_move")
        self.print_stats = self.printer.lookup_object("print_stats")
        self.ready = True
        self.last_status = self._collect_status(self.reactor.monotonic())
        self.ui.status = self.last_status
        self.ui.initialize()
        self.reactor.update_timer(self.update_timer, self.reactor.NOW)

    def _handle_disconnect(self):
        self.ready = False
        self.reactor.update_timer(self.update_timer, self.reactor.NEVER)

    def _handle_shutdown(self):
        if self.ready:
            message = self.printer.get_state_message()[0]
            self.ui.show_message("KLIPPER SHUTDOWN", message,
                                 self.ui.show_dashboard, self.ui.ERROR)

    def _handle_mcu_error(self, message, details):
        if self.ready:
            detail = details.get("error", message)
            self.ui.show_message("MCU ERROR", detail,
                                 self.ui.show_dashboard, self.ui.ERROR)

    def _handle_display_data(self, data):
        # The stock encoder is wired to the mainboard. Display responses are
        # retained for diagnostics, but are not used as UI input.
        logging.debug("E3V3SE display response: %s",
                      binascii.hexlify(data).decode("ascii"))

    @staticmethod
    def _tuple4(value):
        result = tuple(value)
        return (result + (0., 0., 0., 0.))[:4]

    def _collect_status(self, eventtime):
        hotend = self.extruder.get_status(eventtime)
        bed = self.heater_bed.get_status(eventtime)
        fan = self.fan.get_status(eventtime) if self.fan is not None else {}
        toolhead = self.toolhead.get_status(eventtime)
        gmove = self.gcode_move.get_status(eventtime)
        stats = self.print_stats.get_status(eventtime)
        sdcard = self.virtual_sdcard.get_status(eventtime)
        pause = self.pause_resume.get_status(eventtime)
        dstatus = self.display_status.get_status(eventtime)
        state_message, state_category = self.printer.get_state_message()
        return {
            "hotend_temp": hotend.get("temperature", 0.),
            "hotend_target": hotend.get("target", 0.),
            "can_extrude": hotend.get("can_extrude", False),
            "bed_temp": bed.get("temperature", 0.),
            "bed_target": bed.get("target", 0.),
            "fan": fan.get("speed", 0.),
            "position": self._tuple4(gmove.get("gcode_position", (0.,) * 4)),
            "axis_minimum": self._tuple4(
                toolhead.get("axis_minimum", (None,) * 4)),
            "axis_maximum": self._tuple4(
                toolhead.get("axis_maximum", (None,) * 4)),
            "homed_axes": toolhead.get("homed_axes", ""),
            "z_offset": gmove.get("homing_origin", (0., 0., 0., 0.))[2],
            "speed_factor": gmove.get("speed_factor", 1.) * 100.,
            "extrude_factor": gmove.get("extrude_factor", 1.) * 100.,
            "print_state": stats.get("state", "standby"),
            "filename": stats.get("filename", ""),
            "print_duration": stats.get("print_duration", 0.),
            "progress": dstatus.get("progress", sdcard.get("progress", 0.)),
            "display_message": dstatus.get("message"),
            "paused": pause.get("is_paused", False),
            "state_category": state_category,
            "state_message": state_message,
        }

    def _update_event(self, eventtime):
        if not self.ready:
            return self.reactor.NEVER
        try:
            self.transport.request_status()
            self.last_status = self._collect_status(eventtime)
            manual_status = self.manual_probe.get_status(eventtime)
            manual_active = manual_status.get("is_active", False)
            if manual_active:
                self.ui.status = self.last_status
                self.ui.update_manual_probe(manual_status.get("z_position"))
            else:
                if self._manual_probe_was_active:
                    self.ui.show_message(
                        "Z CALIBRATION", "Calibration finished or aborted",
                        self.ui.show_calibration_menu)
                self.ui.update(self.last_status)
            self._manual_probe_was_active = manual_active
        except Exception:
            # A display failure must never stop motion or the MCU link.
            logging.exception("E3V3SE display update failed")
        return eventtime + self.UPDATE_INTERVAL

    def _handle_key(self, key, eventtime):
        try:
            manual_status = self.manual_probe.get_status(eventtime)
            if manual_status.get("is_active", False):
                if key in ("up", "fast_up"):
                    step = 0.25 if key == "fast_up" else 0.05
                    self._execute("TESTZ Z=%.3f" % step)
                elif key in ("down", "fast_down"):
                    step = -0.25 if key == "fast_down" else -0.05
                    self._execute("TESTZ Z=%.3f" % step)
                elif key == "click":
                    self._execute("ACCEPT")
                elif key == "long_click":
                    self._execute("ABORT")
                return
            self.ui.handle_key(key)
        except Exception:
            logging.exception("E3V3SE display key handling failed")

    def _execute(self, script):
        try:
            self.gcode.run_script(script)
            return True, ""
        except self.printer.command_error as exc:
            logging.warning("E3V3SE display command failed: %s", exc)
            return False, str(exc)
        except Exception as exc:
            logging.exception("E3V3SE display command failed")
            return False, str(exc)

    def command_available(self, command):
        commands = self.gcode.get_status(
            self.reactor.monotonic()).get("commands", {})
        return command in commands

    def home_all(self):
        return self._execute("G28")

    def disable_motors(self):
        return self._execute("M84")

    def pause_print(self):
        return self._execute("PAUSE")

    def resume_print(self):
        return self._execute("RESUME")

    def cancel_print(self):
        return self._execute("CANCEL_PRINT")

    def save_config(self):
        return self._execute("SAVE_CONFIG")

    def cooldown(self):
        return self._execute("TURN_OFF_HEATERS\nM107")

    def _set_preheat(self, values):
        return self._execute(
            "SET_HEATER_TEMPERATURE HEATER=extruder TARGET=%d\n"
            "SET_HEATER_TEMPERATURE HEATER=heater_bed TARGET=%d" % values)

    def preheat_pla(self):
        return self._set_preheat(self.preheat_pla_temps)

    def preheat_petg(self):
        return self._set_preheat(self.preheat_petg_temps)

    def set_hotend(self, value):
        return self._execute(
            "SET_HEATER_TEMPERATURE HEATER=extruder TARGET=%.0f" % value)

    def set_bed(self, value):
        return self._execute(
            "SET_HEATER_TEMPERATURE HEATER=heater_bed TARGET=%.0f" % value)

    def set_fan(self, value):
        value = max(0., min(100., value))
        return self._execute("M106 S%d" % int(round(value * 2.55)))

    def set_speed_factor(self, value):
        return self._execute("M220 S%.0f" % value)

    def set_extrude_factor(self, value):
        return self._execute("M221 S%.0f" % value)

    def set_z_offset(self, delta):
        if "z" not in self.last_status.get("homed_axes", ""):
            return False, "Home Z before changing the live Z offset"
        return self._execute(
            "SET_GCODE_OFFSET Z_ADJUST=%.3f MOVE=1" % delta)

    def move_axis(self, axis, delta):
        if axis in "XYZ" and axis.lower() not in self.last_status.get(
                "homed_axes", ""):
            return False, "Home %s before moving it" % axis
        if axis == "E" and not self.last_status.get("can_extrude", False):
            return False, "Extruder is below the minimum extrusion temperature"
        speed = 300 if axis in "ZE" else 3000
        setup = self._execute(
            "SAVE_GCODE_STATE NAME=E3V3SE_DISPLAY_MOVE\nG91\nM83")
        if not setup[0]:
            return setup
        move_result = self._execute("G1 %s%.3f F%d" % (axis, delta, speed))
        restore_result = self._execute(
            "RESTORE_GCODE_STATE NAME=E3V3SE_DISPLAY_MOVE")
        return move_result if not move_result[0] else restore_result

    def probe_calibrate(self):
        return self._execute("G28\nPROBE_CALIBRATE")

    def bed_mesh_calibrate(self):
        return self._execute("G28\nBED_MESH_CALIBRATE")

    def screws_tilt(self):
        return self._execute("G28\nSCREWS_TILT_CALCULATE")

    def load_filament(self):
        if not self.command_available("LOAD_FILAMENT"):
            return False, "Configure the LOAD_FILAMENT macro first"
        if not self.last_status.get("can_extrude", False):
            return False, "Heat the hotend before loading filament"
        return self._execute("LOAD_FILAMENT")

    def unload_filament(self):
        if not self.command_available("UNLOAD_FILAMENT"):
            return False, "Configure the UNLOAD_FILAMENT macro first"
        if not self.last_status.get("can_extrude", False):
            return False, "Heat the hotend before unloading filament"
        return self._execute("UNLOAD_FILAMENT")

    def list_files(self):
        return self.virtual_sdcard.get_file_list(True)

    def start_print(self, filename):
        try:
            filename = validate_filename(filename)
            gcmd = self.gcode.create_gcode_command(
                "SDCARD_PRINT_FILE", "SDCARD_PRINT_FILE", {
                    "FILENAME": filename})
            with self.gcode.get_mutex():
                self.virtual_sdcard.cmd_SDCARD_PRINT_FILE(gcmd)
            return True, ""
        except ValueError as exc:
            return False, str(exc)
        except self.printer.command_error as exc:
            logging.warning("E3V3SE display print start failed: %s", exc)
            return False, str(exc)
        except Exception as exc:
            logging.exception("E3V3SE display print start failed")
            return False, str(exc)

    def run_macro(self, macro):
        try:
            script = macro.template.render()
        except Exception as exc:
            logging.exception("Unable to render E3V3SE display macro")
            return False, str(exc)
        return self._execute(script)

    def cmd_ENDER3V3SE_DISPLAY_REFRESH(self, gcmd):
        if self.ready:
            self.ui.show_dashboard()

    def cmd_ENDER3V3SE_DISPLAY_STATUS(self, gcmd):
        status = self.transport.get_status()
        gcmd.respond_info(
            "ready=%s pending_bytes=%d dropped_frames=%d "
            "mcu_rx_overflows=%d mcu_tx_overflows=%d"
            % (status["ready"], status["pending_bytes"],
               status["dropped_frames"], status["mcu_rx_overflows"],
               status["mcu_tx_overflows"]))

    def get_status(self, eventtime):
        status = self.transport.get_status()
        status.update({"screen": self.ui.mode,
                       "display_ready": self.ready})
        return status


def load_config(config):
    return E3V3SEDisplay(config)


def load_config_prefix(config):
    return E3V3SEDisplayMacro(config)
