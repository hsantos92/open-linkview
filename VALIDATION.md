# Host validation — 2026-10-10

- Python compilation passed for the app and integration check.
- Desktop launcher validation passed.
- Device discovery paired `/dev/video4` to the Cam Link audio source by USB serial.
- 120-frame NV12 1080p60 capture to a discard sink passed.
- Five-second audio captures through both PipeWire and direct ALSA contained digital silence.
- First GTK launch failed in the Vulkan renderer with `VK_ERROR_OUT_OF_DEVICE_MEMORY`. Setting the application's default renderer to OpenGL resolved this launch failure.
- Real GTK window, 12-second smoke test: 691 rendered frames, 0 reported dropped frames, 30 audio-level messages, 0 video errors, 0 audio errors.
- Real GTK control integration test passed: mute, volume, fullscreen, NV12/YUYV mode switching, pause/start. Final capture after restart: 474 rendered frames, 0 reported dropped frames, 91 total audio-level messages, 0 video errors, 0 audio errors.

The render statistics demonstrate frame delivery to the window's video sink. They do not measure end-to-end gaming latency, visually verify image quality, or prove audible sound. The HDMI audio remained silent during these tests. Physical unplug/replug recovery has not been tested.

## Gameplay follow-up

The user confirmed audible game audio, but reported intermittent skipping and motion/transition artifacts. Initial silence was a snapshot of the source at that time, not proof of a hardware audio failure.

- PipeWire-Pulse logs showed two playback underruns from the original LinkView instance. Its requested capture/playback buffers were 40 ms. Playback was routed through Easy Effects.
- Raw diagnostic captures using FFmpeg produced six PNG frames each from NV12 and YUYV. The inspected NV12/YUYV frames looked clean, but those frames did not establish behavior throughout movement or screen transitions.
- Revised rendering uses mmap capture, conversion to independently allocated BGRA buffers, and clocked presentation. This is a rendering mitigation, not a confirmed root-cause diagnosis of the artifacts.
- Revised audio requests 120 ms capture/playback buffers, adds a bounded non-leaky queue, and resamples playback against the capture clock rather than correcting drift by skew.
- Updated 30-second live test: 1764 rendered frames, 0 reported dropped frames, 297 audio-level messages, all 297 containing non-silent signal, 0 stream warnings, 0 video errors, 0 audio errors.
- No new PipeWire-Pulse journal entries in the two-minute window checked after that test.
- After relaunch, eight live PipeWire samples showed both LinkView streams at 0 errors. The existing hardware-input, hardware-output and Easy Effects counters remained unchanged during that sample.

User confirmation of motion/transition image quality and audible playback remains necessary. These counters alone cannot prove either symptom is resolved.

## User-confirmed results

- The user confirmed the revised sound is perfect.
- Horizontal splits/comb-like lines remained during movement with NV12 despite RGB conversion and clocked presentation.
- Host V4L2 settings reported progressive 1080p60 (`Field: None`), with no recent kernel USB capture errors.
- Switched only the capture format to YUYV at the same 1080p60 settings and kept the working audio and video presentation settings. A bounded PNG diagnostic branch sampled the frames before display.
- The user moved the character/rotated the camera and confirmed: "The lines are gone". An inspected moving-character YUYV frame also showed no obvious horizontal splits.
- YUYV is now the preferred format at equal resolution/frame rate, and the selected mode is saved on close. NV12 remains selectable.

This comparison identifies YUYV as the working capture format for this setup. It does not establish whether the NV12 issue originates in device firmware, the driver, or a downstream format-handling component. No deinterlacing was applied.
