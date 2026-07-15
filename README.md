# RemoteMicrophone

A lightweight, low-latency microphone relay for Windows over a local network.

It is well suited for remote desktop and game streaming environments such as Moonlight, Sunshine, Steam Remote Play, and similar multi-PC setups where a local microphone needs to be available on another computer.

## Features

- Low-latency PCM audio over UDP
- Simple sender and receiver applications
- Automatic buffering and clock drift correction
- Configurable microphone gain
- Minimal dependencies

## Requirements

- Windows
- Python 3.10+
- `sounddevice`
- `numpy`
- A compatible output device (VB-CABLE or VAC-Lite recommended)
- Sender and receiver should use matching versions.

Install dependencies:

```bash
pip install sounddevice numpy
```

## Files

- `RemoteMicrophone-Sender.py` - Captures microphone audio on the **Local PC**.
- `RemoteMicrophone-Receiver.py` - Receives and plays audio on the **Remote PC**.

## Building

Install PyInstaller:

```bash
pip install pyinstaller
```

Build the sender:

```bash
pyinstaller --onefile --windowed RemoteMicrophone-Sender.py
```

Build the receiver:

```bash
pyinstaller --onefile --windowed RemoteMicrophone-Receiver.py
```

The executables will be created in the `dist` folder.

## Usage

1. **Start Receiving** on the Remote PC.
2. Note the displayed Remote PC IP address.
3. Start the sender on the Local PC.
4. Enter the Remote PC IP address.
5. Select a microphone.
6. **Start Streaming** on the Local PC.

## Suggested Applications

- Remote gaming / remote desktop
- Presentation and conference
- Multi-PC audio routing

## License

MIT