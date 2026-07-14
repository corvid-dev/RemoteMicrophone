# RemoteMicrophone

Simple low-latency microphone streaming over UDP for Windows.

## Requirements

-   Python 3.10+
-   VB-CABLE
-   `pip install sounddevice numpy`

## Usage

### Receiver (Remote PC)

1.  Run `rm_rec_v0.2.0.py`.
2.  Select **CABLE Input** as the output device.
3.  Copy the displayed IP address.
4.  Click **Start Listening**.

### Sender (Local PC)

1.  Run `rm_sen_v0.2.0.py`.
2.  Select your microphone.
3.  Enter the receiver IP address.
4.  Click **Start Streaming**.

Select **CABLE Output** as your microphone in applications such as Discord or games on the Remote PC.

## Build

Example with PyInstaller:

``` bash
pip install pyinstaller
pyinstaller --onefile --windowed rm_rec_v0.2.0.py
pyinstaller --onefile --windowed rm_sen_v0.2.0.py
```
