"""Optional integration check: opens the real app and exercises its controls."""
import argparse
import sys
from gi.repository import GLib
from linkview import LinkView

app = LinkView(argparse.Namespace(smoke_test=18, diagnose=False))


def exercise():
    if not app.device or not app.video or not app.audio:
        print('Capture did not start before the control check', flush=True)
        app.video_errors += 1
        return False
    app.monitor.set_active(False)
    assert app.volume_element.get_property('mute')
    app.volume.set_value(42)
    assert abs(app.volume_element.get_property('volume') - 0.42) < 0.001
    app.monitor.set_active(True)
    assert not app.volume_element.get_property('mute')
    app.toggle_fullscreen()
    assert app.fullscreen and not app.controls.get_visible()
    app.toggle_fullscreen()
    assert not app.fullscreen and app.controls.get_visible()
    if len(app.modes) > 1:
        app.mode.set_selected(1)
    print('Mute, volume, fullscreen and alternate capture mode exercised', flush=True)
    return False


def restart():
    app.toggle_play()
    assert app.video is None and app.audio is None
    app.toggle_play()
    print('Pause/start exercised', flush=True)
    return False


def checked(callback):
    def run():
        try:
            return callback()
        except Exception as error:
            # GLib otherwise prints callback exceptions without failing the test.
            app.video_errors += 1
            print(f'Control check failed: {error}', file=sys.stderr, flush=True)
            return False
    return run


GLib.timeout_add_seconds(5, checked(exercise))
GLib.timeout_add_seconds(10, checked(restart))
app.run([sys.argv[0]])
sys.exit(0 if getattr(app, 'smoke_ok', False) else 1)
