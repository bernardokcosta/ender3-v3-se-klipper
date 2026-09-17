import os
import struct
import sys
import threading
import unittest


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "klippy"))

from extras.e3v3se_display import E3V3SEDisplay
from extras.e3v3se_display.model import FileBrowser, validate_filename
from extras.e3v3se_display.tjc3224 import (FRAME_TAIL, TJC3224,
                                           safe_text_bytes, utf8_truncate)
from extras.e3v3se_display.transport import split_chunks
from extras.e3v3se_display.ui import DisplayUI
from configfile import ConfigAutoSave, ConfigFileReader


class FakeTransport:
    def __init__(self):
        self.frames = []

    def send(self, frame):
        self.frames.append(bytes(frame))
        return True


class TJC3224Test(unittest.TestCase):
    def test_instances_never_share_frame_state(self):
        lengths = []
        for unused_index in range(100):
            transport = FakeTransport()
            display = TJC3224(transport)
            display.set_palette(0xffff, 0)
            lengths.append(len(transport.frames[0]))
        self.assertEqual([10] * 100, lengths)

    def test_negative_values_are_signed(self):
        transport = FakeTransport()
        display = TJC3224(transport)
        frame = display.value(10, 10, -123, signed=True)
        self.assertTrue(frame[2] & 0x40)
        self.assertEqual(-123, struct.unpack(">q", frame[-12:-4])[0])
        self.assertEqual(FRAME_TAIL, frame[-4:])

    def test_utf8_is_truncated_on_byte_boundary(self):
        text = u"arquivo-\u00e7\u00e3o"
        truncated = utf8_truncate(text, 9)
        encoded = truncated.encode("utf-8")
        self.assertEqual(u"arquivo-", truncated)
        self.assertLessEqual(len(encoded), 9)
        self.assertEqual(truncated, encoded.decode("utf-8"))

    def test_protocol_tail_is_not_embedded_in_text(self):
        encoded = safe_text_bytes(u"name\u00cc3\u00c3<.gcode", 100)
        self.assertNotIn(FRAME_TAIL, encoded)


class TransportTest(unittest.TestCase):
    def test_large_utf8_frame_is_chunked_and_reassembled(self):
        frame = (u"long-" + u"arquivo-" * 30).encode("utf-8")
        chunks = split_chunks(frame)
        self.assertTrue(all(0 < len(chunk) <= 40 for chunk in chunks))
        self.assertEqual(frame, b"".join(chunks))


class FileBrowserTest(unittest.TestCase):
    def test_empty_directory(self):
        browser = FileBrowser()
        self.assertEqual([], browser.update([]))

    def test_files_directories_utf8_and_special_characters(self):
        browser = FileBrowser()
        entries = browser.update([
            (u"cube.gcode", 1),
            (u"models/peca acentuada.gcode", 2),
            (u"models/deep/quoted'name.gcode", 3),
        ])
        self.assertEqual([u"models", u"cube.gcode"],
                         [entry.name for entry in entries])
        self.assertTrue(entries[0].is_dir)
        self.assertIsNone(browser.enter(entries[0]))
        entries = browser.update([
            (u"models/peca acentuada.gcode", 2),
            (u"models/deep/quoted'name.gcode", 3),
        ])
        self.assertEqual([u"deep", u"peca acentuada.gcode"],
                         [entry.name for entry in entries])
        self.assertEqual(u"models/peca acentuada.gcode",
                         browser.enter(entries[1]))
        self.assertTrue(browser.back())
        self.assertEqual("", browser.path)

    def test_special_filename_validation_and_control_rejection(self):
        filename = "dir/a file's #1;copy\\name.gcode"
        self.assertEqual(filename, validate_filename(filename))
        with self.assertRaises(ValueError):
            validate_filename("bad\nRESTART.gcode")

    def test_backslash_is_not_treated_as_a_directory_separator(self):
        browser = FileBrowser()
        entries = browser.update([("part\\name.gcode", 1)])
        self.assertEqual("part\\name.gcode", entries[0].name)


class DisplayActionTest(unittest.TestCase):
    def test_relative_adjustment_uses_only_effective_bounded_delta(self):
        ui = DisplayUI.__new__(DisplayUI)
        values = []
        ui.adjustment = {
            "value": 9., "step": 5., "minimum": 0., "maximum": 10.,
            "callback": lambda value: (values.append(value) or (True, "")),
            "relative": True,
        }
        ui._draw_adjustment = lambda: None
        ui._adjust(1)
        ui._adjust(1)
        self.assertEqual([1.], values)
        self.assertEqual(10., ui.adjustment["value"])

    def test_move_failure_still_restores_gcode_state(self):
        display = E3V3SEDisplay.__new__(E3V3SEDisplay)
        display.last_status = {"homed_axes": "xyz", "can_extrude": True}
        calls = []

        def execute(script):
            calls.append(script)
            if script.startswith("G1"):
                return False, "move failed"
            return True, ""

        display._execute = execute
        result = display.move_axis("X", 1.)
        self.assertFalse(result[0])
        self.assertTrue(calls[-1].startswith("RESTORE_GCODE_STATE"))

    def test_cold_filament_action_is_rejected_before_macro(self):
        display = E3V3SEDisplay.__new__(E3V3SEDisplay)
        display.last_status = {"can_extrude": False}
        display.command_available = lambda command: True
        display._execute = lambda script: self.fail("macro must not run")
        self.assertFalse(display.load_filament()[0])
        self.assertFalse(display.unload_filament()[0])

    def test_start_print_passes_special_filename_without_gcode_parsing(self):
        class CommandError(Exception):
            pass

        class FakeGCode:
            def __init__(self):
                self.params = None
                self.mutex = threading.Lock()

            def create_gcode_command(self, command, commandline, params):
                self.params = params
                return params

            def get_mutex(self):
                return self.mutex

        class FakeVirtualSD:
            def __init__(self):
                self.filename = None

            def cmd_SDCARD_PRINT_FILE(self, gcmd):
                self.filename = gcmd["FILENAME"]

        display = E3V3SEDisplay.__new__(E3V3SEDisplay)
        display.gcode = FakeGCode()
        display.virtual_sdcard = FakeVirtualSD()
        display.printer = type("Printer", (), {
            "command_error": CommandError})()
        filename = "models/a file's #1;copy\\name.gcode"
        result = display.start_print(filename)
        self.assertTrue(result[0])
        self.assertEqual(filename, display.virtual_sdcard.filename)


class ConfigLayoutTest(unittest.TestCase):
    def test_autosave_values_do_not_conflict_with_includes(self):
        config_path = os.path.join(
            ROOT, "config",
            "printer-creality-ender3-v3-se-gd32f303-2023.cfg")
        reader = ConfigFileReader()
        regular_data = reader.read_config_file(config_path)
        autosave = reader.build_fileconfig(
            "[extruder]\ncontrol: pid\n"
            "[heater_bed]\ncontrol: pid\n"
            "[bltouch]\nz_offset: 1.25\n", "*AUTOSAVE*")
        helper = ConfigAutoSave.__new__(ConfigAutoSave)
        stripped = helper._strip_duplicates(regular_data, autosave)
        merged = reader.build_fileconfig_with_includes(stripped, config_path)
        self.assertFalse(merged.has_option("extruder", "control"))
        self.assertFalse(merged.has_option("heater_bed", "control"))
        self.assertFalse(merged.has_option("bltouch", "z_offset"))


if __name__ == "__main__":
    unittest.main()
