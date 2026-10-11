# Open LinkView

A native Linux viewer for an **Elgato Cam Link**, built and tested with a docked **Nintendo Switch**. The desktop application is called **LinkView**.

## Features

- Automatic discovery and pairing of Cam Link video/audio by USB serial.
- Live preview with fullscreen (**F11** or **F**; **Escape** to leave).
- Stereo audio monitoring with output selection, volume, mute and an input meter.
- Capture-mode selection from the formats actually reported by the device.
- YUYV preferred over NV12 at the same resolution/frame rate: this removed horizontal motion artifacts in the tested Switch/Cam Link setup.
- Clocked video presentation and RGB conversion before GTK rendering.
- Buffered audio with playback resampling to accommodate capture/output clock drift.
- Reconnect controls, automatic retry after capture errors, and saved preferences.

This version is a viewer. Recording and virtual camera output are not implemented.

## Requirements

- Linux with GTK 4, Python/PyGObject and GStreamer.
- GStreamer base/good plugins and **gst-plugin-gtk4** (`gtk4paintablesink`).
- `v4l2-ctl` from v4l-utils and `pactl` from libpulse.
- A running PipeWire-Pulse or PulseAudio service.
- Normal user access to the video device and audio service.
- A Cam Link on a USB 3 port with a compatible unencrypted HDMI source.

### Arch Linux

```bash
sudo pacman -S --needed python python-gobject gtk4 gstreamer gst-plugins-base gst-plugins-good gst-plugin-gtk4 v4l-utils libpulse
git clone https://github.com/hsantos92/open-linkview.git
cd open-linkview
python linkview.py
```

Other distributions need equivalent packages, particularly the GTK 4 GStreamer sink. Distribution package availability varies; the app has been tested on Arch Linux.

## Desktop launcher

From your checkout:

```bash
python install_desktop.py
```

This installs a user launcher at `~/.local/share/applications/linkview.desktop`, pointing to the current checkout. Keep the checkout in place, or rerun the installer after moving it. Open **LinkView** from the application menu.

To generate a launcher for inspection without installing it:

```bash
python install_desktop.py --output /tmp/linkview.desktop
desktop-file-validate /tmp/linkview.desktop
```

## Switch setup

Wake the docked Switch, connect its HDMI output to the Cam Link, and close other video-capture applications. Set **System Settings → TV Settings → TV Sound → Stereo**. Disconnect headphones or Bluetooth audio if they divert sound from HDMI.

The original Switch outputs up to 1080p60 in TV mode. Start with **1920 × 1080 · 60 fps · YUYV**. Reconnect after changing the console's TV resolution so the app refreshes the reported modes.

## Settings and troubleshooting

Preferences are stored in `~/.config/linkview/settings.json`. Stream warnings/errors are logged to `~/.local/state/linkview/linkview.log`.

The audio meter measures the input before the app's volume/mute controls. A silent meter can indicate missing HDMI audio; an active meter with no audible sound points toward monitoring or output routing. The application cannot generate audio the source does not send.

The app defaults to GTK's OpenGL renderer after a Vulkan allocation failure on the tested NVIDIA desktop. An explicit `GSK_RENDERER` environment setting takes precedence.

Audio requests 120 ms capture/playback buffers and uses a bounded queue. End-to-end latency and audio/video synchronization depend on the source, device, display and audio output; they have not been measured. See [VALIDATION.md](VALIDATION.md) for host test results and the user-confirmed audio/video fixes.

## Diagnostics

```bash
python linkview.py --diagnose
python linkview.py --smoke-test 12
python verify_controls.py
```

Device discovery prints metadata without opening a window. The smoke test opens live preview, reports rendered frames/audio-meter activity and errors, then exits. It does not save media. The control check additionally exercises mute, volume, fullscreen, alternate capture format and pause/start. Close an existing LinkView instance before running hardware checks.

To compare capture formats:

```bash
python linkview.py --capture-format YUYV
python linkview.py --capture-format NV12
```

The selected mode is saved on close. YUYV is the confirmed working choice for the tested setup; the exact origin of its NV12 artifacts remains undetermined.

Explicitly enabling diagnostic frames saves a rolling set of 24 PNG frames at up to four frames per second:

```bash
python linkview.py --diagnostic-frames /tmp/linkview-frames
```

Normal launches do not save game frames. Compilation and desktop-launcher validation run in GitHub Actions; hardware behavior requires a connected Cam Link and desktop session.

## License

MIT. This is an independent project and is not affiliated with Elgato or Nintendo.

## References

- [Nintendo Switch hardware specifications](https://www.nintendo.com/en-gb/Hardware/Nintendo-Switch-Family/Nintendo-Switch/Nintendo-Switch-1148779.html)
- [Nintendo Switch TV settings](https://en-americas-support.nintendo.com/app/answers/detail/a_id/22331)
- [GStreamer GTK 4 video sink](https://gstreamer.freedesktop.org/documentation/gtk4/index.html)
