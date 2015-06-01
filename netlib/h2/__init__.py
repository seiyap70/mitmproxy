from __future__ import (absolute_import, print_function, division)
import itertools

from .. import utils
from .frame import *


class HTTP2Protocol(object):

    ERROR_CODES = utils.BiDi(
        NO_ERROR=0x0,
        PROTOCOL_ERROR=0x1,
        INTERNAL_ERROR=0x2,
        FLOW_CONTROL_ERROR=0x3,
        SETTINGS_TIMEOUT=0x4,
        STREAM_CLOSED=0x5,
        FRAME_SIZE_ERROR=0x6,
        REFUSED_STREAM=0x7,
        CANCEL=0x8,
        COMPRESSION_ERROR=0x9,
        CONNECT_ERROR=0xa,
        ENHANCE_YOUR_CALM=0xb,
        INADEQUATE_SECURITY=0xc,
        HTTP_1_1_REQUIRED=0xd
    )

    # "PRI * HTTP/2.0\r\n\r\nSM\r\n\r\n"
    CLIENT_CONNECTION_PREFACE = '505249202a20485454502f322e300d0a0d0a534d0d0a0d0a'

    ALPN_PROTO_H2 = b'h2'

    HTTP2_DEFAULT_SETTINGS = {
        SettingsFrame.SETTINGS.SETTINGS_HEADER_TABLE_SIZE: 4096,
        SettingsFrame.SETTINGS.SETTINGS_ENABLE_PUSH: 1,
        SettingsFrame.SETTINGS.SETTINGS_MAX_CONCURRENT_STREAMS: None,
        SettingsFrame.SETTINGS.SETTINGS_INITIAL_WINDOW_SIZE: 2 ** 16 - 1,
        SettingsFrame.SETTINGS.SETTINGS_MAX_FRAME_SIZE: 2 ** 14,
        SettingsFrame.SETTINGS.SETTINGS_MAX_HEADER_LIST_SIZE: None,
    }

    def __init__(self):
        self.http2_settings = self.HTTP2_DEFAULT_SETTINGS.copy()
        self.current_stream_id = None
        self.encoder = Encoder()
        self.decoder = Decoder()

    def check_alpn(self):
        alp = self.get_alpn_proto_negotiated()
        if alp != self.ALPN_PROTO_H2:
            raise NotImplementedError(
                "H2Client can not handle unknown ALP: %s" % alp)
        print("-> Successfully negotiated 'h2' application layer protocol.")

    def send_connection_preface(self):
        self.wfile.write(bytes(self.CLIENT_CONNECTION_PREFACE.decode('hex')))
        self.send_frame(SettingsFrame(state=self))

        frame = Frame.from_file(self.rfile, self)
        assert isinstance(frame, SettingsFrame)
        self._apply_settings(frame.settings)
        self.read_frame()  # read setting ACK frame

        print("-> Connection Preface completed.")

    def next_stream_id(self):
        if self.current_stream_id is None:
            self.current_stream_id = 1
        else:
            self.current_stream_id += 2
        return self.current_stream_id

    def send_frame(self, frame):
        raw_bytes = frame.to_bytes()
        self.wfile.write(raw_bytes)
        self.wfile.flush()

    def read_frame(self):
        frame = Frame.from_file(self.rfile, self)
        if isinstance(frame, SettingsFrame):
            self._apply_settings(frame.settings)

        return frame

    def _apply_settings(self, settings):
        for setting, value in settings.items():
            old_value = self.http2_settings[setting]
            if not old_value:
                old_value = '-'

            self.http2_settings[setting] = value
            print("-> Setting changed: %s to %d (was %s)" % (
                SettingsFrame.SETTINGS.get_name(setting),
                value,
                str(old_value)))

        self.send_frame(SettingsFrame(state=self, flags=Frame.FLAG_ACK))
        print("-> New settings acknowledged.")

    def _create_headers(self, headers, stream_id, end_stream=True):
        # TODO: implement max frame size checks and sending in chunks

        flags = Frame.FLAG_END_HEADERS
        if end_stream:
            flags |= Frame.FLAG_END_STREAM

        bytes = HeadersFrame(
            state=self,
            flags=flags,
            stream_id=stream_id,
            headers=headers).to_bytes()
        return [bytes]

    def _create_body(self, body, stream_id):
        if body is None or len(body) == 0:
            return b''

        # TODO: implement max frame size checks and sending in chunks
        # TODO: implement flow-control window

        bytes = DataFrame(
            state=self,
            flags=Frame.FLAG_END_STREAM,
            stream_id=stream_id,
            payload=body).to_bytes()
        return [bytes]

    def create_request(self, method, path, headers=None, body=None):
        if headers is None:
            headers = []

        headers = [
            (b':method', bytes(method)),
            (b':path', bytes(path)),
            (b':scheme', b'https')] + headers

        stream_id = self.next_stream_id()

        return list(itertools.chain(
            self._create_headers(headers, stream_id, end_stream=(body is None)),
            self._create_body(body, stream_id)))

    def read_response(self):
        header_block_fragment = b''
        body = b''

        while True:
            frame = self.read_frame()
            if isinstance(frame, HeadersFrame):
                header_block_fragment += frame.header_block_fragment
                if frame.flags | Frame.FLAG_END_HEADERS:
                    break
            else:
                print("Unexpected frame received:")
                print(frame.human_readable())

        while True:
            frame = self.read_frame()
            if isinstance(frame, DataFrame):
                body += frame.payload
                if frame.flags | Frame.FLAG_END_STREAM:
                    break
            else:
                print("Unexpected frame received:")
                print(frame.human_readable())

        headers = {}
        for header, value in self.decoder.decode(header_block_fragment):
            headers[header] = value

        return headers[':status'], headers, body
