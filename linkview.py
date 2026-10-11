#!/usr/bin/env python3
"""A small native Cam Link viewer for Linux. No root access required."""
import argparse
import concurrent.futures
import json
import logging
from logging.handlers import RotatingFileHandler
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time

# GTK's Vulkan renderer can fail allocations on this NVIDIA desktop. GL keeps
# hardware rendering available; respect an explicitly selected renderer.
os.environ.setdefault('GSK_RENDERER', 'gl')

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Gdk', '4.0')
gi.require_version('Gst', '1.0')
gi.require_version('GLibUnix', '2.0')
from gi.repository import Gdk, GLib, GLibUnix, Gst, Gtk

Gst.init(None)
LOG = logging.getLogger('linkview')
CONFIG = Path.home() / '.config/linkview/settings.json'


def command(*args):
    result = subprocess.run(args, capture_output=True, text=True, timeout=6)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return result.stdout


def discover():
    """Pair video and audio by USB serial, never by transient device numbers."""
    videos = []
    for entry in sorted(Path('/sys/class/video4linux').glob('video*')):
        name = (entry / 'name').read_text().strip()
        if 'cam link' not in name.lower():
            continue
        dev = '/dev/' + entry.name
        info = command('v4l2-ctl', '-d', dev, '--all')
        if 'Video Capture' not in info or 'Format Video Capture:' not in info:
            continue
        serial = ''
        for parent in entry.resolve().parents:
            if (parent / 'idVendor').exists():
                serial_file = parent / 'serial'
                serial = serial_file.read_text().strip() if serial_file.exists() else ''
                break
        modes = []
        fmt = size = None
        for line in command('v4l2-ctl', '-d', dev, '--list-formats-ext').splitlines():
            match = re.search(r"\[\d+\]: '([^']+)'", line)
            if match:
                fmt = match[1]
                size = None
            match = re.search(r'Size: Discrete (\d+)x(\d+)', line)
            if match:
                size = (int(match[1]), int(match[2]))
            match = re.search(r'\(([\d.]+) fps\)', line)
            if match and size and fmt in ('NV12', 'YUYV', 'MJPG'):
                mode = (fmt, *size, float(match[1]))
                if mode not in modes:
                    modes.append(mode)
        # YUYV eliminated motion lines in the Switch/Cam Link comparison.
        # Prefer it over NV12 when resolution and frame rate are identical.
        modes.sort(key=lambda m: (m[1] * m[2], m[3], m[0] == 'YUYV'), reverse=True)
        videos.append({'device': dev, 'serial': serial, 'name': name, 'modes': modes})
    sources = json.loads(command('pactl', '-f', 'json', 'list', 'sources'))
    sinks = json.loads(command('pactl', '-f', 'json', 'list', 'sinks'))
    audio = [s for s in sources if 'cam_link' in s['name'].lower()]
    for video in videos:
        matching = [s for s in audio if video['serial'] and video['serial'] in s['name']]
        # Do not guess a pairing when multiple identical devices are present.
        video['audio'] = matching[0]['name'] if matching else (audio[0]['name'] if len(audio) == len(videos) == 1 else None)
    return videos, [(s['name'], s.get('description', s['name'])) for s in sinks]


class LinkView(Gtk.Application):
    def __init__(self, args):
        super().__init__(application_id='io.github.linkview.Viewer')
        self.args = args
        self.worker = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        self.video = self.audio = self.video_sink = self.volume_element = None
        self.device = None
        self.modes = []
        self.outputs = [None]
        self.scanning = self.updating = False
        self.want_playing = True
        self.closed = False
        self.fullscreen = False
        self.last_level = 0
        self.last_sound = time.monotonic()
        self.level_messages = 0
        self.stream_warnings = 0
        self.sound_messages = 0
        self.video_errors = self.audio_errors = 0
        self.settings = {}
        try:
            self.settings = json.loads(CONFIG.read_text())
        except (OSError, ValueError):
            pass

    def do_activate(self):
        if hasattr(self, 'window'):
            self.window.present()
            return
        self.window = Gtk.ApplicationWindow(application=self, title='LinkView · Switch viewer')
        self.window.set_default_size(1120, 740)
        self.window.connect('close-request', self.close)
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        header = Gtk.HeaderBar()
        title = Gtk.Label(label='LinkView')
        title.add_css_class('title')
        header.set_title_widget(title)
        fullscreen = Gtk.Button(label='Fullscreen')
        fullscreen.connect('clicked', lambda *_: self.toggle_fullscreen())
        header.pack_end(fullscreen)
        self.window.set_titlebar(header)
        self.controls = Gtk.Box(spacing=10)
        for setter in ('set_margin_top', 'set_margin_bottom', 'set_margin_start', 'set_margin_end'):
            getattr(self.controls, setter)(12)
        self.play = Gtk.Button(label='Pause')
        self.play.connect('clicked', self.toggle_play)
        self.controls.append(self.play)
        self.mode = Gtk.DropDown.new_from_strings(['Detecting Cam Link…'])
        self.mode.connect('notify::selected', self.mode_changed)
        self.controls.append(self.mode)
        refresh = Gtk.Button(label='Reconnect')
        refresh.connect('clicked', self.reconnect)
        self.controls.append(refresh)
        self.controls.append(Gtk.Label(label='Sound'))
        self.monitor = Gtk.Switch(active=bool(self.settings.get('monitor', True)))
        self.monitor.connect('notify::active', self.sound_changed)
        self.controls.append(self.monitor)
        self.volume = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
        self.volume.set_size_request(100, -1)
        self.volume.set_value(float(self.settings.get('volume', 65)))
        self.volume.set_draw_value(False)
        self.volume.connect('value-changed', self.sound_changed)
        self.controls.append(self.volume)
        root.append(self.controls)
        self.overlay = Gtk.Overlay(vexpand=True, hexpand=True)
        self.overlay.add_css_class('preview')
        self.picture = Gtk.Picture(hexpand=True, vexpand=True)
        self.picture.set_content_fit(Gtk.ContentFit.CONTAIN)
        self.picture.set_can_shrink(True)
        self.overlay.set_child(self.picture)
        self.message = Gtk.Label(label='Looking for your Cam Link…', wrap=True)
        self.message.set_halign(Gtk.Align.CENTER)
        self.message.set_valign(Gtk.Align.CENTER)
        self.overlay.add_overlay(self.message)
        root.append(self.overlay)
        self.footer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        for setter in ('set_margin_top', 'set_margin_bottom', 'set_margin_start', 'set_margin_end'):
            getattr(self.footer, setter)(12)
        row = Gtk.Box(spacing=12)
        row.append(Gtk.Label(label='Play through'))
        self.output = Gtk.DropDown.new_from_strings(['System default'])
        self.output.set_hexpand(True)
        self.output.connect('notify::selected', self.output_changed)
        row.append(self.output)
        self.meter = Gtk.LevelBar(min_value=0, max_value=1)
        self.meter.set_size_request(150, -1)
        row.append(self.meter)
        self.db = Gtk.Label(label='— dB')
        row.append(self.db)
        self.footer.append(row)
        self.status = Gtk.Label(label='Starting…', xalign=0)
        self.footer.append(self.status)
        self.audio_status = Gtk.Label(label='Audio: waiting for device', xalign=0, wrap=True)
        self.footer.append(self.audio_status)
        root.append(self.footer)
        self.window.set_child(root)
        keys = Gtk.EventControllerKey()
        keys.connect('key-pressed', self.key_pressed)
        self.window.add_controller(keys)
        css = Gtk.CssProvider()
        css.load_from_data(b'.preview { background: #050708; color: #bcc5ce; } window { background: #171c22; color: #e8edf2; }')
        Gtk.StyleContext.add_provider_for_display(self.window.get_display(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.window.present()
        GLibUnix.signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, self.terminate)
        GLib.timeout_add_seconds(3, self.tick)
        self.scan()
        if self.args.smoke_test:
            GLib.timeout_add_seconds(self.args.smoke_test, self.finish_smoke)

    def scan(self):
        if self.scanning or self.closed:
            return
        self.scanning = True
        future = self.worker.submit(discover)
        future.add_done_callback(lambda f: GLib.idle_add(self.discovered, f))

    def discovered(self, future):
        self.scanning = False
        if self.closed:
            return False
        try:
            devices, sinks = future.result()
        except Exception as error:
            LOG.exception('Device discovery failed')
            self.status.set_text('Cannot inspect devices: ' + str(error))
            return False
        if not devices:
            self.stop_video()
            self.stop_audio()
            self.device = None
            self.message.set_text('Connect your Cam Link and wake the Switch.')
            self.message.set_visible(True)
            self.status.set_text('Cam Link disconnected · retrying automatically')
            self.audio_status.set_text('Audio: no Cam Link connected')
            return False
        device = next((d for d in devices if d['serial'] == self.settings.get('serial')), devices[0])
        changed = self.device != device
        self.device = device
        if not device['modes']:
            self.status.set_text('Cam Link found, but no supported capture formats were reported.')
            return False
        self.updating = True
        self.modes = device['modes']
        self.mode.set_model(Gtk.StringList.new([f'{w} × {h} · {fps:g} fps · {fmt}' for fmt, w, h, fps in self.modes]))
        saved = self.settings.get('mode')
        preferred = next((i for i, m in enumerate(self.modes) if list(m) == saved), 0)
        requested = getattr(self.args, 'capture_format', None)
        if requested:
            preferred = next((i for i, m in enumerate(self.modes) if m[0] == requested), preferred)
        self.mode.set_selected(preferred)
        self.outputs = [None] + [name for name, _ in sinks]
        self.output.set_model(Gtk.StringList.new(['System default'] + [desc for _, desc in sinks]))
        selected = self.settings.get('output')
        self.output.set_selected(self.outputs.index(selected) if selected in self.outputs else 0)
        self.updating = False
        if changed:
            self.stop_video()
            self.stop_audio()
        if self.want_playing:
            if not self.video:
                self.start_video()
            if not self.audio:
                self.start_audio()
        return False

    def watch(self, pipeline, kind):
        bus = pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect('message', self.bus_message, kind, pipeline)

    def start_video(self):
        if not self.device or not self.modes:
            return
        fmt, width, height, fps = self.modes[self.mode.get_selected()]
        fraction = Gst.util_double_to_fraction(fps)
        caps = f'width={width},height={height},framerate={fraction[0]}/{fraction[1]}'
        if fmt == 'MJPG':
            capture = f'image/jpeg,{caps} ! jpegdec ! videoconvert'
        else:
            capture = f'video/x-raw,format={"YUY2" if fmt == "YUYV" else fmt},{caps}'
        try:
            # Convert into independently allocated RGB buffers before GTK. This
            # avoids importing the camera's YUV buffers into the GL renderer.
            # Clock scheduling also keeps frame presentation evenly paced.
            pipeline = f'v4l2src name=camera io-mode=mmap do-timestamp=true ! {capture} ! videoconvert ! video/x-raw,format=BGRA ! tee name=frames frames. ! queue max-size-buffers=2 max-size-bytes=0 max-size-time=0 leaky=downstream ! gtk4paintablesink name=preview sync=true processing-deadline=0 max-lateness=30000000'
            diagnostic_dir = getattr(self.args, 'diagnostic_frames', None)
            if diagnostic_dir:
                # Keep a rolling six-second sample without blocking playback.
                Path(diagnostic_dir).mkdir(parents=True, exist_ok=True)
                pipeline += ' frames. ! queue max-size-buffers=1 max-size-bytes=0 max-size-time=0 leaky=downstream ! videorate drop-only=true ! video/x-raw,framerate=4/1 ! videoconvert ! video/x-raw,format=RGB ! pngenc ! multifilesink name=diagnostics max-files=24 sync=false'
            self.video = Gst.parse_launch(pipeline)
            if diagnostic_dir:
                self.video.get_by_name('diagnostics').set_property('location', str(Path(diagnostic_dir) / 'frame-%06d.png'))
            self.video.get_by_name('camera').set_property('device', self.device['device'])
            self.video_sink = self.video.get_by_name('preview')
            self.picture.set_paintable(self.video_sink.get_property('paintable'))
            self.watch(self.video, 'video')
            self.message.set_text('Waiting for video…')
            self.message.set_visible(True)
            if self.video.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
                raise RuntimeError('Could not start video capture')
            self.status.set_text(f'{self.device["name"]} · {width} × {height} · {fps:g} fps · {fmt}')
        except Exception as error:
            self.video_errors += 1
            self.stop_video()
            self.message.set_text('Video unavailable: ' + str(error))
            self.message.set_visible(True)
            LOG.exception('Video startup failed')

    def start_audio(self):
        if not self.device or not self.device['audio']:
            self.audio_status.set_text('Audio: Cam Link input missing · reconnect to retry')
            return
        try:
            # The original 40 ms buffers underrun under real gameplay. Keep a
            # modest reserve and a separate playback thread; never leak audio.
            # Follow the capture clock by resampling at the output instead of
            # periodically skipping/repeating samples to correct clock skew.
            self.audio = Gst.parse_launch('pulsesrc name=input client-name=LinkView buffer-time=120000 latency-time=20000 ! audio/x-raw,rate=48000,channels=2 ! level name=meter interval=100000000 ! queue max-size-buffers=0 max-size-bytes=0 max-size-time=200000000 ! audioconvert ! audioresample ! volume name=volume ! pulsesink name=output client-name=LinkView buffer-time=120000 latency-time=20000 provide-clock=false slave-method=resample')
            self.audio.get_by_name('input').set_property('device', self.device['audio'])
            sink = self.audio.get_by_name('output')
            sink.set_property('device', self.outputs[self.output.get_selected()])
            self.volume_element = self.audio.get_by_name('volume')
            self.sound_changed()
            self.watch(self.audio, 'audio')
            self.last_level = 0
            self.last_sound = time.monotonic()
            self.audio_status.set_text('Audio: waiting for samples…')
            if self.audio.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
                raise RuntimeError('Could not start sound monitoring')
        except Exception as error:
            self.audio_errors += 1
            self.stop_audio()
            self.audio_status.set_text('Audio unavailable: ' + str(error))
            LOG.exception('Audio startup failed')

    def bus_message(self, bus, message, kind, pipeline):
        if pipeline is not (self.video if kind == 'video' else self.audio):
            return
        if message.type == Gst.MessageType.WARNING:
            self.stream_warnings += 1
            warning, debug = message.parse_warning()
            LOG.warning('%s: %s (%s)', kind, warning.message, debug)
        elif message.type == Gst.MessageType.ERROR:
            error, debug = message.parse_error()
            LOG.error('%s: %s (%s)', kind, error.message, debug)
            if kind == 'video':
                self.video_errors += 1
                self.stop_video()
                self.message.set_text('Video: ' + error.message + '\nClose other capture apps, then reconnect.')
                self.message.set_visible(True)
            else:
                self.audio_errors += 1
                self.stop_audio()
                self.audio_status.set_text('Audio: ' + error.message)
        elif message.type == Gst.MessageType.EOS:
            self.stop_video() if kind == 'video' else self.stop_audio()
        elif message.type == Gst.MessageType.ELEMENT and kind == 'audio':
            structure = message.get_structure()
            if structure and structure.get_name() == 'level':
                peaks = structure.get_value('peak')
                peak = max(peaks)
                self.level_messages += 1
                self.last_level = time.monotonic()
                if math.isfinite(peak) and peak > -80:
                    self.last_sound = self.last_level
                    self.sound_messages += 1
                self.db.set_text(f'{peak:.0f} dB' if math.isfinite(peak) else '−∞ dB')
                self.meter.set_value(max(0, min(1, (peak + 60) / 60)))

    def stop_pipeline(self, pipeline):
        if pipeline:
            pipeline.set_state(Gst.State.NULL)
            pipeline.get_bus().remove_signal_watch()

    def stop_video(self):
        self.stop_pipeline(self.video)
        self.video = self.video_sink = None
        self.picture.set_paintable(None)

    def stop_audio(self):
        self.stop_pipeline(self.audio)
        self.audio = self.volume_element = None
        self.meter.set_value(0)
        self.db.set_text('— dB')

    def sound_changed(self, *_):
        if self.volume_element:
            self.volume_element.set_property('volume', self.volume.get_value() / 100)
            self.volume_element.set_property('mute', not self.monitor.get_active())

    def mode_changed(self, *_):
        if not self.updating and self.device and self.modes:
            self.settings['mode'] = list(self.modes[self.mode.get_selected()])
            self.stop_video()
            if self.want_playing:
                self.start_video()

    def output_changed(self, *_):
        if not self.updating and self.device:
            self.settings['output'] = self.outputs[self.output.get_selected()]
            self.stop_audio()
            if self.want_playing:
                self.start_audio()

    def toggle_play(self, *_):
        self.want_playing = not self.want_playing
        self.play.set_label('Pause' if self.want_playing else 'Start')
        if self.want_playing:
            self.scan()
        else:
            self.stop_video()
            self.stop_audio()
            self.message.set_text('Paused')
            self.message.set_visible(True)
            self.audio_status.set_text('Audio: paused')

    def reconnect(self, *_):
        self.stop_video()
        self.stop_audio()
        self.want_playing = True
        self.play.set_label('Pause')
        self.scan()

    def tick(self):
        if self.closed:
            return False
        if self.video_sink:
            stats = self.video_sink.get_property('stats')
            if stats.get_value('rendered') > 0:
                self.message.set_visible(False)
        if self.audio:
            now = time.monotonic()
            if self.last_level and now - self.last_level < 3:
                if now - self.last_sound > 5:
                    self.audio_status.set_text('Audio: receiving silence · Switch → TV Settings → TV Sound: Stereo; check headphones/Bluetooth.')
                else:
                    self.audio_status.set_text('Audio: signal detected' + (' · monitoring off' if not self.monitor.get_active() else ' · playing through selected output'))
            else:
                self.audio_status.set_text('Audio: waiting for samples…')
        if self.want_playing and (not self.video or not self.audio):
            self.scan()
        return True

    def toggle_fullscreen(self):
        self.fullscreen = not self.fullscreen
        self.controls.set_visible(not self.fullscreen)
        self.footer.set_visible(not self.fullscreen)
        self.window.fullscreen() if self.fullscreen else self.window.unfullscreen()

    def key_pressed(self, controller, keyval, keycode, state):
        if keyval in (Gdk.KEY_F11, Gdk.KEY_f, Gdk.KEY_F):
            self.toggle_fullscreen()
            return True
        if keyval == Gdk.KEY_Escape and self.fullscreen:
            self.toggle_fullscreen()
            return True
        return False

    def finish_smoke(self):
        stats = self.video_sink.get_property('stats') if self.video_sink else None
        report = {'rendered': stats.get_value('rendered') if stats else 0,
                  'dropped': stats.get_value('dropped') if stats else 0,
                  'audio_level_messages': self.level_messages,
                  'audio_status': self.audio_status.get_text(),
                  'sound_messages': self.sound_messages,
                  'stream_warnings': self.stream_warnings,
                  'video_errors': self.video_errors, 'audio_errors': self.audio_errors}
        print(json.dumps(report), flush=True)
        self.smoke_ok = report['rendered'] > 0 and self.level_messages > 0 and not (self.video_errors or self.audio_errors)
        self.window.close()
        return False

    def terminate(self):
        self.window.close()
        return False

    def close(self, *_):
        self.closed = True
        if not self.args.smoke_test:
            self.settings.update(monitor=self.monitor.get_active(), volume=self.volume.get_value(),
                                 output=self.outputs[self.output.get_selected()])
            if self.modes and self.mode.get_selected() < len(self.modes):
                self.settings['mode'] = list(self.modes[self.mode.get_selected()])
            if self.device:
                self.settings['serial'] = self.device['serial']
            try:
                CONFIG.parent.mkdir(parents=True, exist_ok=True)
                CONFIG.write_text(json.dumps(self.settings, indent=2) + '\n')
            except OSError:
                LOG.exception('Could not save preferences')
        self.stop_video()
        self.stop_audio()
        self.worker.shutdown(wait=False, cancel_futures=True)
        return False


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--diagnose', action='store_true', help='Print detected devices without opening a window')
    parser.add_argument('--smoke-test', type=int, metavar='SECONDS', help='Open live preview briefly and report video/audio activity')
    parser.add_argument('--capture-format', choices=['NV12', 'YUYV', 'MJPG'], help='Override the saved capture format for a comparison')
    parser.add_argument('--diagnostic-frames', metavar='DIRECTORY', help='Save a rolling set of 24 preview input frames for motion diagnostics')
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
    log_path = Path.home() / '.local/state/linkview/linkview.log'
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(log_path, maxBytes=1_000_000, backupCount=2)
    handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s: %(message)s'))
    LOG.addHandler(handler)
    if args.diagnose:
        print(json.dumps(discover(), indent=2))
        return 0
    app = LinkView(args)
    result = app.run([sys.argv[0]])
    return (0 if getattr(app, 'smoke_ok', False) else 1) if args.smoke_test else result


if __name__ == '__main__':
    sys.exit(main())
