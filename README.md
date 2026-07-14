# RemoteMicrophone

Simple low-latency microphone streaming over UDP for Windows.

## Requirements

-   Python 3.10+
-   Virtual Audio Cable, such as VAC Lite or VB-AudioCable
-   `pip install sounddevice numpy`

## Usage

### Receiver (Remote PC)

1.  Run `rm_rec_v1.0.0.py`.
2.  Select **CABLE Input** as the output device.
3.  Copy the displayed IP address.
4.  Click **Start Listening**.

### Sender (Local PC)

1.  Run `rm_sen_v1.0.0.py`.
2.  Select your microphone.
3.  Enter the receiver IP address.
4.  Click **Start Streaming**.

On the receiving PC, select **CABLE Output** as your microphone in your desired application (Discord, game, etc).

## Build

Example with PyInstaller:

``` bash
pip install pyinstaller
pyinstaller --onefile --windowed rm_rec_v1.0.0.py
pyinstaller --onefile --windowed rm_sen_v1.0.0.py
```
