# Ender 3 V3 SE display transport
#
# Copyright (C) 2026 Bernardo Costa
#
# This file may be distributed under the terms of the GNU GPLv3 license.

import collections
import logging


DEFAULT_CHUNK_SIZE = 40
MAX_PENDING_BYTES = 4096


def split_chunks(data, size=DEFAULT_CHUNK_SIZE):
    """Return independently encodable byte chunks."""
    data = bytearray(data)
    return [bytes(data[pos:pos + size])
            for pos in range(0, len(data), size)]


class DisplayTransport:
    def __init__(self, config, response_callback=None):
        self._config_error = config.error
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.baud = config.getint("display_baud", 115200,
                                  minval=1200, maxval=1000000)
        mcu_name = config.get("mcu", "mcu")
        if mcu_name == "mcu":
            self.mcu = self.printer.lookup_object(mcu_name)
        else:
            self.mcu = self.printer.lookup_object("mcu " + mcu_name)
        self.oid = self.mcu.create_oid()
        self._send_cmd = None
        self._status_cmd = None
        self._response = None
        self._status_response = None
        self._response_callback = response_callback
        self._ready = False
        self._chunks = collections.deque()
        self._pending_bytes = 0
        self._dropped_frames = 0
        self._mcu_rx_overflows = 0
        self._mcu_tx_overflows = 0
        self._timer = self.reactor.register_timer(self._send_event)
        self.mcu.register_config_callback(self._build_config)
        self.printer.register_event_handler("klippy:ready", self._handle_ready)
        self.printer.register_event_handler("klippy:disconnect",
                                            self._handle_disconnect)

    def _build_config(self):
        constants = self.mcu.get_constants()
        if int(constants.get("E3V3SE_DISPLAY_BRIDGE", 0)) != 1:
            raise self._config_error(
                "The MCU firmware does not contain the Ender 3 V3 SE "
                "display bridge. Rebuild and flash the MCU from this same "
                "commit with CONFIG_E3V3SE_DISPLAY enabled.")
        chunk_size = int(constants.get("E3V3SE_DISPLAY_MAX_CHUNK",
                                       DEFAULT_CHUNK_SIZE))
        if chunk_size < DEFAULT_CHUNK_SIZE:
            raise self._config_error(
                "MCU display bridge chunk size is incompatible with Klippy")
        msgformat = "e3v3se_display_send oid=%c data=%*s"
        self._send_cmd = self.mcu.try_lookup_command(msgformat)
        if self._send_cmd is None:
            raise self._config_error(
                "MCU display bridge commands are missing. Rebuild and flash "
                "the MCU from the same commit as Klippy.")
        self._status_cmd = self.mcu.try_lookup_command(
            "e3v3se_display_get_status oid=%c")
        if self._status_cmd is None:
            raise self._config_error(
                "MCU display bridge status command is missing. Rebuild and "
                "flash the MCU from the same commit as Klippy.")
        self._response = self.mcu.register_serial_response(
            self._handle_response,
            "e3v3se_display_response oid=%c data=%*s", self.oid)
        self._status_response = self.mcu.register_serial_response(
            self._handle_status,
            "e3v3se_display_status oid=%c rx_overflows=%u "
            "tx_overflows=%u", self.oid)
        self.mcu.add_config_cmd(
            "config_e3v3se_display oid=%d baud=%d"
            % (self.oid, self.baud))

    def _handle_ready(self):
        self._ready = True
        self.request_status()
        if self._chunks:
            self.reactor.update_timer(self._timer, self.reactor.NOW)

    def _handle_disconnect(self):
        self._ready = False
        self._chunks.clear()
        self._pending_bytes = 0
        self._mcu_rx_overflows = 0
        self._mcu_tx_overflows = 0
        self.reactor.update_timer(self._timer, self.reactor.NEVER)

    def _handle_response(self, params):
        if self._response_callback is not None:
            self._response_callback(bytes(bytearray(params["data"])))

    def _handle_status(self, params):
        rx_overflows = int(params["rx_overflows"])
        tx_overflows = int(params["tx_overflows"])
        if (rx_overflows > self._mcu_rx_overflows
                or tx_overflows > self._mcu_tx_overflows):
            logging.warning(
                "E3V3SE display MCU overflow: rx=%d tx=%d",
                rx_overflows, tx_overflows)
        self._mcu_rx_overflows = rx_overflows
        self._mcu_tx_overflows = tx_overflows

    def request_status(self):
        if self._ready and self._status_cmd is not None:
            self._status_cmd.send([self.oid])

    def send(self, frame):
        frame = bytes(bytearray(frame))
        if not frame:
            return True
        if self._pending_bytes + len(frame) > MAX_PENDING_BYTES:
            self._dropped_frames += 1
            logging.warning(
                "E3V3SE display queue full; dropping a %d-byte frame",
                len(frame))
            return False
        chunks = split_chunks(frame)
        self._chunks.extend(chunks)
        self._pending_bytes += len(frame)
        if self._ready:
            self.reactor.update_timer(self._timer, self.reactor.NOW)
        return True

    def _send_event(self, eventtime):
        if not self._ready or self._send_cmd is None or not self._chunks:
            return self.reactor.NEVER
        chunk = self._chunks.popleft()
        self._pending_bytes -= len(chunk)
        try:
            self._send_cmd.send([self.oid, chunk])
        except Exception:
            self._chunks.appendleft(chunk)
            self._pending_bytes += len(chunk)
            logging.exception("Unable to send E3V3SE display data to the MCU")
            return self.reactor.NEVER
        if not self._chunks:
            return self.reactor.NEVER
        # Ten serial bits per byte plus one millisecond of scheduling margin.
        return eventtime + max(0.002, len(chunk) * 10.0 / self.baud + 0.001)

    def get_status(self):
        return {
            "ready": self._ready,
            "pending_bytes": self._pending_bytes,
            "dropped_frames": self._dropped_frames,
            "mcu_rx_overflows": self._mcu_rx_overflows,
            "mcu_tx_overflows": self._mcu_tx_overflows,
        }
